from __future__ import annotations

from datetime import date

from dcr.adapters.manual_import import record_to_profile
from dcr.models import CheckResult, LevelBand, NormalizedProfile, Position, RawProfile, StudyQuery
from dcr.pipeline.filter import evaluate, reason_summary, sample
from dcr.pipeline.resolve import merge_profiles, name_similarity, normalize_link, resolve
from dcr.roles.expansion import expand_roles


def test_normalize_link() -> None:
    assert normalize_link("https://www.github.com/JaneDoe/") == "github:janedoe"
    assert normalize_link("github.com/janedoe/repo") == "github:janedoe"
    assert normalize_link("https://janedoe.dev/") == "janedoe.dev"
    assert normalize_link("https://github.com") is None


def test_name_similarity() -> None:
    assert name_similarity("Jane Q. Doe", "Jane Doe") > 0.95
    assert name_similarity("JANE DOE", "jane doe") == 1.0
    assert name_similarity("Alex Smith", "Alexandra Smith") >= 0.9
    assert name_similarity("Jane Doe", "John Smith") == 0.0


def _p(name: str, company: str, gh: str | None = None, src: str = "a") -> RawProfile:
    return RawProfile(
        source=src,
        source_ref=name + src,
        full_name=name,
        github_username=gh,
        country="US",
        positions=[Position(title="SWE", company=company)],
    )


def test_resolve_strong_and_fuzzy_links() -> None:
    profs = [
        _p("Jane Doe", "Google", "jdoe"),
        _p("J. Doe", "Amazon", "jdoe", "b"),  # strong key
        _p("Ravi Kumar", "Google"),
        _p("Ravi K. Kumar", "Google LLC", src="b"),  # fuzzy + same company
        _p("Ravi Kumar", "Meta", src="c"),
    ]  # same name, different employer -> separate
    clusters = resolve(profs)
    groups = sorted(sorted(c.members) for c in clusters)
    assert [0, 1] in groups and [2, 3] in groups and [4] in groups
    merged = merge_profiles([profs[2], profs[3]])
    assert merged.full_name == "Ravi K. Kumar" or merged.full_name == "Ravi Kumar"


def test_resolution_accuracy_on_synthetic(synth_ds) -> None:  # type: ignore[no-untyped-def]
    profs = [record_to_profile(r, "manual", i) for i, r in enumerate(synth_ds.records)]
    clusters = resolve(profs)
    truth = synth_ds.truth_person_of_record
    # pairwise precision / recall
    pred_pairs, true_pairs = set(), set()
    for c in clusters:
        m = sorted(c.members)
        pred_pairs |= {(a, b) for i, a in enumerate(m) for b in m[i + 1 :]}
    by_person: dict[int, list[int]] = {}
    for idx, pid in enumerate(truth):
        by_person.setdefault(pid, []).append(idx)
    for m in by_person.values():
        true_pairs |= {(a, b) for i, a in enumerate(sorted(m)) for b in sorted(m)[i + 1 :]}
    tp = len(pred_pairs & true_pairs)
    precision = tp / max(len(pred_pairs), 1)
    recall = tp / max(len(true_pairs), 1)
    assert precision >= 0.97, precision
    assert recall >= 0.9, recall


def _np(**kw) -> NormalizedProfile:  # type: ignore[no-untyped-def]
    base = dict(
        anon_id="P1",
        sources=["a"],
        resolution_confidence=1.0,
        current_company="Google",
        canonical_role="software_engineer",
        level=LevelBand.ENTRY,
        role_confidence=1.0,
        company_confidence=1.0,
        country="US",
        location="Mountain View, CA",
        start_date=date(2026, 10, 1),
        exp_years=1.5,
    )
    base.update(kw)
    return NormalizedProfile(**base)


def test_filter_checks_and_reasons(ref_date) -> None:  # type: ignore[no-untyped-def]
    q = StudyQuery(
        company="Google",
        country="US",
        joined_within_days=60,
        exp_min=0,
        exp_max=3,
        reference_date=ref_date,
        regions=["CA"],
    )
    roles, _ = expand_roles(["SDE II"], company="Google", include_similar=False)  # Google II -> entry
    assert evaluate(_np(), q, roles, "Google").passed
    d = evaluate(
        _np(current_company="Amazon", start_date=date(2025, 1, 1), exp_years=3.9, country="IN"), q, roles, "Google"
    )
    res = {c.check: c.result for c in d.checks}
    assert not d.passed
    assert res["company"] == res["start_date"] == res["country"] == CheckResult.FAIL
    assert res["experience"] == CheckResult.PASS  # 3 completed years is within 0-3
    assert evaluate(_np(exp_years=4.0), q, roles, "Google").checks[3].result == CheckResult.FAIL
    assert not evaluate(_np(location="Austin, TX"), q, roles, "Google").passed
    assert not evaluate(_np(level=LevelBand.SENIOR), q, roles, "Google").passed
    # unknown start date: excluded by default, included with toggle
    assert not evaluate(_np(start_date=None), q, roles, "Google").passed
    q2 = q.model_copy(update={"include_unknown_start_date": True})
    assert evaluate(_np(start_date=None), q2, roles, "Google").passed
    # low confidence
    assert not evaluate(_np(resolution_confidence=0.4), q, roles, "Google").passed
    # reasons never contain names/urls
    for c in d.checks:
        assert "http" not in c.reason
    summary = reason_summary([d])
    assert summary["_primary_reason"] == {"company": 1}


def test_sample_is_deterministic_and_capped() -> None:
    people = [_np(anon_id=f"P{i}") for i in range(20)]
    a = sample(people, 5, "salt")
    assert len(a) == 5 and [p.anon_id for p in a] == [p.anon_id for p in sample(people, 5, "salt")]
    assert sample(people, 50) == people
