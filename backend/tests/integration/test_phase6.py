from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, timedelta

import pytest

from dcr.analytics.compare import compare_runs, two_prop_p
from dcr.analytics.store import get_report
from dcr.db.models import AggregateRow, NormalizedProfileRow, RawRecord, Run
from dcr.exports.report import ExportPrivacyError, charts_zip, to_csv, to_html, to_json, to_pdf
from dcr.models import SourceSpec, StudyQuery
from dcr.pipeline.retention import purge_expired
from dcr.pipeline.runner import StudyRunner
from dcr.timeutil import utcnow

ROLES = ["SDE II", "AI Engineer", "Data Engineer", "Cloud Engineer"]


def _q(ref: date) -> StudyQuery:
    return StudyQuery(
        company="Google",
        country="US",
        joined_within_days=60,
        roles=ROLES,
        reference_date=ref,
        sample_cap=5000,
        sources=[SourceSpec(name="synthetic", params={"n": 1200, "seed": 1, "vary_by_date": "1"})],
    )


@pytest.fixture
async def two_runs(app_config, secrets, session_factory, ref_date):  # type: ignore[no-untyped-def]
    r = StudyRunner(app_config, secrets, session_factory)
    a = await r.run(_q(ref_date - timedelta(days=60)))
    b = await r.run(_q(ref_date))
    return a.run_id, b.run_id


async def test_compare_and_significance(two_runs, session_factory, app_config) -> None:  # type: ignore[no-untyped-def]
    a, b = two_runs
    out = compare_runs(session_factory, [a, b], {}, app_config)
    assert [h["run_id"] for h in out["headline"]] == [a, b]
    roles = next(m for m in out["metrics"] if m["label"] == "Roles")
    assert roles["deltas"] and all(d["p_value"] is None or 0 <= d["p_value"] <= 1 for d in roles["deltas"])
    assert two_prop_p(50, 100, 50, 100) == pytest.approx(1.0)
    assert two_prop_p(80, 100, 20, 100) < 1e-6  # type: ignore[operator]
    with pytest.raises(ValueError):
        compare_runs(session_factory, [a], {}, app_config)


async def test_exports_are_aggregate_and_k_safe(two_runs, session_factory, app_config) -> None:  # type: ignore[no-untyped-def]
    _, b = two_runs
    rep = get_report(session_factory, b, {}, app_config)
    rows = list(csv.DictReader(io.StringIO(to_csv(rep, 5))))
    assert rows and all(r["n"] in ("", None) or int(r["n"]) >= 5 for r in rows)
    html = to_html(rep, 5, "now")
    assert "Methodology" in html and "data:image/png;base64" in html
    assert to_pdf(rep, 5)[:4] == b"%PDF"
    with zipfile.ZipFile(io.BytesIO(charts_zip(rep, 5))) as zf:
        assert "overview_roles.png" in zf.namelist()
    to_json(rep, 5)
    bad = {"sections": {"x": {"name": "x", "base_n": 10, "buckets": [{"key": "a", "n": 2, "pct": 20}]}}}
    with pytest.raises(ExportPrivacyError):
        to_csv(bad, 5)


async def test_ttl_purge_keeps_aggregates(two_runs, session_factory, app_config) -> None:  # type: ignore[no-untyped-def]
    _, b = two_runs
    dry = purge_expired(session_factory, app_config, dry_run=True)
    assert dry["raw_records"] == 0  # nothing expired yet
    future = utcnow() + timedelta(days=app_config.privacy.raw_ttl_days + 1)
    counts = purge_expired(session_factory, app_config, now=future)
    assert counts["raw_records"] > 0 and counts["normalized_profiles"] > 0
    with session_factory() as s:
        assert s.query(RawRecord).count() == 0 and s.query(NormalizedProfileRow).count() == 0
        assert s.query(AggregateRow).filter_by(run_id=b).count() > 1
        assert s.get(Run, b).purged_at is not None
    # aggregates still served; deep cross-filters no longer computable
    assert get_report(session_factory, b, {}, app_config)["n"]
    three = get_report(
        session_factory, b, {"role": "software_engineer", "level": "entry", "hire_type": "lateral"}, app_config
    )
    assert three["suppressed"] and "purged" in three["suppression_reason"]


async def test_force_purge_single_run(two_runs, session_factory, app_config) -> None:  # type: ignore[no-untyped-def]
    a, b = two_runs
    purge_expired(session_factory, app_config, run_id=a, force=True)
    with session_factory() as s:
        assert s.query(RawRecord).filter_by(run_id=a).count() == 0
        assert s.query(RawRecord).filter_by(run_id=b).count() > 0
