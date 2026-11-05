"""Brave Search API discovery (needs BRAVE_API_KEY; disabled by default).

Runs searches such as ``"joined Google" "software engineer"`` to *discover* public pages, then hands
each URL to the robots.txt-respecting web scraper for fetching and extraction.
"""

from __future__ import annotations

from typing import Any, ClassVar

from dcr.adapters.base import Candidate, SourceError
from dcr.adapters.registry import register_adapter
from dcr.adapters.web_scraper import WebScraperAdapter
from dcr.adapters.webutil import blocked
from dcr.models import StudyQuery

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def default_queries(query: StudyQuery) -> list[str]:
    roles = query.roles[:4] or ["engineer"]
    return [f'"joined {query.company}" "{r}"' for r in roles] + [f'"{query.company}" "new role" "{roles[0]}"']


@register_adapter
class BraveSearchAdapter(WebScraperAdapter):
    name: ClassVar[str] = "brave"
    requires_secret: ClassVar[str | None] = "brave_api_key"
    default_enabled: ClassVar[bool] = False
    description: ClassVar[str] = "Brave Search discovery → robots.txt-respecting fetch"
    tos_note: ClassVar[str] = "Brave Search API terms; discovery only."

    async def search(self, query: StudyQuery, limit: int) -> list[Candidate]:
        queries: list[str] = self.params.get("queries") or default_queries(query)
        urls: list[str] = []
        for q in queries:
            res = await self.ctx.get(
                ENDPOINT,
                params={"q": q, "count": 20},
                headers={"X-Subscription-Token": self.ctx.secrets.brave_api_key or "", "Accept": "application/json"},
            )
            data: dict[str, Any] = res.json()
            for r in (data.get("web") or {}).get("results", []):
                u = r.get("url")
                if u and not blocked(u) and u not in urls:
                    urls.append(u)
        if not urls:
            raise SourceError("brave: no results")
        return [Candidate(self.name, u) for u in urls[:limit]]
