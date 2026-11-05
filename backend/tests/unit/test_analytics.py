from __future__ import annotations

import json
from pathlib import Path

import pytest

from dcr.analytics import demographics
from dcr.analytics.engine import EngineContext, exp_band, view
from dcr.analytics.kanon import check_report_cells, distribution, population_ok, proportion
from dcr.analytics.stats import bootstrap_median_ci, chapman_estimate, wilson
from dcr.models import AnalysisRecord, StudyQuery

FIX = Path(__file__).parent.parent / "fixtures"


def test_wilson_bounds() -> None:
    lo, hi = wilson(50, 100)
    assert 0.40 < lo < 0.41 and 0.59 < hi < 0.60
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == pytest.approx(1.0)
    assert wilson(0, 0) == (0.0, 1.0)


def test_chapman_and_bootstrap() -> None:
    est, se = chapman_estimate(100, 80, 40)  # type: ignore[misc]
    assert 195 < est < 202 and se > 0
    assert chapman_estimate(0, 5, 0) is None
    lo, hi = bootstrap_median_ci([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert lo <= 5.5 <= hi


def test_primary_suppression_multilabel() -> None:
    d = distribution("skills", {"Python": 40, "Go": 4, "Rust": 1}, 50, 5, partition=False)
    assert [b.key for b in d.buckets] == ["Python"] and d.suppressed_buckets == 2


def test_complementary_suppression_partition() -> None:
    # Only one cell < k: the next smallest must also be hidden so it can't be derived as total - visible.
    d = distribution("degree", {"bachelor": 30, "master": 15, "phd": 3}, 48, 5, partition=True)
    keys = {b.key for b in d.buckets}
    assert keys == {"bachelor"} and d.suppressed_buckets == 2
    # Two small cells: no extra suppression needed
    d2 = distribution("degree", {"bachelor": 30, "master": 15, "phd": 3, "mba": 2}, 50, 5, partition=True)
    assert {b.key for b in d2.buckets} == {"bachelor", "master"}


def test_population_and_differencing_rules() -> None:
    assert not population_ok(4, 5)[0]
    assert population_ok(5, 5)[0]
    assert not population_ok(20, 5, [23])[0]  # differs from parent by 3 -> differencing risk
    assert population_ok(20, 5, [20, 40])[0]


def test_proportion_requires_both_groups_ge_k() -> None:
    assert proportion("x", 3, 50, 5).suppressed
    assert proportion("x", 47, 50, 5).suppressed
    p = proportion("x", 20, 50, 5)
    assert not p.suppressed and p.pct == 40.0 and p.ci_low < 40 < p.ci_high


def test_exp_band() -> None:
    assert [exp_band(v) for v in (0.2, 1.0, 2.99, 3.5, 5.9, 7, None)] == [
        "<1 yr",
        "1–2 yrs",
        "2–3 yrs",
        "3–4 yrs",
        "4–6 yrs",
        "6+ yrs",
        "unknown",
    ]


def _rec(**kw) -> AnalysisRecord:  # type: ignore[no-untyped-def]
    base = dict(
        role="software_engineer",
        level="entry",
        exp_years=1.0,
        exp_band="1–2 yrs",
        source="s",
        sources=["s"],
        start_date=None,
        edu_origin="domestic",
        highest_degree="bachelor",
        fields=["CS"],
        schools=["MIT"],
        skills=["Python"],
        project_tags=[],
        project_kinds=[],
        project_stacks=[],
        project_focus=[],
        has_github=False,
        github_stars=[],
        github_commits=[],
        prior_companies=[],
        prior_company_types=[],
        hire_type="new_grad",
        interned_at_target=False,
        worked_abroad=False,
    )
    base.update(kw)
    return AnalysisRecord(**base)


def test_view_suppresses_small_and_differencing_intersections() -> None:
    q = StudyQuery(company="Google")
    ctx = EngineContext(k=5, query=q)
    recs = [_rec() for _ in range(20)] + [_rec(level="mid") for _ in range(3)] + [_rec(role="sre") for _ in range(8)]
    assert view(recs, {"level": "mid"}, ctx)["suppressed"]  # n=3
    # role=software_engineer & level=entry (20) differs from role=software_engineer (23) by 3 -> suppressed
    v = view(recs, {"role": "software_engineer", "level": "entry"}, ctx)
    assert v["suppressed"] and "differencing" in v["suppression_reason"]
    ok = view(recs, {"role": "sre"}, ctx)
    assert not ok["suppressed"] and ok["n"] == 8
    assert check_report_cells(ok["sections"], 5) == []


def test_pronoun_grouping_and_distribution() -> None:
    assert demographics.pronoun_group("She/Her") == "she"
    assert demographics.pronoun_group("he/they") == "he"
    assert demographics.pronoun_group("they/them") == "they"
    assert demographics.pronoun_group(None) is None
    recs = [_rec(pronoun_group="she")] * 6 + [_rec()] * 30
    d = demographics.pronoun_distribution(recs, 5)
    assert {b["key"] for b in d["buckets"]} == {"she/her (self-stated)", "not stated"}


def test_name_estimator_aggregate_with_ci_and_unknown() -> None:
    table = demographics.NameGenderTable.load(FIX / "names.csv")
    assert table is not None
    assert table.estimate("Mary", 50).p_female > 0.99  # type: ignore[union-attr]
    assert table.estimate("Zzyzx", 50) is None
    recs = []
    for name in ["Mary"] * 10 + ["James"] * 10 + ["Unknownname"] * 6:
        e = table.estimate(name, 50)
        recs.append(_rec(p_female_name=e.p_female if e else None, name_estimable=e is not None))
    out = demographics.name_based_estimate(recs, 5)
    assert not out["suppressed"]
    assert 45 < out["pct_women_est"] < 55 and out["ci_low"] < out["pct_women_est"] < out["ci_high"]
    assert out["unknown_n"] == 6 and "ESTIMATE" in out["label"]
    # never per-person: output has no list of individuals
    assert not any(isinstance(v, list) for v in out.values())
    small = demographics.name_based_estimate(recs[:3], 5)
    assert small["suppressed"]


def test_build_ssa_table(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("yob1999.txt", "Mary,F,100\nJames,M,90\nJordan,F,10\nJordan,M,20\n")
        zf.writestr("yob1950.txt", "Mary,F,9999\n")
        zf.writestr("NationalReadMe.pdf", "x")
    n = demographics.build_ssa_table(buf.getvalue(), tmp_path / "t.csv", min_year=1960)
    assert n == 3
    t = demographics.NameGenderTable.load(tmp_path / "t.csv")
    assert t is not None and t.rows["mary"] == (100, 0) and t.rows["jordan"] == (10, 20)


def test_international_proxies_never_mention_visa_as_measure() -> None:
    recs = [_rec(edu_origin="foreign", worked_abroad=True)] * 10 + [_rec()] * 20
    out = demographics.international_proxies(recs, "US", 5)
    assert out["highest_degree_outside_country"]["pct"] == pytest.approx(33.3, abs=0.1)
    assert "visa" in out["disclaimer"].lower()
    assert "Proxy" in out["highest_degree_outside_country"]["note"]
    json.dumps(out)
