from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from datetime import datetime, timedelta

from .adapters import collect_source
from .core import UTC, article_id, category_for, is_ai_relevant, iso_now, merge_candidates
from .llm import SummaryError, summarize
from .article_summaries import collect_summaries
from .network import RequestError
from . import briefings
from .storage import Archive, atomic_json, month_of

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "MAX_SUMMARIES_PER_RUN", "ENGLISH_SUMMARIES_PER_RUN", "BRIEFING_MAX_TOKENS", "SITE_URL"):
                os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def run(args, *, fetch_source=collect_source, generate=summarize) -> dict:
    now = datetime.now(UTC)
    sources = json.loads((ROOT / "config/sources.json").read_text())
    enabled = [s for s in sources if s["enabled"]]
    store = Archive(args.data_dir, args.runtime_dir)
    if store.legacy:
        raise RuntimeError('Run python -m collector migrate before collecting legacy data')
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
    changed_ids, revised_editions = store.editorial_changes(editorial)
    months = store.relevant_news_months(candidates, cutoff, now, changed_ids)
    previous_articles = [a for month in sorted(months) for a in store.read('news', month)]
    articles = merge_candidates(previous_articles, candidates)
    articles = [a for a in articles if a["id"] not in editorial["hidden_ids"]]
    key, model = os.getenv("OPENROUTER_API_KEY"), os.getenv("OPENROUTER_MODEL")
    master_cap = min(30, max(0, int(os.getenv("MAX_SUMMARIES_PER_RUN", "1"))))
    mode = "english" if getattr(args, "summarize_english", False) else "all" if getattr(args, "summarize_articles", False) else "none"
    cap = min(30, max(0, int(os.getenv("ENGLISH_SUMMARIES_PER_RUN", "10")))) if mode == "english" and master_cap else master_cap
    run_report["briefing"] = {"result": "disabled"}
    disabled = ("not_requested" if mode == "none" else "no_llm" if args.no_llm else
                "dry_run" if args.dry_run else "zero_budget" if cap == 0 else
                "missing_openrouter_settings" if not key or not model else None)
    if disabled:
        run_report["article_summaries"] = {"mode": mode, "limit": cap, "generated": 0, "disabled_reason": disabled}
        print(f"[summary] article summaries disabled: {disabled}", flush=True)
    else:
        collect_summaries(articles, evidence, sources=enabled, mode=mode, cap=cap, cutoff=cutoff, now=now,
                          runtime=runtime, key=key, model=model, generate=generate, report=run_report)
    articles = [a for a in articles if a["id"] not in editorial["hidden_ids"]]
    for article in articles:
        correction = editorial["corrections"].get(article["id"])
        if correction:
            # Corrections are visible editorial notes, not silent rewrites of source facts.
            article["correction"] = str(correction)[:1000]
        else:
            article.pop("correction", None)
    public_sources = [{k: s[k] for k in ("id", "name", "homepage", "region", "role", "enabled")} for s in sources]
    # Historical references remain resolvable when a source is removed from collection config.
    current_source_ids = {s['id'] for s in public_sources}
    public_sources.extend({**s, 'enabled': False} for s in store.sources if s['id'] not in current_source_ids)
    grouped = {month: [] for month in months}
    for article in articles:
        grouped.setdefault(month_of(article['published_at']), []).append(article)
    content_changed = sorted(articles, key=lambda a: a['id']) != sorted(previous_articles, key=lambda a: a['id']) or public_sources != store.sources
    changed = False
    if not args.dry_run:
        changed = store.save(news=grouped, editions=revised_editions, sources=public_sources,
                             updated_at=iso_now() if content_changed else None,
                             collection_slot=briefings.collection_slot(now).isoformat(timespec="seconds"), editorial=editorial)
    total = sum(entry['count'] for entry in store.catalog['news'].values()) if not args.dry_run else sum(entry['count'] for entry in store.catalog['news'].values()) - len(previous_articles) + len(articles)
    store.close()
    run_report.update({"finished_at": iso_now(), "result": "changed" if changed else "unchanged", "articles": total})
    atomic_json(runtime / "last-run.json", run_report)
    print(f"[done] {run_report['result']}; {total} articles; {run_report['llm_requests']} LLM requests", flush=True)
    return run_report


