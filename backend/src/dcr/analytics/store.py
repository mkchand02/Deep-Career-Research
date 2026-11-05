"""Build, persist and serve aggregate reports.

At run end we precompute k-anonymized reports for the base view, every single-dimension filter and every
two-dimension filter, and store them in ``aggregates``. That table survives the TTL purge of personal
data. While personal rows still exist, arbitrary filter combinations are computed on the fly with the
same rules.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from itertools import combinations
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session, sessionmaker

from dcr.analytics import demographics
from dcr.analytics.engine import (
    DIMENSION_LABELS,
    DIMENSIONS,
    EngineContext,
    coverage,
    dimension_options,
    filter_key,
    filter_records,
    to_record,
    view,
)
from dcr.analytics.kanon import distribution, population_ok
from dcr.config import AppConfig
from dcr.db.models import AggregateRow, EnrichedProfileRow, JobPostingRow, NormalizedProfileRow, Run
from dcr.models import AnalysisRecord, EnrichedProfile, NormalizedProfile, RunCost, StageCounts, StudyQuery
from dcr.pipeline.filter import evaluate

if TYPE_CHECKING:
    from dcr.pipeline.runner import StudyRunner

SCHEMA_VERSION = "1"


@lru_cache(maxsize=1)
def _name_table() -> demographics.NameGenderTable | None:
    return demographics.NameGenderTable.load()


def estimator_enabled(config: AppConfig, query: StudyQuery) -> bool:
    if query.gender_name_estimator is not None:
        return query.gender_name_estimator
    return config.privacy.gender_name_estimator


def make_records(
    sampled: list[NormalizedProfile], enriched: dict[str, EnrichedProfile], query: StudyQuery, config: AppConfig
) -> tuple[list[AnalysisRecord], bool]:
    est_on = estimator_enabled(config, query)
    table = _name_table() if est_on else None
    recs = [to_record(p, enriched.get(p.anon_id), query, table, config.privacy.gender_name_min_count) for p in sampled]
    return recs, est_on and table is not None


def methodology(
    query: StudyQuery,
    config: AppConfig,
    counts: StageCounts | None,
    est_on: bool,
    est_table_missing: bool,
    sources: list[str],
) -> dict[str, Any]:
    start, end = query.window()
    return {
        "k_anonymity": config.privacy.k_anonymity,
        "raw_ttl_days": config.privacy.raw_ttl_days,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "filters": {
            "company": query.company,
            "aliases": query.company_aliases,
            "country": query.country,
            "regions": query.regions,
            "experience": [query.exp_min, query.exp_max],
            "exclude_internships": query.exclude_internships,
            "min_confidence": query.min_confidence,
            "include_unknown_start_date": query.include_unknown_start_date,
            "sample_cap": query.sample_cap,
        },
        "sources": sources,
        "pipeline_counts": counts.model_dump() if counts else None,
        "gender_estimator": "enabled"
        if est_on
        else ("enabled but name table missing (run `dcr data fetch-names`)" if est_table_missing else "disabled"),
        "limitations": [
            "Sampling bias: only people with discoverable public profiles are included. People who post publicly "
            "(GitHub, personal sites, blogs) are over-represented.",
            "Source coverage varies by company, country and role; see the coverage panel and source mix.",
            "Start dates, titles and education are self-reported and may be stale or incomplete.",
            "Levels are inferred from titles with company-specific ladders, or from experience when the title "
            "has no level, so they are approximate.",
            "Entity resolution can merge different people or split one person; match confidence is recorded "
            "and low-confidence matches are excluded.",
            "Gender: only self-stated pronouns are counted directly. The optional name-based estimate is a rough "
            "aggregate with wide uncertainty and is never applied to individuals.",
            "International background is reported only through proxies (education/work location). Visa or "
            "citizenship status is never inferred.",
            f"Every bucket with fewer than {config.privacy.k_anonymity} people is suppressed (including "
            "cross-filter intersections and views that differ by fewer than k people).",
            "Confidence intervals: Wilson 95% intervals for proportions, bootstrap intervals for medians. They "
            "cover sampling error only, not selection bias.",
        ],
        "no_sensitive_inference": "No inference of race, religion, age, health or other sensitive attributes.",
    }


def trends(
    normalized: list[NormalizedProfile],
    query: StudyQuery,
    roles: list[Any],
    target: str,
    windows: list[int],
    sampled_ids: set[str],
    ctx: EngineContext,
    config: AppConfig,
    enriched: dict[str, EnrichedProfile],
) -> dict[str, Any]:
    """Compare 30/60/90-day (configurable) windows on the same run's data."""
    k = ctx.k
    per_window: list[dict[str, Any]] = []
    ns: list[int] = []
    subsets: list[list[AnalysisRecord]] = []
    for w in sorted(windows):
        q = query.model_copy(update={"joined_within_days": w, "joined_from": None, "joined_to": None})
        passed = [p for p in normalized if evaluate(p, q, roles, target).passed]
        recs, _ = make_records(passed, enriched, query, config)
        ns.append(len(recs))
        subsets.append(recs)
    for i, w in enumerate(sorted(windows)):
        larger = [ns[i + 1]] if i + 1 < len(ns) else []
        ok, reason = population_ok(ns[i], k, larger)
        if not ok:
            per_window.append({"window_days": w, "suppressed": True, "reason": reason, "n": None})
            continue
        recs = subsets[i]
        skills: Counter[str] = Counter(s for r in recs for s in set(r.skills))
        per_window.append(
            {
                "window_days": w,
                "suppressed": False,
                "n": len(recs),
                "per_30_days": round(len(recs) * 30 / w, 1),
                "roles": distribution(
                    "roles", Counter(r.role for r in recs), len(recs), k, partition=True
                ).model_dump(),
                "top_skills": distribution("top_skills", skills, len(recs), k, partition=False, top=10).model_dump(),
                "new_grad_pct": distribution(
                    "hire_type", Counter(r.hire_type for r in recs), len(recs), k, partition=True
                ).model_dump(),
            }
        )
    # weekly join histogram over the largest window
    biggest = subsets[-1] if subsets else []
    end = query.ref_date()
    weeks: Counter[str] = Counter()
    for r in biggest:
        if r.start_date:
            wk = (end - r.start_date).days // 7
            weeks[f"{wk * 7}-{wk * 7 + 6}d ago"] = weeks.get(f"{wk * 7}-{wk * 7 + 6}d ago", 0) + 1
    weekly = distribution("joins_by_week", weeks, len(biggest), k, partition=True, with_ci=False).model_dump()
    weekly["buckets"].sort(key=lambda b: int(b["key"].split("-")[0]))
    return {
        "windows": per_window,
        "joins_by_week": weekly,
        "note": "Windows are nested (30 ⊂ 60 ⊂ 90). per_30_days normalizes volume.",
    }


