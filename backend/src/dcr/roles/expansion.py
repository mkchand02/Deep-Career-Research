"""Role expansion: user role list → confirmed :class:`RoleSpec` list.

1. Each requested role is parsed with the title parser ("SDE II" → software_engineer @ mid band,
   using the target company's level ladder).
2. Requests for the same canonical role are merged (level sets unioned; any-level wins).
3. Synonym titles come from ``role_synonyms.yaml``; ``include_similar`` adds similar roles.
4. An optional LLM suggester can add extra title variants (Phase 4); the result is always shown to the
   user for confirmation before a run.
"""

from __future__ import annotations

from collections.abc import Callable

from dcr.models import LevelBand, RoleSpec
from dcr.roles.titles import RoleCatalog, default_catalog

Suggester = Callable[[list[str], str], dict[str, list[str]]]


class RoleExpansionError(ValueError):
    pass


def expand_roles(
    requested: list[str],
    *,
    company: str | None = None,
    include_similar: bool = True,
    catalog: RoleCatalog | None = None,
    suggester: Suggester | None = None,
) -> tuple[list[RoleSpec], list[str]]:
    """Return (specs, unrecognized_requests)."""
    cat = catalog or default_catalog()
    by_role: dict[str, RoleSpec] = {}
    unrecognized: list[str] = []
    any_level: set[str] = set()

    for req in requested:
        parsed = cat.parse(req, company)
        role = parsed.canonical_role
        if not role:
            unrecognized.append(req)
            continue
        spec = by_role.get(role) or RoleSpec(
            canonical_role=role, display_name=cat.display(role), titles=cat.titles(role), origin="user"
        )
        spec.requested_as.append(req)
        if parsed.level == LevelBand.UNKNOWN:
            any_level.add(role)
        elif parsed.level not in spec.levels:
            spec.levels.append(parsed.level)
        by_role[role] = spec

    for role in any_level:
        by_role[role].levels = []

    if include_similar:
        for role, spec in list(by_role.items()):
            for sim in cat.similar(role):
                if sim in by_role:
                    continue
                by_role[sim] = RoleSpec(
                    canonical_role=sim,
                    display_name=cat.display(sim),
                    titles=cat.titles(sim),
                    levels=list(spec.levels),
                    origin="similar",
                    requested_as=[f"similar to {spec.display_name}"],
                )

    if suggester and by_role:
        try:
            extra = suggester(requested, company or "")
        except Exception:
            extra = {}
        for role, titles in extra.items():
            if role in by_role:
                for t in titles:
                    if t.lower() not in {x.lower() for x in by_role[role].titles}:
                        by_role[role].titles.append(t)

    order = {"user": 0, "synonym": 1, "llm": 2, "similar": 3}
    specs = sorted(by_role.values(), key=lambda s: (order.get(s.origin, 9), s.display_name))
    for s in specs:
        s.levels.sort(key=lambda lv: list(LevelBand).index(lv))
    return specs, unrecognized


def role_matches(specs: list[RoleSpec], role: str | None, level: LevelBand) -> RoleSpec | None:
    for s in specs:
        if s.matches(role, level):
            return s
    return None
