"""End-to-end pipeline on synthetic fixtures, checked against generator ground truth."""

from __future__ import annotations

import math
from datetime import date

from dcr.db.models import FilterDecisionRow, NormalizedProfileRow, Run
from dcr.models import SourceSpec, StudyQuery
from dcr.pipeline.runner import StudyRunner
from dcr.synth.generator import SyntheticGenerator

ROLES = ["SDE I", "SDE II", "SDE III", "Cloud Engineer", "AI Engineer", "Forward Deployed Engineer", "Data Engineer"]


def _query(ref: date, n: int = 600) -> StudyQuery:
    return StudyQuery(
        company="Google",
        country="US",
        joined_within_days=60,
        exp_min=0,
        exp_max=3,
        roles=ROLES,
        include_similar_roles=False,
        reference_date=ref,
        sample_cap=10_000,
        sources=[SourceSpec(name="synthetic", params={"n": n, "seed": 11})],
    )


async def test_pipeline_matches_ground_truth(app_config, secrets, session_factory, ref_date) -> None:  # type: ignore[no-untyped-def]
    runner = StudyRunner(app_config, secrets, session_factory)
    out = await runner.run(_query(ref_date))
    assert out.counts.fetched > 600 and out.counts.resolved_persons <= out.counts.fetched
    # ground truth
    ds = SyntheticGenerator(seed=11, reference_date=ref_date).generate(600)
    wanted_roles = {"software_engineer", "cloud_engineer", "ai_engineer", "forward_deployed_engineer", "data_engineer"}
    expected = 0
    for t in ds.truth:
        days = (ref_date - date.fromisoformat(t["start_date"])).days
        if (
            t["company"] == "Google"
            and t["country"] == "US"
            and 0 <= days <= 60
            and math.floor(t["exp_years"]) <= 3
            and t["role"] in wanted_roles
        ):
            expected += 1
    # Allow for level-ladder/experience boundary effects
    assert abs(out.counts.passed_filter - expected) <= max(6, 0.15 * expected), (out.counts.passed_filter, expected)
    with session_factory() as s:
        run = s.get(Run, out.run_id)
        assert run.status == "completed"
        assert s.query(NormalizedProfileRow).filter_by(run_id=out.run_id).count() == out.counts.resolved_persons
        rows = s.query(FilterDecisionRow).filter_by(run_id=out.run_id).all()
        assert len(rows) == out.counts.resolved_persons
        assert all(r.expires_at > run.created_at for r in rows)
