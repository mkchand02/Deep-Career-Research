"""Enrichment tests with a fake Anthropic client (no network, no spend)."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx

from dcr.config import AppConfig
from dcr.enrichment.heuristic import heuristic_enrich, tag_project
from dcr.enrichment.llm import ClaudeClient
from dcr.enrichment.schemas import EnrichmentOutput, LLMPageProfile, strict_json_schema
from dcr.enrichment.service import Enricher, minimized_input
from dcr.models import NormalizedEducation, NormalizedProfile, Project, RunCost, StageCounts
from dcr.timeutil import utcnow

GOOD = {
    "projects": [
        {
            "name": "Raft KV",
            "description": "A distributed key-value store.",
            "tags": ["distributed-systems"],
            "tech_stack": ["golang", "gRPC"],
            "focus": "systems",
            "kind": "personal",
        }
    ],
    "experience": {
        "total_years": 1.5,
        "prior_companies": ["AWS"],
        "prior_company_types": ["big_tech"],
        "is_new_grad": False,
    },
    "education": [
        {
            "institution": "IIT Bombay",
            "degree_level": "bachelor",
            "field": "Computer Science",
            "institution_country": "IN",
        }
    ],
    "skills": ["k8s", "Go"],
}


class FakeMessages:
    def __init__(self, script: list[Any], log: list[dict[str, Any]]) -> None:
        self.script = script
        self.log = log

    async def create(self, **kw: Any) -> Any:
        self.log.append(kw)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def resp(payload: Any, stop: str = "end_turn", model: str = "claude-opus-5-5") -> Any:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop,
        model=model,
        usage=SimpleNamespace(input_tokens=1000, output_tokens=500, cache_read_input_tokens=0),
    )


def fake_client(beta_script: list[Any], plain_script: list[Any] | None = None) -> tuple[Any, list[dict[str, Any]]]:
    log: list[dict[str, Any]] = []
    c = SimpleNamespace(
        beta=SimpleNamespace(messages=FakeMessages(beta_script, log)),
        messages=FakeMessages(plain_script or beta_script, log),
    )
    return c, log


def profile(i: int = 0) -> NormalizedProfile:
    return NormalizedProfile(
        anon_id=f"P{i}",
        sources=["s"],
        first_name="Priya",
        current_title="Software Engineer II",
        current_company="Google",
        canonical_role="software_engineer",
        exp_years=1.6,
        skills=["Python"],
        education=[NormalizedEducation(institution="Indian Institute of Technology Bombay")],
        projects=[
            Project(
                name="Raft KV",
                description="Priya built this; see https://github.com/priya/raft",
                tech_stack=["Go"],
                stars=12,
                commits=80,
            )
        ],
        keywords_text=f"Priya ({i}) loves Go. Mail priya@example.com or visit https://priya.dev",
    )


def _runs(sm) -> None:  # type: ignore[no-untyped-def]
    from dcr.db.models import Run

    with sm() as s:
        for i in (1, 2):
            s.add(Run(id=i, query_json={}))
        s.commit()


def cfg(budget: float = 5.0) -> AppConfig:
    return AppConfig.model_validate(
        {
            "enrichment": {
                "budget_usd": budget,
                "concurrency": 2,
                "pricing": {"claude-opus-5-5": {"input_per_mtok": 4.0, "output_per_mtok": 20.0}},
            }
        }
    )


def test_minimized_input_has_no_identifiers() -> None:
    blob = json.dumps(minimized_input(profile()))
    assert "Priya" not in blob and "priya" not in blob.lower().replace("[name]", "")
    assert "http" not in blob and "@" not in blob


def test_strict_schema_closes_objects() -> None:
    sch = strict_json_schema(EnrichmentOutput)
    assert sch["additionalProperties"] is False
    defs = sch["$defs"]
    assert all(d.get("additionalProperties") is False for d in defs.values() if d.get("type") == "object")
    strict_json_schema(LLMPageProfile)


async def test_llm_enrichment_validates_caches_and_costs(session_factory) -> None:  # type: ignore[no-untyped-def]
    _runs(session_factory)
    c, log = fake_client([resp(GOOD)])
    config = cfg()
    enr = Enricher(config, session_factory, ClaudeClient(config.enrichment, client=c))
    cost, counts = RunCost(budget_usd=5.0), StageCounts()
    out = await enr.enrich_many(1, [profile(0), profile(1)], cost, counts, utcnow() + timedelta(days=1))
    e = out["P0"]
    assert e.method == "llm"
    assert e.projects[0].tech_stack == ["Go", "gRPC"] and e.projects[0].stars == 12
    assert e.experience.prior_companies == ["Amazon"]
    assert e.education[0].country == "IN"
    assert "Kubernetes" in e.extra_skills
    assert cost.llm_calls == 2 and abs(cost.actual_usd - 2 * (1000 * 4 + 500 * 20) / 1e6) < 1e-9
    # request shape: structured output + server-side fallback
    assert log[0]["fallbacks"] == "default" and log[0]["output_config"]["format"]["type"] == "json_schema"
    assert log[0]["model"] == "claude-opus-5-5"
    # second run: identical content hits the cache, no new calls
    cost2, counts2 = RunCost(budget_usd=5.0), StageCounts()
    out2 = await enr.enrich_many(2, [profile(0)], cost2, counts2, utcnow() + timedelta(days=1))
    assert out2["P0"].method == "cache" and counts2.enrichment_cached == 1 and cost2.llm_calls == 0


async def test_budget_cap_falls_back_to_heuristics(session_factory) -> None:  # type: ignore[no-untyped-def]
    _runs(session_factory)
    c, _ = fake_client([resp(GOOD)])
    config = cfg(budget=0.02)  # ~1 call at $0.014
    enr = Enricher(config, session_factory, ClaudeClient(config.enrichment, client=c))
    cost = RunCost(budget_usd=0.02)
    out = await enr.enrich_many(1, [profile(i) for i in range(5)], cost, StageCounts(), utcnow())
    methods = sorted(e.method for e in out.values())
    assert methods.count("llm") == 1 and methods.count("heuristic") == 4
    assert cost.budget_exhausted and cost.actual_usd <= 0.02


async def test_refusal_invalid_json_and_fallback_rejection(session_factory) -> None:  # type: ignore[no-untyped-def]
    _runs(session_factory)
    config = cfg()
    c, _ = fake_client([resp(GOOD, stop="refusal")])
    enr = Enricher(config, session_factory, ClaudeClient(config.enrichment, client=c))
    assert (await enr.enrich_many(1, [profile()], RunCost(budget_usd=5), StageCounts(), utcnow()))[
        "P0"
    ].method == "heuristic"
    c2, _ = fake_client([resp("{not json")])
    enr2 = Enricher(config, session_factory, ClaudeClient(config.enrichment, client=c2))
    assert (await enr2.enrich_many(1, [profile(9)], RunCost(budget_usd=5), StageCounts(), utcnow()))[
        "P9"
    ].method == "heuristic"
    bad = anthropic.BadRequestError(
        "fallbacks not supported for this model",
        response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
        body=None,
    )
    c3, log3 = fake_client([bad], [resp(GOOD)])
    client3 = ClaudeClient(config.enrichment, client=c3)
    enr3 = Enricher(config, session_factory, client3)
    out = await enr3.enrich_many(1, [profile(7)], RunCost(budget_usd=5), StageCounts(), utcnow())
    assert out["P7"].method == "llm" and not client3._fallbacks_supported
    assert "fallbacks" not in log3[-1]


def test_estimate_and_heuristic(session_factory) -> None:  # type: ignore[no-untyped-def]
    config = cfg()
    c, _ = fake_client([resp(GOOD)])
    est = Enricher(config, session_factory, ClaudeClient(config.enrichment, client=c)).estimate(100)
    assert est["llm_calls"] == 100 and 1.0 < est["estimated_usd"] < 3.0
    assert Enricher(config, session_factory, None).estimate(100)["estimated_usd"] == 0.0
    h = heuristic_enrich(profile())
    assert h.method == "heuristic" and "distributed-systems" in h.projects[0].tags
    assert tag_project(Project(name="LLM-powered document Q&A", tech_stack=["LangChain"])).focus == "machine_learning"