def run_daily(args, *, fetch_source=collect_source, generate_briefing=briefings.generate,
              review_coverage=briefings.review_coverage, now=None):
    """Publish one fixed 06:00-to-06:00 edition, scheduled for 06:30 KST."""
    now = now or datetime.now(UTC)
    requested_day = getattr(args, "briefing_date", None)
    retry = getattr(args, "retry_failed_briefing", False)
    if retry and not requested_day:
        raise ValueError("A retry requires an explicit --briefing-date")
    day, start, end, scheduled = briefings.daily_window(now, requested_day)
    budget = briefings.max_tokens()  # Reject invalid configuration before a paid attempt.
    store = Archive(args.data_dir, args.runtime_dir)
    if store.legacy:
        raise RuntimeError('Run python -m collector migrate before publishing legacy data')
    editorial = json.loads((ROOT / "config/editorial.json").read_text())
    month = day[:7]
    old = {'version': 1, 'editions': store.read('briefings', month)}
    changed_ids, revised_editions = store.editorial_changes(editorial)
    history = briefings.apply_editorial(old, editorial)
    report = {"started_at": now.isoformat(), "llm_requests": 0, "summary_failures": 0,
              "briefing": {"day": day, "result": "disabled", "retry_requested": retry},
              "llm": {"model": os.getenv("OPENROUTER_MODEL"), "max_tokens": budget}, "coverage_llm": {},
              "result": "unchanged"}
    runtime = Path(args.runtime_dir)
    updated_news = {}

    def finish(result, **details):
        report["briefing"].update(result=result, **details)
        report["finished_at"] = iso_now()
        briefings.validate_history(history)
        if not args.dry_run:
            if history != old: revised_editions[month] = history['editions']
            # Do not mark news corrections as processed in a briefing-only run.
            if (revised_editions or updated_news) and store.save(
                    news=updated_news, editions=revised_editions,
                    updated_at=iso_now() if updated_news else None):
                report["result"] = "changed"
        store.close()
        atomic_json(runtime / "last-run.json", report)
        print(f"[briefing] {day}: {result}; {report['llm_requests']} LLM requests", flush=True)
        diagnostic = json.dumps({k: report[k] for k in ('briefing', 'llm', 'coverage_llm')}, ensure_ascii=False)
        print("[briefing-diagnostics] " + diagnostic, flush=True)
        if result == "failed_previous_preserved" and os.getenv("GITHUB_ACTIONS") == "true":
            escaped = diagnostic.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print("::error title=Daily briefing failed::" + escaped, flush=True)
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
    if attempts.get(attempt_key) and not retry:
        return finish("cooldown")
    if attempts.get(attempt_key):
        report["briefing"]["previous_attempt_at"] = attempts[attempt_key]
    slot = store.catalog.get("collection_slot")
    if not slot or datetime.fromisoformat(slot) < end:
        # Shared workflow concurrency + the shell flock serialize this recovery with collection.
        recovery_args = argparse.Namespace(**vars(args))
        recovery_args.no_llm, recovery_args.summarize_articles, recovery_args.summarize_english = True, False, False
        store.close()
        run(recovery_args, fetch_source=fetch_source)
        store = Archive(args.data_dir, args.runtime_dir)
        # Collection may have applied corrections to old editions as well.
        old = {'version': 1, 'editions': store.read('briefings', month)}
        changed_ids, revised_editions = store.editorial_changes(editorial)
        history = briefings.apply_editorial(old, editorial)
        report["collection_recovered"] = True
        if not store.catalog.get("collection_slot") or datetime.fromisoformat(store.catalog["collection_slot"]) < end:
            raise RuntimeError("A successful morning collection is required before publishing")
    sources = json.loads((ROOT / "config/sources.json").read_text())
    months = {month_of(start), month_of(end - timedelta(microseconds=1))}
    news_by_month = {m: store.read('news', m) for m in sorted(months)}
    articles = [a for items in news_by_month.values() for a in items
                if a['id'] not in editorial['hidden_ids'] and start <= datetime.fromisoformat(a['published_at']) < end]
    previously_used = store.previously_briefed_ids(articles)
    articles = [a for a in articles if a['id'] not in previously_used]
    recent_coverage = store.recent_briefing_issues(day)
    report['briefing'].update(excluded_previous_articles=len(previously_used),
                              previous_issues_compared=len(recent_coverage))
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
    attempts[attempt_key] = iso_now()
    atomic_json(attempts_path, attempts)
    report["briefing"]["reviewed_articles"] = len(inputs)
    report['briefing']['excluded_repeated_stories'] = 0
    stage = "coverage_review"
    try:
        if recent_coverage:
            report['llm_requests'] += 1
            coverage = review_coverage(inputs, key, model, recent_coverage=recent_coverage,
                                       diagnostics=report['coverage_llm'])
            stage = 'coverage_validation'
            fresh = briefings.uncovered_inputs(coverage, inputs, recent_coverage)
            report['briefing']['excluded_repeated_stories'] = len(inputs) - len(fresh)
            inputs = fresh
            if not inputs:
                return finish('no_new_issues')
        digest = briefings.input_hash(inputs, model, recent_coverage)
        stage = 'generation'
        report['llm_requests'] += 1
        value = generate_briefing(inputs, key, model, diagnostics=report["llm"])
        if isinstance(value, dict):
            report["llm"]["output_counts"] = {
                field: len(value[field]) if isinstance(value.get(field), list) else None
                for field in ("issues", "overview", "article_summaries")}
        stage = "briefing_validation"
        edition = briefings.make_edition(value, inputs, articles, sources, end, model, digest)
        candidate = briefings.apply_editorial({"version": 1, "editions": [edition, *history["editions"]]}, editorial)
        briefings.validate_history(candidate)
        stage = "article_summary_validation"
        updates = briefings.article_summary_updates(value["article_summaries"], edition, inputs, articles)
    except Exception as exc:
        reason = exc.reason if isinstance(exc, (SummaryError, RequestError)) else type(exc).__name__
        if isinstance(exc, briefings.BriefingValidationError):
            reason = "validation_failed"
            report["briefing"]["validation_error"] = str(exc)
            if exc.details:
                report["briefing"]["validation_details"] = exc.details
        report["summary_failures"] = 1
        return finish("failed_previous_preserved", reason=reason, stage=stage)
    # Publish the edition and its article summaries in the same archive transaction.
    history = candidate
    updated_news = {m: [updates.get(a["id"], a) for a in items]
                    for m, items in news_by_month.items() if any(a["id"] in updates for a in items)}
    return finish("created", edition_id=edition["id"], issues=len(edition["issues"]),
                  articles_summarized=len(updates), selected_articles=len(edition["articles"]))