def job_postings_section(
    session: Session, run_id: int, records: list[AnalysisRecord], k: int, country: str
) -> dict[str, Any] | None:
    rows = session.query(JobPostingRow).filter_by(run_id=run_id).all()
    if not rows:
        return None
    in_country = [r for r in rows if r.country in (None, country)]
    roles = Counter(r.canonical_role or "unrecognized" for r in in_country)
    levels = Counter(r.level or "unknown" for r in in_country)
    skills: Counter[str] = Counter(s for r in in_country for s in set(r.skills or []))
    total = len(in_country)

    def plain(name: str, c: Counter[str], top: int | None = None) -> dict[str, Any]:
        # Job postings are not personal data, so no k-suppression; k=1 keeps the same shape.
        return distribution(name, c, total, 1, partition=top is None, top=top, with_ci=False).model_dump()

    prof_skills: Counter[str] = Counter(s for r in records for s in set(r.skills))
    top_p = {s for s, _ in prof_skills.most_common(15)}
    top_j = {s for s, _ in skills.most_common(15)}
    jacc = len(top_p & top_j) / len(top_p | top_j) if (top_p | top_j) else None
    prof_roles = Counter(r.role for r in records)
    role_cmp = []
    for role, jn in roles.most_common(10):
        pn = prof_roles.get(role, 0)
        role_cmp.append(
            {
                "role": role,
                "postings_pct": round(100 * jn / total, 1),
                "joiners_pct": round(100 * pn / len(records), 1) if records and pn >= k else None,
            }
        )
    return {
        "total_postings": total,
        "all_countries": len(rows),
        "by_role": plain("posting_roles", roles),
        "by_level": plain("posting_levels", levels),
        "top_required_skills": plain("posting_skills", skills, top=20),
        "cross_check": {
            "top15_skill_overlap_jaccard": round(jacc, 2) if jacc is not None else None,
            "shared_top_skills": sorted(top_p & top_j),
            "role_mix": role_cmp,
            "note": "Compares what postings ask for with what recent joiners list. Postings are a leading "
            "indicator; joiner profiles lag.",
        },
    }


