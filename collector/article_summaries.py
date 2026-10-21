"""Bounded article summaries from permitted evidence, independent of daily editions."""
from collections import Counter
from datetime import datetime, timedelta
import json
import re

from .adapters import extract_body, hosts_for
from .core import article_id, iso_now
from .llm import SummaryError
from .network import RequestError, Robots, request
from .storage import atomic_json


def is_english(text):
    """Conservative prose heuristic, not a region or product-name classification."""
    sample = text[:4000]
    letters = [c for c in sample if c.isalpha()]
    latin = sum(c.isascii() for c in letters)
    words = re.findall(r"[a-z]+", sample.lower())
    common = {"a", "an", "the", "and", "or", "of", "to", "in", "for", "with", "is", "are",
              "was", "were", "has", "have", "that", "this", "from", "will", "we", "our",
              "you", "its", "can", "by", "on", "as", "it", "about"}
    return bool(letters and latin / len(letters) >= 0.85 and len(words) >= 30
                and sum(word in common for word in words) >= 3)


def collect_summaries(articles, evidence, *, sources, mode, cap, cutoff, now, runtime, key, model, generate, report):
    attempts_path = runtime / "summary-attempts.json"
    attempts = json.loads(attempts_path.read_text()) if attempts_path.exists() else {}
    stats = {"mode": mode, "limit": cap, "generated": 0, "remaining_candidates": 0, "skipped": {}}
    report["article_summaries"] = stats
    skipped = Counter()
    robots, body_fetches = Robots(), 0
    source_map = {s["id"]: s for s in sources if s["enabled"] and s["summarize"]}
    # English-looking titles get body-fetch priority; the actual body decides eligibility.
    def order(article):
        letters = [c for c in article["original_title"] if c.isalpha()]
        english_hint = bool(letters and sum(c.isascii() for c in letters) / len(letters) >= 0.85)
        return (mode == "english" and not english_hint, article["collected_at"], article["published_at"], article["id"])
    # Oldest collected articles first within that priority, to drain the recent backlog.
    queue = sorted((a for a in articles if datetime.fromisoformat(a["published_at"]) >= cutoff),
                   key=order)
    for position, article in enumerate(queue):
        if article["status"] != "link":
            skipped["already_summarized"] += 1
            continue
        if report["llm_requests"] >= cap:
            stats["remaining_candidates"] = sum(a["status"] == "link" for a in queue[position:])
            break
        last_attempt = attempts.get(article["id"])
        if last_attempt and now - datetime.fromisoformat(last_attempt) < timedelta(hours=24):
            skipped["cooldown"] += 1
            continue
        # A discovered URL can be merged into an older ID. Use its allowed source's body and URL.
        choices = []
        for ref in article["sources"]:
            pair = evidence.get(article_id(ref["url"]))
            if pair and pair[1]["summarize"] and pair[1]["id"] == ref["source_id"]:
                choices.append((*pair, ref))
            elif ref["source_id"] in source_map and source_map[ref["source_id"]]["fetch_body"]:
                # Pending articles can fall out of an RSS feed between collection runs.
                choices.append(("", source_map[ref["source_id"]], ref))
        if not choices:
            skipped["no_permitted_evidence"] += 1
            continue
        body, source, ref = max(choices, key=lambda pair: len(pair[0]))
        if mode == "english" and len(re.findall(r"[가-힣]", body[:4000])) >= 20 and not is_english(body):
            skipped["not_english"] += 1
            continue
        try:
            if len(body) < 600 and source["fetch_body"] and body_fetches < 24:
                body_fetches += 1
                if robots.allows(ref["url"], hosts_for(source)):
                    body = extract_body(request(ref["url"], hosts_for(source)))
        except Exception:
            skipped["body_unavailable"] += 1
            continue
        if len(body) < 600:
            skipped["insufficient_text"] += 1
            continue
        if mode == "english" and not is_english(body):
            skipped["not_english"] += 1
            continue
        # Persist BEFORE the paid POST. Failure/timeout must not trigger an automatic retry.
        attempts[article["id"]] = iso_now()
        atomic_json(attempts_path, attempts)
        report["llm_requests"] += 1
        try:
            summary, meta = generate({**article, "url": ref["url"], "original_title": ref["title"]}, body, key, model)
            article.update({k: summary[k] for k in ("title", "summary", "why_it_matters", "category")})
            article.update(status="summarized", summary_meta=meta)
            article["sources"].sort(key=lambda r: r["source_id"] != source["id"])
            stats["generated"] += 1
        except Exception as exc:
            report["summary_failures"] += 1
            reason = exc.reason if isinstance(exc, (SummaryError, RequestError)) else type(exc).__name__
            report["summary_errors"].append({"article_id": article["id"], "reason": reason})
            print(f"[summary] {article['id']}: kept as link ({reason})", flush=True)
    stats["skipped"] = dict(skipped)
    stats["body_fetches"] = body_fetches
    print("[article-summaries] " + json.dumps(stats, ensure_ascii=False), flush=True)
