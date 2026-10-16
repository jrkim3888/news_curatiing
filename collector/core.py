from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

UTC = timezone.utc
CATEGORIES = ("모델·연구", "개발·도구", "산업·비즈니스", "정책·사회")
AI_PATTERN = re.compile(r"\b(llm|gpt\w*|claude|anthropic|openai|gemini|deepmind|hugging\s?face|nvidia|machine learning|neural|agentic|copilot|chatbot|diffusion|transformer|mistral|qwen|deepseek)\b|(?<![a-z])ai(?![a-z])|인공지능|생성형|언어모델|언어 모델|머신러닝|딥러닝|챗봇|챗GPT|에이전트|클로드|제미나이|엔비디아", re.I)


def iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def plain(html: str) -> str:
    if "<" not in (html or ""):
        return re.sub(r"\s+", " ", html_lib.unescape(html or "")).strip()
    soup = BeautifulSoup(html or "", "html.parser")
    for el in soup.select("script, style, noscript, nav, footer"):
        el.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def canonical_url(raw: str) -> str:
    parts = urlsplit(raw.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Invalid article URL")
    if parts.port and parts.port not in (80, 443):
        raise ValueError("Unsupported URL port")
    params = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                    if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid", "ref_src"))
    # Preserve meaningful IDs: idxno, id, p, etc. Trailing slash differences are equivalent here.
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(params), ""))


def article_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:20]


def parse_date(value: str | int | float | None, zone: str = "UTC") -> str | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(value, UTC)
        else:
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(value)
                except (ValueError, TypeError):
                    parsed = datetime.strptime(value.strip(), "%b %d, %Y")
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(zone))
        return parsed.astimezone(UTC).isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError):
        return None


def category_for(text: str) -> str:
    if re.search(r"규제|정책|안전|선거|저작권|법안|policy|safety|election|regulat|copyright", text, re.I):
        return "정책·사회"
    if re.search(r"투자|매출|인수|협약|파트너|상장|funding|acqui|revenue|partnership|billion", text, re.I):
        return "산업·비즈니스"
    if re.search(r"개발|코딩|도구|에이전트|api|sdk|coding|agent|tool|code|deploy", text, re.I):
        return "개발·도구"
    return "모델·연구"


def is_ai_relevant(title: str, body: str) -> bool:
    # Media feeds prepend image/video credits. An AI-generated illustration does
    # not make the underlying local jobs/event story AI news.
    body = re.sub(r"AI\s*생성\s*(?:영상|이미지|사진)|AI[- ]generated\s+(?:image|photo|video)", "", body, flags=re.I)
    return bool(AI_PATTERN.search(title + " " + body[:1000]))


def normalized_title(title: str) -> str:
    return re.sub(r"[^\w]", "", title.casefold())


def merge_candidates(existing: list[dict], candidates: list[dict]) -> list[dict]:
    """Only merge identical URLs or exact long titles within 72h. Never fuzzy-merge facts."""
    result = {a["id"]: json.loads(json.dumps(a)) for a in existing}
    url_index = {r["url"]: a["id"] for a in result.values() for r in a["sources"]}
    for item in candidates:
        key = item["id"]
        match = url_index.get(item["url"])
        title = normalized_title(item["original_title"])
        if not match and len(title) >= 24:
            for old in result.values():
                if title == normalized_title(old["original_title"]) and abs(
                    (datetime.fromisoformat(old["published_at"]) - datetime.fromisoformat(item["published_at"])).total_seconds()
                ) < 72 * 3600:
                    match = old["id"]
                    break
        if match:
            old = result[match]
            known = {(r["source_id"], r["url"]) for r in old["sources"]}
            for ref in item["sources"]:
                if (ref["source_id"], ref["url"]) not in known:
                    old["sources"].append(ref)
            url_index[item["url"]] = match
        else:
            result[key] = item
            url_index[item["url"]] = key
    return sorted(result.values(), key=lambda a: (a["published_at"], a["id"]), reverse=True)


def validate_summary(value: dict, evidence: str) -> dict:
    if not isinstance(value, dict) or set(value) != {"title", "summary", "why_it_matters", "category", "evidence"}:
        raise ValueError("Unexpected summary fields")
    for key, low, high in (("title", 5, 140), ("summary", 30, 600), ("why_it_matters", 15, 300)):
        v = value[key]
        if not isinstance(v, str) or not low <= len(v) <= high or not re.search(r"[가-힣]", v):
            raise ValueError("Invalid Korean summary")
        if "<" in v or ">" in v:
            raise ValueError("HTML is not allowed in summaries")
    if value["category"] not in CATEGORIES:
        raise ValueError("Unknown category")
    quotes = value["evidence"]
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= 3:
        raise ValueError("Missing source evidence")
    normalized = re.sub(r"\s+", " ", evidence)
    if any(not isinstance(q, str) or not 12 <= len(q) <= 240 or q not in normalized for q in quotes):
        raise ValueError("Evidence not found in source")
    return value


def validate_dataset(data: dict) -> None:
    assert data["version"] == 1
    assert data["updated_at"] is None or parse_date(data["updated_at"])
    assert len(data["articles"]) <= 5000, "5,000 article guard: plan archive pagination before increasing"
    source_ids = {s["id"] for s in data["sources"]}
    assert len(source_ids) == len(data["sources"])
    seen = set()
    for a in data["articles"]:
        assert re.fullmatch(r"[a-f0-9]{20}", a["id"]) and a["id"] not in seen
        seen.add(a["id"])
        assert canonical_url(a["url"])
        assert isinstance(a["title"], str) and 1 <= len(a["title"]) <= 500
        assert a["category"] in CATEGORIES
        assert a["region"] in ("kr", "global")
        assert a["status"] in ("link", "summarized")
        assert parse_date(a["published_at"]) and parse_date(a["collected_at"])
        assert a["sources"] and all(r["source_id"] in source_ids and canonical_url(r["url"]) for r in a["sources"])
        assert len(a.get("excerpt", "")) <= 180
        assert "body" not in a and "evidence" not in a
        if a["status"] == "summarized":
            assert 30 <= len(a["summary"]) <= 600 and 15 <= len(a["why_it_matters"]) <= 300
            assert a["summary_meta"]["model"] and a["summary_meta"]["prompt_version"]
            assert parse_date(a["summary_meta"]["generated_at"])
