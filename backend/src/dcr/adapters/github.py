"""GitHub REST API adapter (zero-key; ``GITHUB_TOKEN`` optional for higher rate limits).

Discovery (any combination of params):
* ``orgs``: *public* members of company GitHub orgs (members who chose to make membership public);
* ``users``: explicit usernames;
* ``search``: a raw GitHub user-search query (e.g. ``location:"San Francisco" followers:>5``).

Every discovered user is fetched with their public profile + repos (projects, languages, topics, stars).
``enrich(profile)`` adds GitHub projects to a profile from another source that links a GitHub account.
The start date at an employer is not on GitHub, so these profiles carry an unknown start date unless
another source supplies one (merged by entity resolution through the shared GitHub username).
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from dcr.adapters.base import Candidate, SourceAdapter, SourceError
from dcr.adapters.registry import register_adapter
from dcr.models import Position, Project, RawProfile, StudyQuery
from dcr.roles.titles import default_catalog

API = "https://api.github.com"


def _list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in re.split(r"[\n,|]", v) if x.strip()]
    return [str(x) for x in v]


def title_from_bio(bio: str | None) -> str | None:
    """Pick the first bio fragment that parses as a known job title."""
    if not bio:
        return None
    cat = default_catalog()
    for frag in re.split(r"[|•·,;/\n]| @ | at ", bio):
        frag = frag.strip(" .-@")
        if 3 <= len(frag) <= 60:
            p = cat.parse(frag)
            if p.canonical_role and p.confidence >= 0.85:
                return frag
    return None


@register_adapter
class GitHubAdapter(SourceAdapter):
    name: ClassVar[str] = "github"
    description: ClassVar[str] = "GitHub public profiles and repositories (org members, users, search)"
    tos_note: ClassVar[str] = "GitHub REST API within its rate limits; public data only."

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.ctx.secrets.github_token:
            h["Authorization"] = f"Bearer {self.ctx.secrets.github_token}"
        return h

    async def _json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        res = await self.ctx.get(f"{API}{path}", params=params, headers=self._headers())
        return res.json()

    async def search(self, query: StudyQuery, limit: int) -> list[Candidate]:
        users: list[str] = []
        for org in _list(self.params.get("orgs")):
            page = 1
            while len(users) < limit and page <= 10:
                batch = await self._json(f"/orgs/{org}/public_members", {"per_page": 100, "page": page})
                if not batch:
                    break
                users += [u["login"] for u in batch if u["login"] not in users]
                page += 1
        users += [u for u in _list(self.params.get("users")) if u not in users]
        if self.params.get("search"):
            data = await self._json("/search/users", {"q": self.params["search"], "per_page": min(100, limit)})
            users += [u["login"] for u in data.get("items", []) if u["login"] not in users]
        if not users and not self.params.get("enrich_only"):
            raise SourceError("github needs params.orgs, params.users or params.search")
        return [Candidate(self.name, u) for u in users[:limit]]

    async def fetch(self, candidate: Candidate) -> RawProfile | None:
        return await self.profile(candidate.ref)

    async def profile(self, login: str) -> RawProfile | None:
        u = await self._json(f"/users/{login}")
        if u.get("type") != "User":
            return None
        repos = await self._json(f"/users/{login}/repos", {"sort": "pushed", "per_page": 30, "type": "owner"})
        projects = []
        langs: list[str] = []
        for r in repos:
            if r.get("fork") or r.get("archived"):
                continue
            stack = [r["language"]] if r.get("language") else []
            if r.get("language") and r["language"] not in langs:
                langs.append(r["language"])
            projects.append(
                Project(
                    name=r["name"],
                    description=r.get("description"),
                    url=r.get("html_url"),
                    tags=list(r.get("topics") or [])[:8],
                    tech_stack=stack,
                    stars=int(r.get("stargazers_count") or 0),
                    kind="unknown",
                )
            )
        projects.sort(key=lambda p: -(p.stars or 0))
        company = (u.get("company") or "").strip().lstrip("@").strip() or None
        positions = []
        if company:
            title = title_from_bio(u.get("bio")) or "Engineer"
            positions.append(Position(title=title, company=company, location=u.get("location")))
        links = [f"https://github.com/{login}"]
        if u.get("blog"):
            links.append(u["blog"] if "://" in u["blog"] else f"https://{u['blog']}")
        return RawProfile(
            source=self.name,
            source_ref=login,
            full_name=u.get("name"),
            headline=u.get("bio"),
            location=u.get("location"),
            github_username=login,
            links=links,
            positions=positions,
            skills=langs,
            projects=projects[:15],
            summary=u.get("bio"),
        )

    async def enrich(self, profile: RawProfile) -> RawProfile | None:
        """GitHub view of a profile from another source (merged later via the shared username)."""
        if not profile.github_username:
            return None
        try:
            gh = await self.profile(profile.github_username)
        except SourceError:
            return None
        if gh is None:
            return None
        # Only contribute projects/skills; employment facts come from the primary source.
        return gh.model_copy(update={"positions": [], "source_ref": f"enrich:{profile.github_username}"})
