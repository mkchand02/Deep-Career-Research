"""Privacy invariants over every stored aggregate view."""

from __future__ import annotations

import json

from dcr.analytics.kanon import check_report_cells
from dcr.analytics.store import get_report
from dcr.db.models import AggregateRow
from dcr.models import SourceSpec, StudyQuery
from dcr.pipeline.runner import StudyRunner
from dcr.synth.generator import SyntheticGenerator

ROLES = ["SDE I", "SDE II", "SDE III", "Cloud Engineer", "AI Engineer", "Forward Deployed Engineer", "Data Engineer"]


async def test_all_views_k_anonymous_and_pii_free(app_config, secrets, session_factory, ref_date) -> None:  # type: ignore[no-untyped-def]
    q = StudyQuery(
        company="Google",
        country="US",
        joined_within_days=90,
        exp_min=0,
        exp_max=3,
        roles=ROLES,
        reference_date=ref_date,
        sample_cap=10_000,
        sources=[
            SourceSpec(name="synthetic", params={"n": 1500, "seed": 3}),
            SourceSpec(name="synthetic_jobs", params={"n": 1500, "seed": 3}),
        ],
    )
    out = await StudyRunner(app_config, secrets, session_factory).run(q)
    assert out.report is not None and not out.report["suppressed"]
    ds = SyntheticGenerator(seed=3, reference_date=ref_date).generate(1500)
    names = {r["full_name"] for r in ds.records} | {r["full_name"].split()[-1] for r in ds.records}
    handles = {r["github_username"] for r in ds.records if r["github_username"]}
    surnames = {n for n in names if len(n) > 6}  # avoid tiny tokens matching skill names
    with session_factory() as s:
        rows = s.query(AggregateRow).filter_by(run_id=out.run_id).all()
    assert len(rows) > 10
    for row in rows:
        payload = row.payload
        assert check_report_cells(payload.get("sections", {}), 5) == [], row.filter_key
        blob = json.dumps(payload)
        assert "github.com/" not in blob and "https://" not in blob
        for h in list(handles)[:300]:
            assert h not in blob, (row.filter_key, h)
        for nm in list(surnames)[:300]:
            assert f'"{nm}"' not in blob and f"{nm} " not in blob, (row.filter_key, nm)
    # filtered views
    rep = get_report(session_factory, out.run_id, {"role": "software_engineer"}, app_config)
    assert rep["n"] and rep["n"] >= 5
    small = get_report(session_factory, out.run_id, {"role": "software_engineer", "level": "staff"}, app_config)
    assert small["suppressed"]
    three = get_report(
        session_factory,
        out.run_id,
        {"role": "software_engineer", "level": "entry", "edu_origin": "domestic"},
        app_config,
    )
    assert three.get("source") == "on_the_fly" or three["suppressed"]
    if not three["suppressed"]:
        assert check_report_cells(three["sections"], 5) == []
