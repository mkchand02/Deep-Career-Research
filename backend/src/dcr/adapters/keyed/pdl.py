"""People Data Labs Person Search (licensed data; needs PDL_API_KEY; disabled by default).

Uses PDL's SQL search over ``job_company_name``, ``job_start_date`` and ``location_country``. Follow your PDL
contract and permissible-purpose terms. Only fields needed for aggregate analysis are mapped.
"""

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar

import httpx

from dcr.adapters.base import Candidate, SourceAdapter, SourceError
from dcr.adapters.registry import register_adapter
from dcr.models import Education, Position, RawProfile, StudyQuery
from dcr.pipeline.normalize.geo import COUNTRY_NAMES

ENDPOINT = "https://api.peopledatalabs.com/v5/person/search"


def _d(s: str | None) -> date | None:
    if not s:
        return None
    parts = [int(x) for x in s.split("-")]
    return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)


def pdl_to_profile(rec: dict[str, Any]) -> RawProfile:
    positions = []
    for e in rec.get("experience") or []:
        comp = (e.get("company") or {}).get("name")
        title = (e.get("title") or {}).get("name")
        if not comp or not title:
            continue
        loc = None
        if e.get("location_names"):
            loc = e["location_names"][0]
        positions.append(
            Position(
                title=title,
                company=comp,
                start_date=_d(e.get("start_date")),
                end_date=None if e.get("is_primary") and not e.get("end_date") else _d(e.get("end_date")),
                location=loc,
            )
        )
    edu = []
    for ed in rec.get("education") or []:
        school = (ed.get("school") or {}).get("name")
        if school:
            degrees = ed.get("degrees") or []
            majors = ed.get("majors") or []
            end = _d(ed.get("end_date"))
            edu.append(
                Education(
                    institution=school,
                    degree=degrees[0] if degrees else None,
                    field=majors[0] if majors else None,
                    end_year=end.year if end else None,
                )
            )
    links = [f"https://{u}" if "://" not in u else u for u in [rec.get("github_url"), rec.get("linkedin_url")] if u]
    return RawProfile(
        source="pdl",
        source_ref=str(rec.get("id")),
        full_name=rec.get("full_name"),
        headline=rec.get("job_title"),
        location=rec.get("location_name"),
        country=None,
        github_username=rec.get("github_username"),
        links=links,
        positions=positions,
        education=edu,
        skills=list(rec.get("skills") or [])[:40],
    )


@register_adapter
class PDLAdapter(SourceAdapter):
    name: ClassVar[str] = "pdl"
    requires_secret: ClassVar[str | None] = "pdl_api_key"
    default_enabled: ClassVar[bool] = False
    description: ClassVar[str] = "People Data Labs person search (licensed; key required)"
    tos_note: ClassVar[str] = "Licensed data; follow your PDL agreement."

    async def search(self, query: StudyQuery, limit: int) -> list[Candidate]:
        start, end = query.window()
        country = next((k for k, v in COUNTRY_NAMES.items() if v == query.country and len(k) > 3), query.country)
        names = ", ".join("'" + n.lower().replace("'", "''") + "'" for n in query.company_names())
        sql = (
            f"SELECT * FROM person WHERE job_company_name IN ({names}) "
            f"AND job_start_date >= '{start.isoformat()}' AND job_start_date <= '{end.isoformat()}' "
            f"AND location_country = '{country}'"
        )
        out: list[Candidate] = []
        scroll = None
        self._cache: dict[str, dict[str, Any]] = {}
        while len(out) < limit:
            body: dict[str, Any] = {"sql": sql, "size": min(100, limit - len(out))}
            if scroll:
                body["scroll_token"] = scroll
            await self.ctx.bucket.acquire()
            self.ctx.requests_made += 1
            resp = await self.ctx.client.post(
                ENDPOINT, json=body, headers={"X-Api-Key": self.ctx.secrets.pdl_api_key or ""}
            )
            if resp.status_code == 404:
                break
            if resp.status_code != 200:
                raise SourceError(f"pdl: HTTP {resp.status_code}")
            data = resp.json()
            for rec in data.get("data", []):
                self._cache[str(rec.get("id"))] = rec
                out.append(Candidate(self.name, str(rec.get("id"))))
            scroll = data.get("scroll_token")
            if not scroll or not data.get("data"):
                break
        return out[:limit]

    async def fetch(self, candidate: Candidate) -> RawProfile | None:
        rec = self._cache.get(candidate.ref)
        return pdl_to_profile(rec) if rec else None


__all__ = ["PDLAdapter", "httpx", "pdl_to_profile"]
