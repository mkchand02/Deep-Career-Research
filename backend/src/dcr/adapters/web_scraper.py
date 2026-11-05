"""Polite public-web scraper (zero-key).

Fetches only public pages from user-supplied seed URLs, sitemaps and same-site links: personal sites,
team pages, engineering-blog author pages, conference speaker pages, "I just joined X" posts.

Compliance (not configurable):
* robots.txt is always obeyed for our honest User-Agent, and an unreachable robots.txt means *disallow*;
* login-walled or anti-automation domains (LinkedIn, Facebook, ...) are never fetched;
* no logins, cookies, CAPTCHA solving, proxy rotation or bot-detection evasion;
* rate limit per process plus a per-host delay (``Crawl-delay`` honoured, minimum 1 s);
* responses are cached (``cache_ttl_hours``) so re-runs don't re-hit sites.

Extraction: schema.org ``Person`` JSON-LD first, then (optionally) Claude structured extraction
(``params.use_llm``), then a conservative regex parser for "joined X as Y" / "Y at X" statements.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, ClassVar

from dcr.adapters.base import Candidate, SourceAdapter, SourceError
from dcr.adapters.registry import register_adapter
from dcr.adapters.webutil import (
    RobotsGate,
    blocked,
    html_to_text,
    json_ld_people,
    parse_joined_post,
    published_date,
    same_site_links,
    sitemap_urls,
)
from dcr.models import Education, Position, Project, RawProfile, StudyQuery


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in re.split(r"[\n,|]", v) if x.strip()]
    return [str(x) for x in v]


def _month(s: str | None) -> date | None:
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", s)
    if not m:
        m2 = re.match(r"(\d{4})$", s.strip())
        return date(int(m2[1]), 1, 1) if m2 else None
    return date(int(m[1]), int(m[2]), int(m[3] or 1))


@register_adapter
class WebScraperAdapter(SourceAdapter):
    """params: seeds, sitemaps, follow_patterns, sitemap_patterns, max_pages, use_llm."""

    name: ClassVar[str] = "web_scraper"
    description: ClassVar[str] = "Public pages from seed URLs/sitemaps; robots.txt-respecting"
    tos_note: ClassVar[str] = "robots.txt always obeyed; login-walled sites (e.g. LinkedIn) are never fetched."

    def __init__(self, ctx: Any, params: dict[str, Any] | None = None) -> None:
        super().__init__(ctx, params)
        self.robots = RobotsGate(ctx, min_delay_s=float(self.params.get("min_delay_s", 1.0)))
        self.skipped: list[dict[str, str]] = []
        self.llm_cost_usd = 0.0
        self._llm: Any = None
        if self.params.get("use_llm") and ctx.secrets.anthropic_api_key:
            from dcr.enrichment.llm import ClaudeClient

            self._llm = ClaudeClient(ctx.app_config.enrichment, api_key=ctx.secrets.anthropic_api_key)

    @property
    def max_pages(self) -> int:
        return int(self.params.get("max_pages", self.ctx.config.options.get("max_pages_per_domain", 25)))

    async def _allowed(self, url: str) -> bool:
        ok, reason = await self.robots.can_fetch(url)
        if not ok:
            self.skipped.append({"url_host": url.split("/")[2] if "//" in url else url, "reason": reason})
        return ok

    async def _get_html(self, url: str) -> str:
        await self.robots.wait_turn(url)
        res = await self.ctx.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
        ctype = res.headers.get("content-type", res.headers.get("Content-Type", ""))
        if ctype and "html" not in ctype and "xml" not in ctype:
            raise SourceError(f"{self.name}: non-HTML content at {url[:80]}")
        return res.text

    async def search(self, query: StudyQuery, limit: int) -> list[Candidate]:
        seeds = _as_list(self.params.get("seeds"))
        if not seeds and not self.params.get("sitemaps"):
            raise SourceError("web_scraper needs params.seeds (URLs) or params.sitemaps")
        urls: list[str] = []
        for sm in _as_list(self.params.get("sitemaps")):
            if await self._allowed(sm):
                try:
                    xml = (await self.ctx.get(sm)).text
                    urls += sitemap_urls(xml, _as_list(self.params.get("sitemap_patterns")), self.max_pages)
                except SourceError as exc:
                    self.skipped.append({"url_host": sm.split("/")[2], "reason": str(exc)[:120]})
        patterns = _as_list(self.params.get("follow_patterns"))
        for seed in seeds:
            if seed not in urls:
                urls.append(seed)
            if patterns and await self._allowed(seed):
                try:
                    html = await self._get_html(seed)
                    urls += [u for u in same_site_links(html, seed, patterns, self.max_pages) if u not in urls]
                except SourceError:
                    continue
        out = []
        per_host: dict[str, int] = {}
        for u in urls:
            host = u.split("/")[2] if "//" in u else u
            if per_host.get(host, 0) >= self.max_pages or blocked(u):
                if blocked(u):
                    self.skipped.append({"url_host": host, "reason": "never-fetch domain"})
                continue
            per_host[host] = per_host.get(host, 0) + 1
            out.append(Candidate(self.name, u))
        return out[:limit]

    async def fetch(self, candidate: Candidate) -> RawProfile | None:
        url = candidate.ref
        if not await self._allowed(url):
            return None
        html = await self._get_html(url)
        return await self.extract(url, html)

    async def extract(self, url: str, html: str) -> RawProfile | None:
        """Turn one public page into a RawProfile, or None if it doesn't describe a professional."""
        text, _ = html_to_text(html)
        pub = published_date(html)
        people = json_ld_people(html)
        if people:
            p = people[0]
            works = p.get("worksFor")
            company = works.get("name") if isinstance(works, dict) else (works if isinstance(works, str) else None)
            alumni = p.get("alumniOf")
            alumni_list = alumni if isinstance(alumni, list) else [alumni] if alumni else []
            edu = [
                Education(institution=str(a.get("name") or "") if isinstance(a, dict) else str(a)) for a in alumni_list
            ]
            edu = [e for e in edu if e.institution]
            addr = p.get("address") or p.get("homeLocation")
            location = (addr.get("addressLocality") or addr.get("name")) if isinstance(addr, dict) else addr
            positions = []
            if company and p.get("jobTitle"):
                positions.append(Position(title=str(p["jobTitle"]), company=str(company), location=location))
            if positions or edu:
                same_as = p.get("sameAs") or []
                links = [url, *([same_as] if isinstance(same_as, str) else same_as)]
                gh = next((lnk.rstrip("/").split("/")[-1] for lnk in links if "github.com/" in lnk), None)
                return RawProfile(
                    source=self.name,
                    source_ref=url,
                    full_name=p.get("name"),
                    headline=p.get("jobTitle"),
                    location=location,
                    positions=positions,
                    education=edu,
                    links=links,
                    github_username=gh,
                    text=text[:5000],
                )
        if self._llm is not None:
            prof = await self._llm_extract(url, text)
            if prof is not None:
                return prof
        found = parse_joined_post(text)
        if not found:
            return None
        start = pub if found["kind"] == "joined" else None
        title = found["title"] or "Unknown"
        return RawProfile(
            source=self.name,
            source_ref=url,
            headline=f"{title} at {found['company']}",
            positions=[Position(title=title, company=str(found["company"]), start_date=start)],
            links=[url],
            summary=text[:1000],
            text=text[:5000],
        )

    async def _llm_extract(self, url: str, text: str) -> RawProfile | None:
        from dcr.enrichment.llm import LLMError
        from dcr.enrichment.schemas import LLMPageProfile

        system = (
            "Extract career facts about the single person this public page is about. Use only facts on the "
            "page; null when unknown. Never infer gender, age, nationality or visa status."
        )
        try:
            res = await self._llm.structured(
                system, f"URL host: {url.split('/')[2]}\n\nPage text:\n{text}", LLMPageProfile, max_tokens=4000
            )
        except LLMError as exc:
            self.llm_cost_usd += exc.cost_usd
            return None
        self.llm_cost_usd += res.cost_usd
        d = res.data
        assert isinstance(d, LLMPageProfile)
        if not d.is_professional_profile:
            return None
        positions = [
            Position(
                title=pp.title,
                company=pp.company,
                start_date=_month(pp.start),
                end_date=_month(pp.end),
                location=pp.location,
                is_internship=None,
            )
            for pp in d.prior_positions
        ]
        if d.current_title and d.current_company:
            positions.append(
                Position(
                    title=d.current_title,
                    company=d.current_company,
                    start_date=_month(d.current_start_date),
                    location=d.location,
                )
            )
        return RawProfile(
            source=self.name,
            source_ref=url,
            location=d.location,
            pronouns=d.pronouns,
            positions=positions,
            education=[
                Education(institution=e.institution, degree=e.degree, field=e.field, end_year=e.end_year)
                for e in d.education
            ],
            skills=d.skills,
            projects=[Project(name=p.name, description=p.description) for p in d.projects],
            links=[url],
            text=text[:5000],
        )