def main():
    parser = argparse.ArgumentParser(description="Collect AI news once; GitHub Actions or cron handles scheduling.")
    parser.add_argument("command", nargs="?", choices=("collect", "brief", "validate", "migrate", "recover"), default="collect")
    parser.add_argument("--no-llm", action="store_true")
    summary_mode = parser.add_mutually_exclusive_group()
    summary_mode.add_argument("--summarize-articles", action="store_true", help="Summarize permitted articles in any source language")
    summary_mode.add_argument("--summarize-english", action="store_true", help="Generate Korean summaries of permitted English articles during collection")
    parser.add_argument("--briefing-date", help="Publish a specific YYYY-MM-DD edition (KST); defaults to the latest due date")
    parser.add_argument("--retry-failed-briefing", action="store_true",
                        help="Allow one new briefing attempt (coverage review + generation); never replaces a published edition")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate, but do not call the LLM or change public data")
    parser.add_argument("--incremental", action="store_true", help="Validate changed archive shards using the disposable local index")
    parser.add_argument("--days", type=int, default=3, help="Lookback in days (initial bootstrap: 14)")
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--runtime-dir", default=str(ROOT / ".runtime"))
    args = parser.parse_args()
    if not 1 <= args.days <= 30:
        parser.error("--days must be between 1 and 30")
    if (args.briefing_date or args.retry_failed_briefing) and args.command != "brief":
        parser.error("Briefing date/retry options require the brief command")
    if (args.summarize_articles or args.summarize_english) and args.command != "collect":
        parser.error("Article summary options require the collect command")
    if args.retry_failed_briefing and not args.briefing_date:
        parser.error("--retry-failed-briefing requires --briefing-date")
    if args.briefing_date:
        try:
            briefings.daily_window(datetime.now(UTC), args.briefing_date)
        except ValueError as exc:
            parser.error(str(exc))
    load_env()
    if args.command == "validate":
        store = Archive(args.data_dir, args.runtime_dir).validate(incremental=args.incremental)
        store.close()
        print("Monthly archive validated")
    elif args.command == "migrate":
        changed = Archive(args.data_dir, args.runtime_dir).migrate()
        print("Migrated legacy data" if changed else "Already using monthly archives")
    elif args.command == "recover":
        Archive(args.data_dir, args.runtime_dir)
        print("Archive transaction recovery checked")
    elif args.command == "brief":
        result = run_daily(args)
        if result["briefing"]["result"] == "failed_previous_preserved":
            raise SystemExit(1)
    else:
        run(args)


if __name__ == "__main__":
    main()
