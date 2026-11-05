"""Public job-board APIs (no key): Greenhouse, Lever, Ashby.

``params.board`` is the company's board token (e.g. ``stripe`` on Greenhouse). Postings are not personal
data. They serve as a complementary, leading signal that cross-checks the trends seen in joiner profiles.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from dcr.adapters.base import JobPosting, JobPostingAdapter, SourceError
from dcr.adapters.registry import register_adapter
from dcr.adapters.webutil import html_to_text
from dcr.models import StudyQuery
from dcr.pipeline.normalize.skills import default_skill_normalizer


def _boards(params: dict[str, Any], query: StudyQuery) -> list[str]:
    b = params.get("board") or params.get("boards")
    if not b:
        return [query.company.lower().replace(" ", "")]
    return [x.strip() for x in (b.split(",") if isinstance(b, str) else b) if x.strip()]


def _skills(text: str | None) -> list[str]:
    return default_skill_normalizer().find_in_text(text or "")[:25]


def _iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)
    except ValueError:
        return None


@register_adapter
class GreenhouseAdapter(JobPostingAdapter):
    name: ClassVar[str] = "greenhouse"
    description: ClassVar[str] = "Greenhouse public job board API"
    tos_note: ClassVar[str] = "boards-api.greenhouse.io public Job Board API."

    async def postings(self, query: StudyQuery, limit: int) -> list[JobPosting]:
        out: list[JobPosting] = []
        for board in _boards(self.params, query):
            data = (
                await self.ctx.get(
                    f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"}
                )
            ).json()
            for j in data.get("jobs", [])[:limit]:
                desc = html_to_text(_unescape(j.get("content") or ""))[0]
                out.append(
                    JobPosting(
                        source=self.name,
                        company=query.company,
                        title=j.get("title", ""),
                        location=(j.get("location") or {}).get("name"),
                        skills=_skills(desc),
                        posted_at=_iso(j.get("first_published") or j.get("updated_at")),
                        url=j.get("absolute_url"),
                    )
                )
        return out


@register_adapter
class LeverAdapter(JobPostingAdapter):
    name: ClassVar[str] = "lever"
    description: ClassVar[str] = "Lever public postings API"
    tos_note: ClassVar[str] = "api.lever.co/v0/postings public API."

    async def postings(self, query: StudyQuery, limit: int) -> list[JobPosting]:
        out: list[JobPosting] = []
        for board in _boards(self.params, query):
            data = (await self.ctx.get(f"https://api.lever.co/v0/postings/{board}", params={"mode": "json"})).json()
            if not isinstance(data, list):
                raise SourceError(f"lever: unexpected response for board {board}")
            for j in data[:limit]:
                cats = j.get("categories") or {}
                lists = " ".join(html_to_text(x.get("content", ""))[0] for x in j.get("lists", []))
                created = j.get("createdAt")
                out.append(
                    JobPosting(
                        source=self.name,
                        company=query.company,
                        title=j.get("text", ""),
                        location=cats.get("location"),
                        skills=_skills((j.get("descriptionPlain") or "") + " " + lists),
                        posted_at=datetime.fromtimestamp(created / 1000, UTC).replace(tzinfo=None) if created else None,
                        url=j.get("hostedUrl"),
                    )
                )
        return out


@register_adapter
class AshbyAdapter(JobPostingAdapter):
    name: ClassVar[str] = "ashby"
    description: ClassVar[str] = "Ashby public job board API"
    tos_note: ClassVar[str] = "api.ashbyhq.com/posting-api public API."

    async def postings(self, query: StudyQuery, limit: int) -> list[JobPosting]:
        out: list[JobPosting] = []
        for board in _boards(self.params, query):
            data = (await self.ctx.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}")).json()
            for j in data.get("jobs", [])[:limit]:
                out.append(
                    JobPosting(
                        source=self.name,
                        company=query.company,
                        title=j.get("title", ""),
                        location=j.get("location"),
                        skills=_skills(j.get("descriptionPlain")),
                        posted_at=_iso(j.get("publishedAt")),
                        url=j.get("jobUrl"),
                    )
                )
        return out


def _unescape(s: str) -> str:
    import html

    return html.unescape(s)
