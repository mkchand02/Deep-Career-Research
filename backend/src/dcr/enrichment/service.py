"""Enrichment service: LLM (Claude) with content-hash cache, cost estimate, budget cap and heuristic fallback.

Data minimization: the LLM only sees what extraction needs (titles, companies, dates, education, skills,
project descriptions, headline/summary text). Names, emails and URLs are removed before sending.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime
from typing import Any

import structlog
from rich.console import Console
from sqlalchemy.orm import Session, sessionmaker

from dcr.config import AppConfig, Secrets
from dcr.db.models import EnrichedProfileRow, EnrichmentCache
from dcr.enrichment.heuristic import heuristic_enrich
from dcr.enrichment.llm import ClaudeClient, LLMError, price
from dcr.enrichment.schemas import SCHEMA_VERSION, EnrichmentOutput, RoleSuggestions
from dcr.models import (
    DegreeLevel,
    EnrichedProfile,
    ExperienceSummary,
    NormalizedEducation,
    NormalizedProfile,
    Project,
    RunCost,
    StageCounts,
    StudyQuery,
)
from dcr.pipeline.normalize.companies import default_company_normalizer
from dcr.pipeline.normalize.education import default_school_normalizer
from dcr.pipeline.normalize.skills import default_skill_normalizer
from dcr.timeutil import utcnow

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You extract structured career facts from a de-identified professional profile.
The output feeds aggregate statistics only (never per-person reports).

Rules:
- Use only facts present in the input. Do not guess or embellish; use null or empty lists when unknown.
- Projects: give a short neutral description without names or URLs. Tag the domain, list the tech stack,
  pick one focus category, and say whether the project is personal, academic or professional.
- Experience: count full-time years only, excluding internships. List prior employers (not the current one)
  and classify each as big_tech, enterprise, startup, finance, consulting, academia, government or other.
- Education: degree level, field, institution, and the institution's ISO country code when you are confident.
- Never infer gender, race, religion, age, health, nationality, citizenship or visa status."""

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"https?://\S+|www\.\S+")
EST_INPUT_TOKENS = 1400  # system prompt + schema + typical minimized profile
EST_OUTPUT_TOKENS = 700


def minimized_input(p: NormalizedProfile) -> dict[str, Any]:
    """The only profile content sent to the LLM (no names, links or exact contact details)."""

    def scrub(text: str | None) -> str:
        if not text:
            return ""
        t = _URL.sub("[link]", _EMAIL.sub("[email]", text))
        if p.first_name:
            t = re.sub(rf"\b{re.escape(p.first_name)}\b", "[name]", t, flags=re.I)
        return t

    return {
        "current": {"title": p.current_title, "company": p.current_company, "role_hint": p.canonical_role},
        "prior_companies": p.prior_companies,
        "experience_years_computed": p.exp_years,
        "education": [e.model_dump(mode="json", exclude={"institution_known"}) for e in p.education],
        "skills": p.skills,
        "projects": [
            {
                "name": scrub(pr.name),
                "description": scrub(pr.description),
                "tags": pr.tags,
                "tech_stack": pr.tech_stack,
                "kind": pr.kind,
            }
            for pr in p.projects
        ],
        "text": scrub(p.keywords_text),
    }


def content_hash(payload: dict[str, Any], model: str) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str) + "|" + SCHEMA_VERSION + "|" + model
    return hashlib.sha256(raw.encode()).hexdigest()


