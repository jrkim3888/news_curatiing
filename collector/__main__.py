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
                evidence.setdefault(aid, (body, source))
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
    key, model = os.getenv("OPENROUTER_API_KEY"), os.getenv("OPENROUTER_MODEL")
    cap = min(30, max(0, int(os.getenv("MAX_SUMMARIES_PER_RUN", "8"))))
    attempts_path = runtime / "summary-attempts.json"
    attempts = json.loads(attempts_path.read_text()) if attempts_path.exists() else {}
    robots = Robots()
    if not args.no_llm and not args.dry_run and key and model:
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
    validate_dataset(data)
    if changed and not args.dry_run:
        atomic_json(data_path, data)
    run_report.update({"finished_at": iso_now(), "result": "changed" if changed else "unchanged", "articles": len(articles)})
    atomic_json(runtime / "last-run.json", run_report)
    print(f"[done] {run_report['result']}; {len(articles)} articles; {run_report['llm_requests']} LLM requests", flush=True)
    return run_report


def main():
    parser = argparse.ArgumentParser(description="Collect AI news once; GitHub Actions or cron handles scheduling.")
    parser.add_argument("command", nargs="?", choices=("collect", "validate"), default="collect")
    parser.add_argument("--no-llm", action="store_true")
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
        print("Dataset validated")
    else:
        run(args)


if __name__ == "__main__":
    main()
