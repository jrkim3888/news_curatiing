from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import tempfile
from datetime import datetime, timedelta

from .adapters import collect_source, extract_body, hosts_for
from .core import UTC, article_id, category_for, is_ai_relevant, iso_now, merge_candidates, validate_dataset
from .llm import SummaryError, summarize
from .network import RequestError, Robots, request
from . import briefings

ROOT = Path(__file__).resolve().parents[1]


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_env() -> None:
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "MAX_SUMMARIES_PER_RUN", "SITE_URL"):
                os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def run(args, *, fetch_source=collect_source, generate=summarize) -> dict:
    now = datetime.now(UTC)
    sources = json.loads((ROOT / "config/sources.json").read_text())
    enabled = [s for s in sources if s["enabled"]]
    data_path = Path(args.data_dir) / "news.json"
    previous = json.loads(data_path.read_text()) if data_path.exists() else {"version": 1, "updated_at": None, "articles": [], "sources": []}
    history_path = Path(args.data_dir) / "briefings.json"
    old_history = json.loads(history_path.read_text()) if history_path.exists() else {"version": 1, "editions": []}
    briefings.validate_history(old_history)
    editorial = json.loads((ROOT / "config/editorial.json").read_text())
    cutoff = now - timedelta(days=args.days)
    candidates, evidence, reports = [], {}, []
    for source in enabled:
        try:
            entries, note = fetch_source(source)
            eligible = []
            for entry in entries:
                published = entry.get("published_at")
                if not published:
                    continue
                date = datetime.fromisoformat(published)
                if date < cutoff or date > now + timedelta(minutes=10):
                    continue
                if not source["ai_only"] and not is_ai_relevant(entry["title"], entry["body"]):
                    continue
                eligible.append(entry)
            eligible.sort(key=lambda x: x["published_at"], reverse=True)
            for entry in eligible[:12]:
                aid = article_id(entry["url"])
                if aid in editorial["hidden_ids"]:
                    continue
                body = entry["body"]
                ref = {"source_id": source["id"], "url": entry["url"], "title": entry["title"]}
                if entry.get("discussion_url"):
                    ref["discussion_url"] = entry["discussion_url"]
                candidates.append({"id": aid, "url": entry["url"], "title": entry["title"], "original_title": entry["title"],
                                   "published_at": entry["published_at"], "collected_at": now.isoformat(timespec="seconds"),
                                   "published_precision": entry.get("published_precision", "time"),
                                   "region": source["region"], "category": category_for(entry["title"]), "status": "link",
                                   "excerpt": body[:177] + "…" if body else "", "sources": [ref]})
                old_evidence = evidence.get(aid)
                if not old_evidence or (source["summarize"], len(body)) > (old_evidence[1]["summarize"], len(old_evidence[0])):
                    evidence[aid] = (body, source)
            reports.append({"id": source["id"], "status": "ok", "items": len(eligible[:12]), "note": note})
            print(f"[source] {source['id']}: {len(eligible[:12])} eligible", flush=True)
        except Exception as exc:
            # Do not persist upstream response bodies, credentials, or tracebacks in public data.
            reports.append({"id": source["id"], "status": "error", "items": 0, "note": type(exc).__name__})
            print(f"[source] {source['id']}: failed ({type(exc).__name__})", flush=True)
    success = sum(r["status"] == "ok" for r in reports)
    run_report = {"started_at": now.isoformat(), "sources": reports, "llm_requests": 0, "summary_failures": 0, "summary_errors": []}
    runtime = Path(args.runtime_dir)
    if success < max(1, math.ceil(len(enabled) / 2)):
        run_report["result"] = "insufficient_sources_previous_data_preserved"
        atomic_json(runtime / "last-run.json", run_report)
        raise RuntimeError("Less than half the sources succeeded; previous dataset preserved")
    articles = merge_candidates(previous["articles"], candidates)
    articles = [a for a in articles if a["id"] not in editorial["hidden_ids"]]
    key, model = os.getenv("OPENROUTER_API_KEY"), os.getenv("OPENROUTER_MODEL")
    # Scheduled collection makes no paid calls. Article summaries are an explicit manual option.
    cap = min(30, max(0, int(os.getenv("MAX_SUMMARIES_PER_RUN", "1"))))
    attempts_path = runtime / "summary-attempts.json"
    attempts = json.loads(attempts_path.read_text()) if attempts_path.exists() else {}
    history = briefings.apply_editorial(old_history, editorial)
    run_report["briefing"] = {"result": "disabled"}
    robots = Robots()
    if getattr(args, "summarize_articles", False) and not args.no_llm and not args.dry_run and key and model:
        for article in articles:
            if run_report["llm_requests"] >= cap:
                break
            if article["status"] != "link" or article["id"] not in evidence:
                continue
            body, source = evidence[article["id"]]
            if not source["summarize"]:
                continue
            # Failed/uncertain paid requests get a 24h cooldown. Persist BEFORE sending.
            last_attempt = attempts.get(article["id"])
            if last_attempt and now - datetime.fromisoformat(last_attempt) < timedelta(hours=24):
                continue
            try:
                if len(body) < 600 and source["fetch_body"] and robots.allows(article["url"], hosts_for(source)):
                    body = extract_body(request(article["url"], hosts_for(source)))
                if len(body) < 600:
                    continue
                attempts[article["id"]] = iso_now()
                atomic_json(attempts_path, attempts)
                run_report["llm_requests"] += 1
                summary, meta = generate(article, body, key, model)
                article.update({k: summary[k] for k in ("title", "summary", "why_it_matters", "category")})
                article.update({"status": "summarized", "summary_meta": meta})
            except Exception as exc:
                run_report["summary_failures"] += 1
                reason = exc.reason if isinstance(exc, (SummaryError, RequestError)) else type(exc).__name__
                run_report["summary_errors"].append({"article_id": article["id"], "reason": reason})
                print(f"[summary] {article['id']}: kept as link ({reason})", flush=True)
    else:
        print("[summary] disabled or missing OpenRouter settings; keeping source links", flush=True)
    articles = [a for a in articles if a["id"] not in editorial["hidden_ids"]]
    for article in articles:
        correction = editorial["corrections"].get(article["id"])
        if correction:
            # Corrections are visible editorial notes, not silent rewrites of source facts.
            article["correction"] = str(correction)[:1000]
        else:
            article.pop("correction", None)
    public_sources = [{k: s[k] for k in ("id", "name", "homepage", "region", "role", "enabled")} for s in sources]
    changed = articles != previous["articles"] or public_sources != previous["sources"]
    data = {"version": 1, "updated_at": iso_now() if changed else previous["updated_at"], "articles": articles, "sources": public_sources}
    data["collection_slot"] = briefings.collection_slot(now).isoformat(timespec="seconds")
    changed = data != previous
    validate_dataset(data)
    briefings.validate_history(history)
    history_changed = history != old_history
    if changed and not args.dry_run:
        atomic_json(data_path, data)
    if history_changed and not args.dry_run:
        atomic_json(history_path, history)
    run_report.update({"finished_at": iso_now(), "result": "changed" if changed or history_changed else "unchanged", "articles": len(articles)})
    atomic_json(runtime / "last-run.json", run_report)
    print(f"[done] {run_report['result']}; {len(articles)} articles; {run_report['llm_requests']} LLM requests", flush=True)
    return run_report


