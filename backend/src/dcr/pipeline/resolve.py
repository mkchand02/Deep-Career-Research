"""Entity resolution: merge the same person across sources.

Evidence, strongest first:

* shared GitHub username or personal-site link (0.97)
* name similarity within a block (same normalized last name), boosted by same current employer,
  same country and shared schools

Pairs scoring at or above ``threshold`` are unioned (union-find). A cluster's confidence is the weakest
link used to build it (singletons: 1.0).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urlparse

from rapidfuzz import fuzz

from dcr.models import Education, Position, Project, RawProfile
from dcr.pipeline.normalize.companies import CompanyNormalizer, default_company_normalizer
from dcr.pipeline.normalize.text import company_key, norm_key

_GENERIC_HOSTS = {"github.com", "linkedin.com", "twitter.com", "x.com", "medium.com", "gmail.com", "google.com"}


@dataclass
class Cluster:
    members: list[int]
    confidence: float


class _UF:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.min_edge = [1.0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int, w: float) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            self.min_edge[ra] = min(self.min_edge[ra], w)
            return
        self.parent[rb] = ra
        self.min_edge[ra] = min(self.min_edge[ra], self.min_edge[rb], w)


def normalize_link(url: str) -> str | None:
    """Canonical identity key for a profile link (``None`` for generic hosts)."""
    try:
        u = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return None
    host = (u.hostname or "").lower().removeprefix("www.")
    path = u.path.rstrip("/").lower()
    if not host:
        return None
    if host == "github.com":
        user = path.strip("/").split("/")[0] if path.strip("/") else ""
        return f"github:{user}" if user else None
    if host in _GENERIC_HOSTS:
        return f"{host}{path}" if path and path != "/" else None
    return host + path


def strong_keys(p: RawProfile) -> set[str]:
    keys = set()
    if p.github_username:
        keys.add(f"github:{p.github_username.lower()}")
    for link in p.links:
        k = normalize_link(link)
        if k:
            keys.add(k)
    return keys


def _name_parts(name: str | None) -> tuple[str, str, str]:
    """(first, last, normalized_full) with middle initials removed."""
    key = norm_key(name)
    toks = [t for t in key.split() if len(t) > 1]
    if not toks:
        return "", "", ""
    return toks[0], toks[-1], " ".join(toks)


def name_similarity(a: str | None, b: str | None) -> float:
    fa, la, na = _name_parts(a)
    fb, lb, nb = _name_parts(b)
    if not na or not nb:
        return 0.0
    if la != lb and fuzz.ratio(la, lb) < 90:
        return 0.0
    score = fuzz.token_sort_ratio(na, nb) / 100
    # nickname / truncated first name ("Alex" vs "Alexandra")
    if fa and fb and (fa.startswith(fb) or fb.startswith(fa)) and min(len(fa), len(fb)) >= 3:
        score = max(score, 0.9)
    return score


def _current_company(p: RawProfile, cn: CompanyNormalizer) -> str:
    cur = [pos for pos in p.positions if pos.end_date is None]
    if not cur:
        return ""
    return cn.match(cur[-1].company).canonical


def _schools(p: RawProfile) -> set[str]:
    return {norm_key(e.institution) for e in p.education}


def pair_score(a: RawProfile, b: RawProfile, cn: CompanyNormalizer) -> float:
    if strong_keys(a) & strong_keys(b):
        return 0.97
    ns = name_similarity(a.full_name, b.full_name)
    if ns < 0.75:
        return 0.0
    score = 0.65 * ns
    ca, cb = _current_company(a, cn), _current_company(b, cn)
    if ca and cb:
        score += 0.22 if ca == cb else -0.3
    if a.country and b.country:
        score += 0.05 if a.country == b.country else -0.1
    sa, sb = _schools(a), _schools(b)
    if sa and sb:
        score += 0.08 if sa & sb else -0.05
    return round(min(score, 0.96), 3)


def resolve(profiles: list[RawProfile], threshold: float = 0.82, cn: CompanyNormalizer | None = None) -> list[Cluster]:
    """Cluster profiles that refer to the same person."""
    cn = cn or default_company_normalizer()
    n = len(profiles)
    uf = _UF(n)
    # strong-key blocking
    by_key: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(profiles):
        for k in strong_keys(p):
            by_key[k].append(i)
    for idxs in by_key.values():
        for j in idxs[1:]:
            uf.union(idxs[0], j, 0.97)
    # name blocking on last name
    by_last: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(profiles):
        _, last, _ = _name_parts(p.full_name)
        if last:
            by_last[last[:4]].append(i)
    for idxs in by_last.values():
        if len(idxs) > 400:  # pathological block; skip fuzzy comparisons
            continue
        for x in range(len(idxs)):
            for y in range(x + 1, len(idxs)):
                i, j = idxs[x], idxs[y]
                if uf.find(i) == uf.find(j):
                    continue
                s = pair_score(profiles[i], profiles[j], cn)
                if s >= threshold:
                    uf.union(i, j, s)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)
    return [Cluster(members=m, confidence=1.0 if len(m) == 1 else uf.min_edge[root]) for root, m in groups.items()]


def _completeness(p: RawProfile) -> int:
    return len(p.positions) * 3 + len(p.education) * 2 + len(p.skills) + len(p.projects) + len(p.links)


def merge_profiles(profiles: list[RawProfile]) -> RawProfile:
    """Merge a cluster into one RawProfile, preferring the most complete record for scalar fields."""
    if len(profiles) == 1:
        return profiles[0]
    ordered = sorted(profiles, key=_completeness, reverse=True)
    base = ordered[0].model_copy(deep=True)

    def first(attr: str) -> object:
        for p in ordered:
            v = getattr(p, attr)
            if v:
                return v
        return None

    for attr in ("headline", "location", "country", "pronouns", "github_username", "summary", "text"):
        setattr(base, attr, first(attr))
    names = [p.full_name for p in ordered if p.full_name]
    base.full_name = max(names, key=lambda s: len(re.sub(r"\b\w\.\s*", "", s))) if names else None

    seen_pos: set[tuple[str, str, str]] = set()
    positions: list[Position] = []
    for p in ordered:
        for pos in p.positions:
            k = (company_key(pos.company), norm_key(pos.title), str(pos.start_date))
            if k not in seen_pos:
                seen_pos.add(k)
                positions.append(pos)
    base.positions = positions

    seen_edu: set[tuple[str, str]] = set()
    edu: list[Education] = []
    for p in ordered:
        for e in p.education:
            ek = (norm_key(e.institution), norm_key(e.degree))
            if ek not in seen_edu:
                seen_edu.add(ek)
                edu.append(e)
    base.education = edu

    base.skills = list(dict.fromkeys(s for p in ordered for s in p.skills))
    base.links = list(dict.fromkeys(link for p in ordered for link in p.links))
    seen_proj: set[str] = set()
    projects: list[Project] = []
    for p in ordered:
        for pr in p.projects:
            pk = norm_key(pr.name)
            if pk not in seen_proj:
                seen_proj.add(pk)
                projects.append(pr)
    base.projects = projects
    base.synthetic = all(p.synthetic for p in profiles)
    return base
