"""X (Twitter) official API v2 recent search (needs X_BEARER_TOKEN; disabled by default).

Finds public "I just joined <company>" posts. The post date becomes the approximate start date. Only
the official API is used, with the user's own credentials.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from dcr.adapters.base import Candidate, SourceAdapter
from dcr.adapters.registry import register_adapter
from dcr.adapters.webutil import parse_joined_post
from dcr.models import Position, RawProfile, StudyQuery
from dcr.pipeline.normalize.text import company_key

ENDPOINT = "https://api.x.com/2/tweets/search/recent"


@register_adapter
class XAdapter(SourceAdapter):
    name: ClassVar[str] = "x"
    requires_secret: ClassVar[str | None] = "x_bearer_token"
    default_enabled: ClassVar[bool] = False
    description: ClassVar[str] = "X API v2 recent search for 'joined <company>' posts"
    tos_note: ClassVar[str] = "Official X API only, within its developer terms."

    async def search(self, query: StudyQuery, limit: int) -> list[Candidate]:
        q = (
            self.params.get("query")
            or f'("joined {query.company}" OR "started at {query.company}") -is:retweet lang:en'
        )
        res = await self.ctx.get(
            ENDPOINT,
            params={
                "query": q,
                "max_results": min(100, max(10, limit)),
                "tweet.fields": "created_at,author_id",
                "expansions": "author_id",
                "user.fields": "name,username,location,description",
            },
            headers={"Authorization": f"Bearer {self.ctx.secrets.x_bearer_token}"},
        )
        data: dict[str, Any] = res.json()
        users = {u["id"]: u for u in (data.get("includes") or {}).get("users", [])}
        self._items: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        target = {company_key(n) for n in query.company_names()}
        for t in data.get("data", []):
            found = parse_joined_post(t.get("text", ""))
            if not found or company_key(found["company"]) not in target:
                continue
            self._items[t["id"]] = (t, users.get(t.get("author_id"), {}))
        return [Candidate(self.name, k) for k in list(self._items)[:limit]]

    async def fetch(self, candidate: Candidate) -> RawProfile | None:
        t, u = self._items[candidate.ref]
        found = parse_joined_post(t.get("text", "")) or {}
        created = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")).date() if t.get("created_at") else None
        title = found.get("title") or "Unknown"
        return RawProfile(
            source=self.name,
            source_ref=candidate.ref,
            full_name=u.get("name"),
            headline=u.get("description"),
            location=u.get("location"),
            links=[f"https://x.com/{u.get('username')}"] if u.get("username") else [],
            positions=[
                Position(title=title, company=str(found.get("company")), start_date=created, location=u.get("location"))
            ],
        )