def to_enriched(anon_id: str, out: EnrichmentOutput, base: NormalizedProfile, method: str) -> EnrichedProfile:
    sn, cn, schools = default_skill_normalizer(), default_company_normalizer(), default_school_normalizer()
    projects: list[Project] = []
    for lp in out.projects:
        stars = next((pr.stars for pr in base.projects if pr.name.lower() == lp.name.lower()), None)
        commits = next((pr.commits for pr in base.projects if pr.name.lower() == lp.name.lower()), None)
        projects.append(
            Project(
                name=lp.name,
                description=lp.description,
                tags=list(lp.tags),
                tech_stack=sn.normalize(lp.tech_stack),
                focus=lp.focus,
                kind=lp.kind,
                stars=stars,
                commits=commits,
            )
        )
    edu = []
    for le in out.education:
        canon, country, known = schools.match(le.institution)
        edu.append(
            NormalizedEducation(
                institution=canon,
                institution_known=known,
                degree_level=DegreeLevel(le.degree_level),
                field=le.field,
                country=(le.institution_country or country or None),
            )
        )
    prior = [cn.match(c).canonical for c in out.experience.prior_companies]
    return EnrichedProfile(
        anon_id=anon_id,
        projects=projects,
        experience=ExperienceSummary(
            years=out.experience.total_years,
            prior_companies=prior,
            prior_company_types=list(out.experience.prior_company_types),
            is_new_grad=out.experience.is_new_grad,
        ),
        education=edu or base.education,
        extra_skills=[s for s in sn.normalize(out.skills) if s not in base.skills],
        method=method,  # type: ignore[arg-type]
    )


class Enricher:
    """Enriches sampled profiles. Uses Claude when configured and within budget, else heuristics."""

    def __init__(self, config: AppConfig, sm: sessionmaker[Session], client: ClaudeClient | None) -> None:
        self.config = config
        self.cfg = config.enrichment
        self.sm = sm
        self.client = client

    @property
    def uses_llm(self) -> bool:
        return self.client is not None and self.cfg.enabled

    def estimate(self, n_profiles: int) -> dict[str, Any]:
        if not self.uses_llm:
            return {
                "estimated_usd": 0.0,
                "llm_calls": 0,
                "model": None,
                "budget_usd": self.cfg.budget_usd,
                "note": "No ANTHROPIC_API_KEY (or enrichment disabled): heuristic enrichment, no LLM cost.",
            }
        per = price(self.cfg, self.cfg.model, EST_INPUT_TOKENS, EST_OUTPUT_TOKENS)
        est = per * n_profiles
        return {
            "estimated_usd": round(est, 4),
            "llm_calls": n_profiles,
            "model": self.cfg.model,
            "per_profile_usd": round(per, 5),
            "budget_usd": self.cfg.budget_usd,
            "will_hit_budget": est > self.cfg.budget_usd,
            "note": "Upper bound: assumes every capped profile passes the filters and none are cached. "
            "Profiles beyond the budget fall back to heuristic enrichment.",
        }

    def _cache_get(self, key: str) -> EnrichmentOutput | None:
        with self.sm() as s:
            row = s.get(EnrichmentCache, key)
            if row and (row.expires_at is None or row.expires_at > utcnow()):
                try:
                    return EnrichmentOutput.model_validate(row.output)
                except Exception:
                    return None
        return None

    def _cache_put(self, key: str, out: EnrichmentOutput, cost: float, expires: datetime) -> None:
        with self.sm() as s:
            s.merge(
                EnrichmentCache(
                    content_hash=key,
                    schema_version=SCHEMA_VERSION,
                    model=self.cfg.model,
                    output=out.model_dump(mode="json"),
                    cost_usd=cost,
                    expires_at=expires,
                )
            )
            s.commit()

    async def enrich_many(
        self, run_id: int, profiles: list[NormalizedProfile], cost: RunCost, counts: StageCounts, expires: datetime
    ) -> dict[str, EnrichedProfile]:
        results: dict[str, EnrichedProfile] = {}
        if self.uses_llm:
            cost.estimated_usd = self.estimate(len(profiles))["estimated_usd"]
        sem = asyncio.Semaphore(max(1, self.cfg.concurrency))
        lock = asyncio.Lock()
        est_per_call = price(self.cfg, self.cfg.model, EST_INPUT_TOKENS, EST_OUTPUT_TOKENS)

        async def one(p: NormalizedProfile) -> None:
            if not self.uses_llm:
                results[p.anon_id] = heuristic_enrich(p)
                return
            payload = minimized_input(p)
            key = content_hash(payload, self.cfg.model)
            cached = self._cache_get(key)
            if cached is not None:
                results[p.anon_id] = to_enriched(p.anon_id, cached, p, "cache")
                async with lock:
                    counts.enrichment_cached += 1
                return
            async with lock:
                if cost.actual_usd + est_per_call > cost.budget_usd:
                    cost.budget_exhausted = True
                    results[p.anon_id] = heuristic_enrich(p)
                    return
                cost.actual_usd += est_per_call  # reserve; reconciled below
            async with sem:
                assert self.client is not None
                try:
                    res = await self.client.structured(
                        SYSTEM_PROMPT, "Profile (JSON):\n" + json.dumps(payload, default=str), EnrichmentOutput
                    )
                except LLMError as exc:
                    async with lock:
                        cost.actual_usd += exc.cost_usd - est_per_call
                        cost.input_tokens += exc.input_tokens
                        cost.output_tokens += exc.output_tokens
                        cost.llm_calls += 1
                    log.warning("llm_enrich_failed", error=str(exc)[:200])
                    results[p.anon_id] = heuristic_enrich(p)
                    return
            async with lock:
                cost.actual_usd += res.cost_usd - est_per_call
                cost.input_tokens += res.input_tokens
                cost.output_tokens += res.output_tokens
                cost.llm_calls += 1
            assert isinstance(res.data, EnrichmentOutput)
            self._cache_put(key, res.data, res.cost_usd, expires)
            results[p.anon_id] = to_enriched(p.anon_id, res.data, p, "llm")

        await asyncio.gather(*(one(p) for p in profiles))
        cost.actual_usd = round(max(cost.actual_usd, 0.0), 6)
        counts.enriched = len(results)
        with self.sm() as s:
            for anon, e in results.items():
                s.add(
                    EnrichedProfileRow(
                        run_id=run_id, anon_id=anon, method=e.method, data=e.model_dump(mode="json"), expires_at=expires
                    )
                )
            s.commit()
        return results


