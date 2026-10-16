from __future__ import annotations

import json
from urllib.parse import urljoin, urlsplit

import feedparser
from bs4 import BeautifulSoup

from .core import canonical_url, parse_date, plain
from .network import request


def hosts_for(source: dict) -> set[str]:
    return {urlsplit(source[k]).hostname for k in ("homepage", "endpoint")}


def parse_rss(content: bytes, source: dict) -> list[dict]:
    feed = feedparser.parse(content)
    if not feed.version:
        raise ValueError("Expected RSS or Atom")
    items = []
    for entry in feed.entries:
        url = entry.get("link")
        if not url or not entry.get("title"):
            continue
        try:
            url = canonical_url(url)
        except ValueError:
            continue
        body = " ".join(c.get("value", "") for c in entry.get("content", [])) or entry.get("summary", "")
        items.append({"url": url, "title": plain(entry.title), "published_at": parse_date(
            entry.get("published") or entry.get("updated"), source["timezone"]), "body": plain(body)})
    return items


def parse_anthropic(content: bytes, source: dict) -> list[dict]:
    soup = BeautifulSoup(content, "html.parser")
    items = []
    seen = set()
    for a in soup.select('a[href^="/news/"]'):
        time = a.find("time")
        title = a.select_one('[class*="__title"]') or a.find(["h2", "h3"])
        if not time or not title:
            continue
        url = canonical_url(urljoin(source["homepage"], a["href"]))
        if url in seen:
            continue
        seen.add(url)
        items.append({"url": url, "title": title.get_text(" ", strip=True), "published_at": parse_date(
            time.get("datetime") or time.get_text(strip=True)), "published_precision": "day", "body": ""})
    if not items:
        raise ValueError("Anthropic listing structure changed")
    return items


def collect_source(source: dict) -> tuple[list[dict], str]:
    hosts = hosts_for(source)
    raw = request(source["endpoint"], hosts)
    if source["adapter"] == "rss":
        return parse_rss(raw, source), ""
    if source["adapter"] == "anthropic":
        return parse_anthropic(raw, source), ""
    if source["adapter"] == "hn":
        ids = json.loads(raw)
        items = []
        # Sequential requests are deliberately gentle on a 1 GB VM and the API.
        for item_id in ids[:60]:
            entry = json.loads(request(f"https://hacker-news.firebaseio.com/v0/item/{int(item_id)}.json", hosts))
            if not entry or entry.get("type") != "story" or not entry.get("url") or entry.get("dead") or entry.get("deleted"):
                continue
            try:
                url = canonical_url(entry["url"])
            except ValueError:
                continue
            items.append({"url": url, "title": plain(entry.get("title", "")), "published_at": parse_date(entry.get("time")),
                          "body": "", "discussion_url": f"https://news.ycombinator.com/item?id={item_id}"})
        return items, "최근 60개 제출만 확인합니다. 6시간 전체를 포괄하지 않는 발견용 소스입니다."
    raise ValueError("Unknown adapter")


def extract_body(content: bytes) -> str:
    soup = BeautifulSoup(content, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
            if '"isAccessibleForFree": false' in json.dumps(data) or '"isAccessibleForFree": "false"' in json.dumps(data):
                return ""
        except (ValueError, TypeError):
            pass
    # Do not use metadata or hidden app payloads to extract subscription content.
    for el in soup.select("script,style,nav,footer,aside,form,[hidden],.advertisement"):
        el.decompose()
    body = None
    for selector in ("#article-view-content-div", ".entry-content", "[itemprop=articleBody]"):
        body = soup.select_one(selector)
        if body is not None:
            break
    if body is None:
        body = max(soup.select("article"), key=lambda el: len(el.get_text()), default=None)
    if body is None:
        body = soup.select_one("main")
    return plain(str(body))[:18000] if body else ""
