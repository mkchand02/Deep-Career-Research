from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

import pytest

from dcr.adapters.base import Candidate, SourceAdapter, SourceError, TokenBucket
from dcr.adapters.manual_import import csv_row_to_record, load_records
from dcr.adapters.registry import all_adapters, register_adapter
from dcr.config import AppConfig, PrivacyConfig
from dcr.models import RawProfile, SourceSpec, StudyQuery
from dcr.pipeline.discover import discover
from dcr.synth.generator import SyntheticGenerator


def test_k_anonymity_floor_cannot_be_lowered() -> None:
    assert PrivacyConfig(k_anonymity=2).k_anonymity == 5
    assert PrivacyConfig(k_anonymity=10).k_anonymity == 10
    assert AppConfig.model_validate({"privacy": {"k_anonymity": 1}}).privacy.k_anonymity == 5


def test_query_validation_and_window(ref_date) -> None:  # type: ignore[no-untyped-def]
    q = StudyQuery(company="Google", country="us", joined_within_days=30, reference_date=ref_date)
    start, end = q.window()
    assert q.country == "US"
    assert (end - start).days == 30 and end == ref_date
    with pytest.raises(ValueError):
        StudyQuery(company="X", exp_min=5, exp_max=1)


def test_generator_is_deterministic_and_synthetic(ref_date) -> None:  # type: ignore[no-untyped-def]
    a = SyntheticGenerator(seed=1, reference_date=ref_date).generate(50)
    b = SyntheticGenerator(seed=1, reference_date=ref_date).generate(50)
    assert a.records == b.records
    assert all(r["synthetic"] for r in a.records)
    assert len(a.records) >= 50  # duplicates added
    assert len(set(a.truth_person_of_record)) == 50
    assert any(r["source_hint"] == "web_sim" for r in a.records)
    # Every record must validate as a RawProfile
    for r in a.records:
        RawProfile(
            source="t",
            source_ref=r["source_ref"],
            **{k: v for k, v in r.items() if k in RawProfile.model_fields and k != "source_ref"},
        )


def test_manual_import_json_and_csv(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"profiles": [{"full_name": "Test Person", "skills": ["k8s"]}]}))
    assert load_records(p)[0]["full_name"] == "Test Person"
    row = {
        "full_name": "A B",
        "skills": "Python|Go",
        "current_title": "SWE II",
        "current_company": "Google",
        "current_start_date": "2026-10-01",
        "links": "https://github.com/ab",
    }
    rec = csv_row_to_record(row)
    assert rec["skills"] == ["Python", "Go"]
    assert rec["positions"][0]["company"] == "Google" and rec["positions"][0]["end_date"] is None
    csv_path = tmp_path / "x.csv"
    csv_path.write_text("full_name,skills,current_title,current_company\nA B,Python;Go,SWE,Google\n")
    assert load_records(csv_path)[0]["skills"] == ["Python", "Go"]
    with pytest.raises(SourceError):
        load_records(tmp_path / "missing.json")


def test_registry_has_builtin_adapters() -> None:
    names = set(all_adapters())
    assert {"manual", "synthetic", "synthetic_jobs"} <= names


@register_adapter
class _BrokenAdapter(SourceAdapter):
    name: ClassVar[str] = "_broken_test"

    async def search(self, query: StudyQuery, limit: int) -> list[Candidate]:
        return [Candidate(self.name, "1"), Candidate(self.name, "2")]

    async def fetch(self, candidate: Candidate) -> RawProfile | None:
        if candidate.ref == "1":
            raise SourceError("boom")
        return RawProfile(source=self.name, source_ref=candidate.ref)


async def test_discovery_isolates_source_failures(app_config, secrets, ref_date) -> None:  # type: ignore[no-untyped-def]
    q = StudyQuery(
        company="Google",
        reference_date=ref_date,
        sample_cap=20,
        sources=[
            SourceSpec(name="_broken_test"),
            SourceSpec(name="synthetic", params={"n": 30}),
            SourceSpec(name="does_not_exist"),
            SourceSpec(name="synthetic_jobs", params={"n": 30}),
        ],
    )
    res = await discover(q, app_config, secrets)
    assert len([p for p in res.profiles if p.source.startswith("synthetic")]) > 0
    assert any(p.source == "_broken_test" for p in res.profiles)
    stages = {(e["source"], e["stage"]) for e in res.errors}
    assert ("_broken_test", "fetch") in stages and ("does_not_exist", "init") in stages
    assert res.postings


async def test_token_bucket_limits_rate() -> None:
    import time

    b = TokenBucket(rate_per_minute=600, burst=1)  # 10/s
    t0 = time.monotonic()
    for _ in range(4):
        await b.acquire()
    assert time.monotonic() - t0 >= 0.25


def _unused(_: Any) -> None:  # keep asyncio import used for older pytest-asyncio
    asyncio.get_event_loop_policy()
