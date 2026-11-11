from __future__ import annotations

import gzip
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urlsplit

USER_AGENT = "AIBriefBot/1.0 (+news-curatiing; feed-based news curation)"
MAX_BYTES = 5_000_000


class RequestError(RuntimeError):
    def __init__(self, reason: str, *, retry_after_seconds: int | None = None):
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds
        super().__init__(reason)


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> int | None:
    """Retain only a safe duration from Retry-After, never other headers/body."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        if value.isascii() and value.isdecimal():
            seconds = int(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = max(0, math.ceil((date - (now or datetime.now(timezone.utc))).total_seconds()))
        # Reject malformed/unrepresentable durations before date arithmetic.
        return seconds if 0 <= seconds <= 2**31 - 1 else None
    except (ValueError, TypeError, OverflowError):
        return None


class AllowedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts: set[str]):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ensure_allowed(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def ensure_allowed(url: str, hosts: set[str]) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in hosts or parts.port not in (None, 443) or parts.username or parts.password:
        raise ValueError("Request outside source HTTPS allowlist")


def request(url: str, hosts: set[str], *, payload: bytes | None = None, headers: dict | None = None, timeout: int = 30) -> bytes:
    ensure_allowed(url, hosts)
    opener = urllib.request.build_opener(AllowedRedirect(hosts))
    req_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **(headers or {})}
    # No transport-level POST retries: an ambiguous timeout may have been billed.
    # The briefing caller retries 502/503/504 and defers 429 to later recovery.
    for attempt in range(1 if payload is not None else 2):
        retry_after = None
        try:
            req = urllib.request.Request(url, data=payload, headers=req_headers)
            with opener.open(req, timeout=timeout) as response:
                reader = gzip.GzipFile(fileobj=response) if response.headers.get("Content-Encoding") == "gzip" else response
                content = reader.read(MAX_BYTES + 1)
                if len(content) > MAX_BYTES:
                    raise ValueError("Response exceeds 5 MB limit")
                return content
        except urllib.error.HTTPError as exc:
            retry_after = parse_retry_after(exc.headers.get('Retry-After') if exc.headers else None)
            # GET retries must also respect a server's requested wait. Leave a
            # long wait to the next scheduled collection instead of blocking it.
            if payload is not None or exc.code not in (429, 500, 502, 503, 504) or attempt or (retry_after or 0) > 60:
                raise RequestError(f"http_{exc.code}", retry_after_seconds=retry_after) from None
        except (urllib.error.URLError, TimeoutError):
            if payload is not None or attempt:
                raise RequestError("network_failed_or_timed_out") from None
        time.sleep(max(1, retry_after or 0))
    raise RuntimeError("Request failed")


class Robots:
    def __init__(self):
        self.cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allows(self, url: str, hosts: set[str]) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.cache:
            try:
                text = request(origin + "/robots.txt", hosts).decode("utf-8", errors="replace")
                if "<html" in text[:1000].lower() or "user-agent" not in text.lower():
                    self.cache[origin] = None
                else:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(text.splitlines())
                    self.cache[origin] = parser
            except (ValueError, RuntimeError):
                self.cache[origin] = None
        policy = self.cache[origin]
        # Ambiguous/unavailable rules: keep RSS links, skip article-body crawling.
        if policy is None:
            return False
        # Honor common explicit AI-crawler exclusions as a conservative default.
        return all(policy.can_fetch(agent, url) for agent in (USER_AGENT, "GPTBot", "ClaudeBot"))
