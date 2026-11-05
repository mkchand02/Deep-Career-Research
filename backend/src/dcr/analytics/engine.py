"""Analytics engine: de-identified records → k-anonymized report sections."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from dcr.analytics import demographics, keywords
from dcr.analytics.kanon import distribution, population_ok, proportion
from dcr.analytics.stats import bootstrap_median_ci, chapman_estimate, percentile
from dcr.models import AnalysisRecord, EnrichedProfile, NormalizedProfile, NumericSummary, StudyQuery

EXP_BANDS = ["<1 yr", "1–2 yrs", "2–3 yrs", "3–4 yrs", "4–6 yrs", "6+ yrs", "unknown"]
LEVEL_ORDER = ["entry", "mid", "senior", "staff", "principal", "unknown"]

DIMENSIONS: dict[str, Callable[[AnalysisRecord], str]] = {
    "role": lambda r: r.role,
    "level": lambda r: r.level,
    "exp_band": lambda r: r.exp_band,
    "source": lambda r: r.source,
    "edu_origin": lambda r: r.edu_origin,
    "highest_degree": lambda r: r.highest_degree,
    "hire_type": lambda r: r.hire_type,
}
DIMENSION_LABELS = {
    "role": "Role",
    "level": "Level",
    "exp_band": "Experience band",
    "source": "Source",
    "edu_origin": "Education origin",
    "highest_degree": "Highest degree",
    "hire_type": "Hire type",
}


def exp_band(years: float | None) -> str:
    if years is None:
        return "unknown"
    y = math.floor(years + 1e-9)
    if y < 1:
        return EXP_BANDS[0]
    if y < 2:
        return EXP_BANDS[1]
    if y < 3:
        return EXP_BANDS[2]
    if y < 4:
        return EXP_BANDS[3]
    if y < 6:
        return EXP_BANDS[4]
    return EXP_BANDS[5]


@dataclass
class EngineContext:
    k: int
    query: StudyQuery
    baseline_skill_docs: list[list[str]] = field(default_factory=list)
    name_estimator_enabled: bool = False


def to_record(
    p: NormalizedProfile,
    enriched: EnrichedProfile | None,
    query: StudyQuery,
    name_table: demographics.NameGenderTable | None = None,
    name_min_count: int = 50,
) -> AnalysisRecord:
    """Drop identifiers and derive analysis fields. The first name is used only for the optional estimator."""
    projects = enriched.projects if enriched and enriched.projects else p.projects
    skills = list(p.skills)
    if enriched:
        skills += [s for s in enriched.extra_skills if s not in skills]
    edu = enriched.education if enriched and enriched.education else p.education
    hd_country = p.highest_degree_country
    if enriched and enriched.education:
        from dcr.pipeline.normalize.education import highest_degree

        hd = highest_degree(edu)
        hd_country = hd.country if hd else hd_country
    if hd_country is None:
        edu_origin = "unknown"
    else:
        edu_origin = "domestic" if hd_country == query.country else "foreign"
    if p.is_new_grad is None:
        hire_type = "unknown"
    else:
        hire_type = "new_grad" if p.is_new_grad else "lateral"
    if p.prior_work_countries:
        worked_abroad: bool | None = any(c != query.country for c in p.prior_work_countries)
    elif not p.prior_companies:
        worked_abroad = False
    else:
        worked_abroad = None
    prior_types = list(p.prior_company_types)
    if enriched and enriched.experience.prior_company_types:
        prior_types = enriched.experience.prior_company_types
    est = None
    if name_table is not None and p.pronouns is None:
        est = name_table.estimate(p.first_name, name_min_count)
    stars = [pr.stars for pr in projects if pr.stars is not None]
    commits = [pr.commits for pr in projects if pr.commits is not None]
    return AnalysisRecord(
        role=p.canonical_role or "unknown",
        level=p.level.value,
        exp_years=p.exp_years,
        exp_band=exp_band(p.exp_years),
        source=p.sources[0] if len(p.sources) == 1 else "multi-source",
        sources=p.sources,
        start_date=p.start_date,
        edu_origin=edu_origin,  # type: ignore[arg-type]
        highest_degree=p.highest_degree.value if hasattr(p.highest_degree, "value") else str(p.highest_degree),
        fields=sorted({e.field for e in edu if e.field}),
        schools=sorted({e.institution for e in edu if e.institution}),
        skills=skills,
        keywords_text=p.keywords_text,
        project_tags=sorted({t for pr in projects for t in pr.tags}),
        project_kinds=sorted({pr.kind for pr in projects}),
        project_stacks=sorted({t for pr in projects for t in pr.tech_stack}),
        project_focus=sorted({pr.focus for pr in projects if pr.focus}),
        has_github=p.has_github,
        github_stars=stars,
        github_commits=commits,
        prior_companies=p.prior_companies,
        prior_company_types=sorted(set(prior_types)),
        hire_type=hire_type,  # type: ignore[arg-type]
        interned_at_target=p.interned_at_target,
        worked_abroad=worked_abroad,
        pronoun_group=demographics.pronoun_group(p.pronouns),  # type: ignore[arg-type]
        p_female_name=est.p_female if est else None,
        name_estimable=est is not None,
    )


def numeric(name: str, values: list[float], k: int, note: str = "") -> dict[str, Any]:
    vals = sorted(v for v in values if v is not None and not math.isnan(v))
    if len(vals) < k:
        return NumericSummary(name=name, n=None, suppressed=True, note=f"fewer than {k} values").model_dump()
    lo, hi = bootstrap_median_ci(vals, iters=300)
    return NumericSummary(
        name=name,
        n=len(vals),
        p25=round(percentile(vals, 0.25), 2),
        median=round(percentile(vals, 0.5), 2),
        p75=round(percentile(vals, 0.75), 2),
        mean=round(sum(vals) / len(vals), 2),
        ci_low=round(lo, 2),
        ci_high=round(hi, 2),
        note=note or "quartiles only; min/max never shown",
    ).model_dump()


def _ordered(dist: dict[str, Any], order: list[str]) -> dict[str, Any]:
    pos = {v: i for i, v in enumerate(order)}
    dist["buckets"].sort(key=lambda b: pos.get(b["key"], 99))
    return dist


def _multi(
    name: str,
    records: list[AnalysisRecord],
    get: Callable[[AnalysisRecord], list[str]],
    k: int,
    top: int = 15,
    note: str = "",
) -> dict[str, Any]:
    c: Counter[str] = Counter(v for r in records for v in set(get(r)))
    return distribution(name, c, len(records), k, partition=False, top=top, note=note).model_dump()


def _part(
    name: str, records: list[AnalysisRecord], get: Callable[[AnalysisRecord], str], k: int, note: str = ""
) -> dict[str, Any]:
    c: Counter[str] = Counter(get(r) for r in records)
    return distribution(name, c, len(records), k, partition=True, note=note).model_dump()


def compute_sections(records: list[AnalysisRecord], ctx: EngineContext, *, full: bool = True) -> dict[str, Any]:
    """All report sections for one (already population-checked) set of records."""
    k = ctx.k
    n = len(records)
    with_projects = [r for r in records if r.project_kinds]
    sections: dict[str, Any] = {
        "overview": {
            "n": n,
            "roles": _part("roles", records, lambda r: r.role, k),
            "levels": _ordered(
                _part(
                    "levels",
                    records,
                    lambda r: r.level,
                    k,
                    note="Level from title + company ladder; inferred from experience when the title has no level",
                ),
                LEVEL_ORDER,
            ),
            "exp_bands": _ordered(
                _part("exp_bands", records, lambda r: r.exp_band, k, note="Completed years, internships excluded"),
                EXP_BANDS,
            ),
            "exp_years": numeric("exp_years", [r.exp_years for r in records if r.exp_years is not None], k),
            "sources": _part("sources", records, lambda r: r.source, k),
        },
        "keywords": {
            "top_skills": keywords.skill_frequency(records, k),
            "distinctive": keywords.distinctive_terms(records, ctx.baseline_skill_docs, k),
            "by_role": keywords.skills_by_role(records, k),
        },
        "projects": {
            "with_projects": proportion(
                "with_projects", len(with_projects), n, k, label="% of profiles with at least one listed project"
            ).model_dump(),
            "tags": _multi("project_tags", with_projects, lambda r: r.project_tags, k),
            "focus": _multi("project_focus", with_projects, lambda r: r.project_focus, k),
            "tech_stacks": _multi("project_stacks", with_projects, lambda r: r.project_stacks, k, top=20),
            "kinds": _multi(
                "project_kinds",
                with_projects,
                lambda r: r.project_kinds,
                k,
                note="% of people-with-projects having ≥1 project of each kind",
            ),
            "has_github": proportion(
                "has_github", sum(r.has_github for r in records), n, k, label="% with a public GitHub profile"
            ).model_dump(),
            "max_stars": numeric(
                "max_stars",
                [max(r.github_stars) for r in records if r.github_stars],
                k,
                note="Highest-starred listed repo per person (quartiles)",
            ),
            "max_commits": numeric("max_commits", [max(r.github_commits) for r in records if r.github_commits], k),
        },
        "education": {
            "degree_levels": _part("degree_levels", records, lambda r: r.highest_degree, k),
            "top_schools": _multi("top_schools", records, lambda r: r.schools, k),
            "top_fields": _multi("top_fields", records, lambda r: r.fields, k),
            "origin": _part(
                "edu_origin",
                records,
                lambda r: r.edu_origin,
                k,
                note=f"Where the highest degree was earned relative to {ctx.query.country}",
            ),
        },
        "experience": {
            "feeder_companies": _multi("feeder_companies", records, lambda r: r.prior_companies, k),
            "prior_company_types": _multi("prior_company_types", records, lambda r: r.prior_company_types, k),
            "hire_type": _part(
                "hire_type",
                records,
                lambda r: r.hire_type,
                k,
                note="new grad = <6 months prior full-time work and degree within ~1 year",
            ),
            "interned_at_target": proportion(
                "interned_at_target",
                sum(r.interned_at_target for r in records),
                n,
                k,
                label=f"% who previously interned at {ctx.query.company} (internship→FTE conversion signal)",
            ).model_dump(),
        },
        "demographics": {
            "pronouns": demographics.pronoun_distribution(records, k),
            "international": demographics.international_proxies(records, ctx.query.country, k),
        },
    }
    if ctx.name_estimator_enabled:
        sections["demographics"]["name_estimate"] = demographics.name_based_estimate(records, k)
    if full:
        sections["keywords"]["text_terms"] = keywords.text_keywords(records, k)
        sections["keywords"]["cooccurrence"] = keywords.cooccurrence(records, k)
    return sections


def coverage(records: list[AnalysisRecord], k: int, discovered_persons: int) -> dict[str, Any]:
    """Representativeness indicators, including Chapman capture-recapture when two sources overlap."""
    n = len(records)
    src_counts: Counter[str] = Counter(s for r in records for s in r.sources)
    out: dict[str, Any] = {
        "n": n,
        "discovered_persons": discovered_persons,
        "match_rate_pct": round(100 * n / discovered_persons, 1) if discovered_persons else None,
        "completeness": {
            "education_known": proportion(
                "education_known",
                sum(r.edu_origin != "unknown" for r in records),
                n,
                k,
                label="% with education country known",
            ).model_dump(),
            "skills_3plus": proportion(
                "skills_3plus", sum(len(r.skills) >= 3 for r in records), n, k, label="% with ≥3 skills"
            ).model_dump(),
            "has_projects": proportion(
                "has_projects", sum(bool(r.project_kinds) for r in records), n, k, label="% with listed projects"
            ).model_dump(),
        },
        "capture_recapture": None,
    }
    if len(src_counts) >= 2:
        (a, n1), (b, n2) = src_counts.most_common(2)
        m = sum(1 for r in records if a in r.sources and b in r.sources)
        est = chapman_estimate(n1, n2, m)
        if est and m >= k and n1 >= k and n2 >= k:
            pop, se = est
            out["capture_recapture"] = {
                "sources": [a, b],
                "n1": n1,
                "n2": n2,
                "overlap": m,
                "estimated_population": round(pop),
                "ci_low": round(max(n, pop - 1.96 * se)),
                "ci_high": round(pop + 1.96 * se),
                "sample_coverage_pct": round(100 * n / pop, 1) if pop else None,
                "note": "Chapman estimator; assumes the two sources sample independently (they rarely do), so "
                "treat as an order-of-magnitude check.",
            }
    return out


def filter_records(records: list[AnalysisRecord], filters: dict[str, str]) -> list[AnalysisRecord]:
    out = records
    for dim, val in filters.items():
        get = DIMENSIONS[dim]
        out = [r for r in out if get(r) == val]
    return out


def view(
    records: list[AnalysisRecord], filters: dict[str, str], ctx: EngineContext, full: bool = False
) -> dict[str, Any]:
    """Population-checked report for a filter selection (with differencing protection)."""
    sub = filter_records(records, filters)
    parent_ns = []
    for dim in filters:
        rest = {d: v for d, v in filters.items() if d != dim}
        parent_ns.append(len(filter_records(records, rest)))
    ok, reason = population_ok(len(sub), ctx.k, parent_ns)
    if not ok:
        return {"filters": filters, "suppressed": True, "suppression_reason": reason, "n": None, "sections": {}}
    return {
        "filters": filters,
        "suppressed": False,
        "suppression_reason": "",
        "n": len(sub),
        "sections": compute_sections(sub, ctx, full=full),
    }


def filter_key(filters: dict[str, str]) -> str:
    return "&".join(f"{d}={filters[d]}" for d in sorted(filters))


def dimension_options(records: list[AnalysisRecord], k: int) -> dict[str, list[dict[str, Any]]]:
    """Available cross-filter values (counts suppressed below k)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for dim, get in DIMENSIONS.items():
        c = Counter(get(r) for r in records)
        out[dim] = [
            {"value": v, "n": n if n >= k else None, "suppressed": n < k}
            for v, n in sorted(c.items(), key=lambda kv: -kv[1])
        ]
    return out