def build_and_store_reports(
    runner: StudyRunner,
    run_id: int,
    query: StudyQuery,
    sampled: list[NormalizedProfile],
    enriched: dict[str, EnrichedProfile],
    counts: StageCounts,
    cost: RunCost,
    normalized: list[NormalizedProfile],
) -> dict[str, Any]:
    config = runner.config
    k = config.privacy.k_anonymity
    records, est_on = make_records(sampled, enriched, query, config)
    sampled_ids = {p.anon_id for p in sampled}
    baseline = [p.skills for p in normalized if p.anon_id not in sampled_ids]
    ctx = EngineContext(k=k, query=query, baseline_skill_docs=baseline, name_estimator_enabled=est_on)
    roles = query.expanded_roles or runner.resolve_roles(query)[0]
    target = runner.norm.companies.match(query.company).canonical or query.company

    base = view(records, {}, ctx, full=True)
    with runner.sm() as s:
        base["coverage"] = coverage(records, k, counts.resolved_persons)
        base["trends"] = trends(
            normalized, query, roles, target, config.pipeline.trend_windows, sampled_ids, ctx, config, enriched
        )
        base["job_postings"] = job_postings_section(s, run_id, records, k, query.country)
        base["methodology"] = methodology(
            query,
            config,
            counts,
            est_on,
            estimator_enabled(config, query) and not est_on,
            sorted({src for r in records for src in r.sources}),
        )
        base["dimensions"] = dimension_options(records, k)
        base["dimension_labels"] = DIMENSION_LABELS
        base["run_id"] = run_id
        base["schema_version"] = SCHEMA_VERSION
        base["cost"] = cost.model_dump()
        base["counts"] = counts.model_dump()

        s.query(AggregateRow).filter_by(run_id=run_id).delete()
        s.add(AggregateRow(run_id=run_id, filter_key="", payload=base))
        dims = list(DIMENSIONS)
        values = {d: sorted({DIMENSIONS[d](r) for r in records}) for d in dims}
        for d in dims:
            for v in values[d]:
                rep = view(records, {d: v}, ctx, full=True)
                if not rep["suppressed"]:
                    s.add(AggregateRow(run_id=run_id, filter_key=filter_key({d: v}), payload=rep))
        for d1, d2 in combinations(dims, 2):
            for v1 in values[d1]:
                for v2 in values[d2]:
                    f = {d1: v1, d2: v2}
                    sub_n = len(filter_records(records, f))
                    if sub_n < k:
                        continue
                    rep = view(records, f, ctx, full=False)
                    if not rep["suppressed"]:
                        s.add(AggregateRow(run_id=run_id, filter_key=filter_key(f), payload=rep))
        s.commit()
    return base


# ------------------------------------------------------------------------- serving
def load_records_for_run(sm: sessionmaker[Session], run_id: int, config: AppConfig) -> list[AnalysisRecord] | None:
    """Rebuild analysis records from TTL-retained rows (``None`` if purged)."""
    with sm() as s:
        run = s.get(Run, run_id)
        if run is None or run.purged_at is not None:
            return None
        rows = s.query(NormalizedProfileRow).filter_by(run_id=run_id, passed_filter=True).all()
        if not rows:
            return None
        enr = {
            r.anon_id: EnrichedProfile.model_validate(r.data)
            for r in s.query(EnrichedProfileRow).filter_by(run_id=run_id).all()
        }
        query = StudyQuery.model_validate(run.query_json)
    profiles = [NormalizedProfile.model_validate(r.data) for r in rows]
    recs, _ = make_records(profiles, enr, query, config)
    return recs


def get_report(sm: sessionmaker[Session], run_id: int, filters: dict[str, str], config: AppConfig) -> dict[str, Any]:
    """Serve a report for any filter selection: precomputed if available, else on the fly, else suppressed."""
    bad = [d for d in filters if d not in DIMENSIONS]
    if bad:
        raise KeyError(f"unknown filter dimension(s): {bad}")
    key = filter_key(filters)
    with sm() as s:
        base_row = s.query(AggregateRow).filter_by(run_id=run_id, filter_key="").one_or_none()
        if base_row is None:
            raise LookupError("no aggregates for this run")
        base = base_row.payload
        if not filters:
            return base
        row = s.query(AggregateRow).filter_by(run_id=run_id, filter_key=key).one_or_none()
        query = StudyQuery.model_validate(s.get(Run, run_id).query_json)  # type: ignore[union-attr]
    shared = {k: base.get(k) for k in ("methodology", "dimensions", "dimension_labels", "run_id", "schema_version")}
    if row is not None:
        return row.payload | shared | {"source": "precomputed"}
    if len(filters) <= 2:
        # precomputed set is exhaustive for ≤2 filters, so absence means suppressed
        return {
            "filters": filters,
            "suppressed": True,
            "n": None,
            "sections": {},
            "suppression_reason": f"fewer than {config.privacy.k_anonymity} people match these filters",
        } | shared
    recs = load_records_for_run(sm, run_id, config)
    if recs is None:
        return {
            "filters": filters,
            "suppressed": True,
            "n": None,
            "sections": {},
            "suppression_reason": "raw data purged; only combinations of up to 2 filters are retained",
        } | shared
    ctx = EngineContext(
        k=config.privacy.k_anonymity,
        query=query,
        name_estimator_enabled=estimator_enabled(config, query) and _name_table() is not None,
    )
    return view(recs, filters, ctx, full=False) | shared | {"source": "on_the_fly"}