def run_daily(args, *, fetch_source=collect_source, generate_briefing=briefings.generate, now=None):
    """Publish one fixed 06:00-to-06:00 edition, scheduled for 06:30 KST."""
    now = now or datetime.now(UTC)
    day, start, end, scheduled = briefings.daily_window(now)
    path = Path(args.data_dir) / "briefings.json"
    old = json.loads(path.read_text()) if path.exists() else {"version": 1, "editions": []}
    briefings.validate_history(old)
    editorial = json.loads((ROOT / "config/editorial.json").read_text())
    history = briefings.apply_editorial(old, editorial)
    report = {"started_at": now.isoformat(), "llm_requests": 0, "summary_failures": 0,
              "briefing": {"day": day, "result": "disabled"}, "result": "unchanged"}
    runtime = Path(args.runtime_dir)

    def finish(result, **details):
        report["briefing"].update(result=result, **details)
        report["finished_at"] = iso_now()
        briefings.validate_history(history)
        if history != old and not args.dry_run:
            atomic_json(path, history)
            report["result"] = "changed"
        atomic_json(runtime / "last-run.json", report)
        print(f"[briefing] {day}: {result}; {report['llm_requests']} LLM requests", flush=True)
        return report

    # A published/withdrawn date is never rewritten, even if new articles or a new model arrive.
    if any(e["day"] == day for e in history["editions"]):
        return finish("already_published")
    if args.dry_run:
        return finish("dry_run", window_start=start.isoformat(), window_end=end.isoformat(), scheduled_at=scheduled.isoformat())
    key, model = os.getenv("OPENROUTER_API_KEY"), os.getenv("OPENROUTER_MODEL")
    cap = max(0, int(os.getenv("MAX_SUMMARIES_PER_RUN", "1")))
    if args.no_llm or not key or not model or cap == 0:
        return finish("disabled")
    attempts_path = runtime / "summary-attempts.json"
    attempts = json.loads(attempts_path.read_text()) if attempts_path.exists() else {}
    attempt_key = f"daily:{day}"
    if attempts.get(attempt_key):
        return finish("cooldown")
    data_path = Path(args.data_dir) / "news.json"
    data = json.loads(data_path.read_text())
    slot = data.get("collection_slot")
    if not slot or datetime.fromisoformat(slot) < end:
        # Shared workflow concurrency + the shell flock serialize this recovery with collection.
        recovery_args = argparse.Namespace(**vars(args))
        recovery_args.no_llm, recovery_args.summarize_articles = True, False
        run(recovery_args, fetch_source=fetch_source)
        data = json.loads(data_path.read_text())
        report["collection_recovered"] = True
        if not data.get("collection_slot") or datetime.fromisoformat(data["collection_slot"]) < end:
            raise RuntimeError("A successful morning collection is required before publishing")
    sources = json.loads((ROOT / "config/sources.json").read_text())
    articles = [a for a in data["articles"] if a["id"] not in editorial["hidden_ids"] and start <= datetime.fromisoformat(a["published_at"]) < end]
    # Re-read permitted source text for analysis, without adding news to the completed window.
    needed = {r["source_id"] for a in articles for r in a["sources"]}
    evidence = {}
    for source in sources:
        if not source["enabled"] or not source["summarize"] or source["id"] not in needed:
            continue
        try:
            entries, _ = fetch_source(source)
            for entry in entries:
                aid = article_id(entry["url"])
                existing = evidence.get(aid)
                if not existing or len(entry["body"]) > len(existing[0]):
                    evidence[aid] = (entry["body"], source)
        except Exception:
            pass  # Direct body retrieval remains available if allowed by source policy.
        for a in articles:
            if any(ref["source_id"] == source["id"] for ref in a["sources"]):
                evidence.setdefault(a["id"], ("", source))
    inputs = briefings.prepare_inputs(articles, evidence, end)
    if len(inputs) < 2:
        return finish("insufficient_sources")
    digest = briefings.input_hash(inputs, model)
    attempts[attempt_key] = iso_now()
    atomic_json(attempts_path, attempts)
    report["llm_requests"] = 1
    try:
        value = generate_briefing(inputs, key, model)
        edition = briefings.make_edition(value, inputs, articles, sources, end, model, digest)
        candidate = briefings.apply_editorial({"version": 1, "editions": [edition, *history["editions"]]}, editorial)
        briefings.validate_history(candidate)
        history = candidate
    except Exception as exc:
        reason = exc.reason if isinstance(exc, (SummaryError, RequestError)) else type(exc).__name__
        report["summary_failures"] = 1
        return finish("failed_previous_preserved", reason=reason)
    return finish("created", edition_id=edition["id"], issues=len(edition["issues"]))


def main():
    parser = argparse.ArgumentParser(description="Collect AI news once; GitHub Actions or cron handles scheduling.")
    parser.add_argument("command", nargs="?", choices=("collect", "brief", "validate"), default="collect")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--summarize-articles", action="store_true", help="Opt in to individual article summaries during manual collection")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate, but do not call the LLM or change public data")
    parser.add_argument("--days", type=int, default=3, help="Lookback in days (initial bootstrap: 14)")
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--runtime-dir", default=str(ROOT / ".runtime"))
    args = parser.parse_args()
    if not 1 <= args.days <= 30:
        parser.error("--days must be between 1 and 30")
    load_env()
    if args.command == "validate":
        validate_dataset(json.loads((Path(args.data_dir) / "news.json").read_text()))
        briefings.validate_history(json.loads((Path(args.data_dir) / "briefings.json").read_text()))
        print("Dataset validated")
    elif args.command == "brief":
        result = run_daily(args)
        if result["briefing"]["result"] == "failed_previous_preserved":
            raise SystemExit(1)
    else:
        run(args)


if __name__ == "__main__":
    main()
