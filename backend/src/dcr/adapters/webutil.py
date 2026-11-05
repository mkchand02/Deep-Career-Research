"""Compliance and parsing helpers for public-web adapters.

* :class:`RobotsGate` fetches and caches ``robots.txt`` per host and answers ``can_fetch`` for our honest
  User-Agent. Unreachable robots.txt (5xx/timeouts) means *disallow*; 4xx means allow (RFC 9309). It also
  enforces ``Crawl-delay`` and a minimum per-host delay.
* :data:`BLOCKED_DOMAINS` lists login-walled or anti-automation sites that are never fetched, whatever
  robots.txt says.
* HTML → text, JSON-LD ``Person`` extraction, publish-date detection, and a conservative "I just joined X"
  pattern parser.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from protego import Protego

BLOCKED_DOMAINS = (
    "linkedin.com",
    "lnkd.in",
    "facebook.com",
    "instagram.com",
    "glassdoor.com",
    "indeed.com",
    "twitter.com",
    "x.com",
    "threads.net",
    "tiktok.com",
    "rocketreach.co",
    "zoominfo.com",
    "apollo.io",
    "signalhire.com",
    "contactout.com",
    "lusha.com",
)


def blocked(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


@dataclass
class _HostState:
    parser: Protego | None
    allow_all: bool
    disallow_all: bool
    last_fetch: float = 0.0
    crawl_delay: float = 0.0


class RobotsGate:
    """Per-host robots.txt policy + politeness delay."""

    def __init__(self, ctx: Any, min_delay_s: float = 1.0) -> None:
        self.ctx = ctx
        self.min_delay = min_delay_s
        self.hosts: dict[str, _HostState] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    async def _state(self, url: str) -> _HostState:
        u = urlparse(url)
        host = f"{u.scheme}://{u.netloc}"
        if host in self.hosts:
            return self.hosts[host]
        lock = self.locks.setdefault(host, asyncio.Lock())
        async with lock:
            if host in self.hosts:
                return self.hosts[host]
            try:
                res = await self.ctx.get(urljoin(host, "/robots.txt"), ok_statuses=tuple(range(200, 600)))
            except Exception:
                st = _HostState(None, allow_all=False, disallow_all=True)
            else:
                if 200 <= res.status < 300:
                    parser = Protego.parse(res.text)
                    delay = parser.crawl_delay(self.ctx.user_agent) or 0.0
                    st = _HostState(parser, False, False, crawl_delay=float(delay))
                elif 400 <= res.status < 500:
                    st = _HostState(None, allow_all=True, disallow_all=False)
                else:
                    st = _HostState(None, allow_all=False, disallow_all=True)
            self.hosts[host] = st
            return st

    async def can_fetch(self, url: str) -> tuple[bool, str]:
        if blocked(url):
            return False, "domain is on the never-fetch list (login-walled / anti-automation)"
        if urlparse(url).scheme not in ("http", "https"):
            return False, "unsupported scheme"
        st = await self._state(url)
        if st.disallow_all:
            return False, "robots.txt unavailable (treated as disallow)"
        if st.allow_all:
            return True, "no robots.txt"
        assert st.parser is not None
        ok = st.parser.can_fetch(url, self.ctx.user_agent)
        return ok, "allowed by robots.txt" if ok else "disallowed by robots.txt"

    async def wait_turn(self, url: str) -> None:
        st = await self._state(url)
        delay = max(self.min_delay, st.crawl_delay)
        wait = st.last_fetch + delay - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        st.last_fetch = time.monotonic()


# ------------------------------------------------------------------ parsing
def html_to_text(html: str, max_chars: int = 40_000) -> tuple[str, bool]:
    """Visible text from HTML (scripts/nav/footer removed). Returns (text, truncated)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form", "svg"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n"))
    text = re.sub(r"[ \t]+", " ", text).strip()
    return (text[:max_chars], True) if len(text) > max_chars else (text, False)


def json_ld_people(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict) and it.get("@type") in ("Person", ["Person"]):
                out.append(it)
            elif isinstance(it, dict) and isinstance(it.get("author"), dict) and it["author"].get("@type") == "Person":
                out.append(it["author"])
    return out


def published_date(html: str) -> date | None:
    soup = BeautifulSoup(html, "lxml")
    metas: tuple[dict[str, Any], ...] = (
        {"property": "article:published_time"},
        {"name": "date"},
        {"itemprop": "datePublished"},
        {"name": "pubdate"},
    )
    for attrs in metas:
        m = soup.find("meta", attrs=attrs)
        if m and m.get("content"):
            d = _parse_date(str(m["content"]))
            if d:
                return d
    t = soup.find("time", attrs={"datetime": True})
    if t:
        return _parse_date(str(t["datetime"]))
    return None


def _parse_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(s.replace("Z", "+0000"), fmt).date()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


_JOINED = re.compile(
    r"\b(?:I(?:'ve| have)?|I am|I'm)?\s*(?:just |recently |officially |excited to (?:have )?)?"
    r"(?:joined|started (?:a new (?:role|position|job) )?at|starting at|started my new (?:role|job|position) at)\s+"
    r"(?P<company>[A-Z][\w&.\-]*(?: [A-Z][\w&.\-]*){0,3})"
    r"(?:\s+as\s+(?:an?\s+)?(?P<title>[A-Za-z][\w ,/&\-]{2,60}?))?(?=[.!,;\n]|\s+(?:on|in|team|where|to)\b|$)",
)
_TITLE_AT = re.compile(
    r"\b(?P<title>(?:Senior |Staff |Junior |Associate )?[A-Z][A-Za-z/&\- ]{2,50}(?:Engineer|Developer|Scientist|"
    r"Architect|Manager|SRE|SWE|SDE)(?: I{1,3}| [1-4]| L\d)?)\s+(?:at|@)\s+"
    r"(?P<company>[A-Z][\w&.\-]*(?: [A-Z][\w&.\-]*){0,2})"
)


def parse_joined_post(text: str) -> dict[str, str | None] | None:
    """Find 'I just joined X as Y' / 'Y at X' statements. Conservative: returns None if nothing clear."""
    m = _JOINED.search(text)
    if m:
        return {
            "company": m.group("company").strip(),
            "title": (m.group("title") or "").strip() or None,
            "kind": "joined",
        }
    m2 = _TITLE_AT.search(text)
    if m2:
        return {"company": m2.group("company").strip(), "title": m2.group("title").strip(), "kind": "title_at"}
    return None


def same_site_links(html: str, base_url: str, patterns: list[str], limit: int = 50) -> list[str]:
    """Same-host links whose path matches any regex in ``patterns``."""
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(base_url).netloc
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, str(a["href"])).split("#")[0]
        if urlparse(url).netloc != host or url in out:
            continue
        if any(re.search(p, urlparse(url).path) for p in patterns):
            out.append(url)
        if len(out) >= limit:
            break
    return out


def sitemap_urls(xml: str, patterns: list[str], limit: int = 200) -> list[str]:
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    return [u for u in urls if not patterns or any(re.search(p, u) for p in patterns)][:limit]