def build_enricher(config: AppConfig, secrets: Secrets, sm: sessionmaker[Session]) -> Enricher:
    client = None
    if config.enrichment.enabled and secrets.anthropic_api_key:
        client = ClaudeClient(config.enrichment, api_key=secrets.anthropic_api_key)
    return Enricher(config, sm, client)


def estimate_run_cost(
    config: AppConfig, secrets: Secrets, sm: sessionmaker[Session], query: StudyQuery
) -> dict[str, Any]:
    return build_enricher(config, secrets, sm).estimate(query.sample_cap)


def confirm_cost(console: Console, enricher: Enricher, query: StudyQuery, assume_yes: bool) -> bool:
    est = enricher.estimate(query.sample_cap)
    console.print(
        f"[bold]Cost estimate:[/bold] ${est['estimated_usd']:.2f} for up to {est['llm_calls']} LLM calls "
        f"(model {est['model'] or 'none'}; budget cap ${est['budget_usd']:.2f}). {est['note']}"
    )
    if not enricher.uses_llm or assume_yes:
        return True
    import typer

    return typer.confirm("Proceed?", default=True)


def llm_role_suggester(config: AppConfig, secrets: Secrets, sm: sessionmaker[Session]) -> Any:
    """Return a sync suggester(roles, company) -> {canonical_role: [titles]} backed by Claude (or None)."""
    if not (config.enrichment.enabled and secrets.anthropic_api_key):
        return None
    from dcr.roles.titles import default_catalog

    cat = default_catalog()
    client = ClaudeClient(config.enrichment, api_key=secrets.anthropic_api_key)

    def suggest(roles: list[str], company: str) -> dict[str, list[str]]:
        prompt = (
            f"Company: {company or 'unspecified'}\nRequested roles: {roles}\n"
            f"Canonical role ids: {sorted(cat.roles)}\n"
            "For each requested role, map it to a canonical role id and list up to 8 equivalent job titles "
            "that this company or its peers use (including level variants)."
        )
        res = asyncio.run(
            client.structured("You map job titles to canonical roles.", prompt, RoleSuggestions, max_tokens=4000)
        )
        assert isinstance(res.data, RoleSuggestions)
        out: dict[str, list[str]] = {}
        for sgg in res.data.suggestions:
            if sgg.canonical_role in cat.roles:
                out.setdefault(sgg.canonical_role, []).extend(sgg.titles)
        return out

    return suggest
