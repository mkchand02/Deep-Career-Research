"""Pipeline orchestration.

Stages: discover → resolve (entity resolution) → normalize → filter → sample → enrich → aggregate.
Normalization runs before filtering because the filter needs canonical company, role, level and dates;
both are recorded in the audit table.

Per-stage counts, errors and cost are written to the ``runs`` row as the run progresses, so the API can
stream progress. Personal-data rows get ``expires_at = now + raw_ttl_days``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets as pysecrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy.orm import Session, sessionmaker

from dcr.config import AppConfig, Secrets, get_config, get_secrets
from dcr.db.models import (
    AuditLog,
    FilterDecisionRow,
    JobPostingRow,
    NormalizedProfileRow,
    Person,
    RawRecord,
    Run,
    Study,
)
from dcr.db.session import get_sessionmaker
from dcr.models import FilterDecision, NormalizedProfile, RoleSpec, RunCost, StageCounts, StudyQuery
from dcr.pipeline.discover import DiscoveryResult, discover
from dcr.pipeline.filter import evaluate, reason_summary, sample
from dcr.pipeline.normalize.companies import TargetCompany, default_company_normalizer
from dcr.pipeline.normalize.geo import infer_country
from dcr.pipeline.normalize.profile import Normalizers, normalize_profile
from dcr.pipeline.resolve import merge_profiles, resolve
from dcr.roles.expansion import expand_roles
from dcr.timeutil import utcnow

log = structlog.get_logger(__name__)

ProgressCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class RunOutcome:
    run_id: int
    counts: StageCounts
    cost: RunCost
    errors: list[dict[str, Any]]
    roles: list[RoleSpec]
    reasons: dict[str, Any]
    sampled: list[NormalizedProfile] = field(default_factory=list)
    decisions: list[FilterDecision] = field(default_factory=list)
    report: dict[str, Any] | None = None
    skipped_sources: list[dict[str, str]] = field(default_factory=list)


class StudyRunner:
    """Runs one study query end-to-end."""

    def __init__(
        self,
        config: AppConfig | None = None,
        secrets: Secrets | None = None,
        session_factory: sessionmaker[Session] | None = None,
        progress: ProgressCallback | None = None,
        adapter_kwargs: dict[str, Any] | None = None,
        enricher: Any = None,
    ) -> None:
        self.config = config or get_config()
        self.secrets = secrets or get_secrets()
        self.sm = session_factory or get_sessionmaker()
        self.progress = progress
        self.adapter_kwargs = {"session_factory": self.sm, **(adapter_kwargs or {})}
        self.enricher = enricher
        self.norm = Normalizers()

    # ----------------------------------------------------------------- helpers
    def _emit(
        self,
        run_id: int,
        stage: str,
        counts: StageCounts,
        cost: RunCost | None = None,
        status: str | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        with self.sm() as s:
            run = s.get(Run, run_id)
            if run is None:
                return
            run.stage = stage
            run.stage_counts = counts.model_dump()
            if cost is not None:
                run.cost = cost.model_dump()
            if status:
                run.status = status
            if errors is not None:
                run.errors = list(errors)
            s.commit()
        if self.progress:
            self.progress(stage, counts.model_dump())

    def audit(
        self, run_id: int, stage: str, event: str, detail: dict[str, Any] | None = None, level: str = "info"
    ) -> None:
        """Append to the run audit log (counts/anon IDs only, never names or URLs)."""
        with self.sm() as s:
            s.add(AuditLog(run_id=run_id, stage=stage, event=event, detail=detail or {}, level=level))
            s.commit()

    def create_run(self, query: StudyQuery, study_id: int | None = None) -> int:
        with self.sm() as s:
            run = Run(
                study_id=study_id,
                query_json=query.model_dump(mode="json"),
                status="queued",
                config_snapshot=self.config.model_dump(mode="json", exclude={"database_url"}),
            )
            s.add(run)
            s.commit()
            return run.id

    def resolve_roles(self, query: StudyQuery) -> tuple[list[RoleSpec], list[str]]:
        """Use confirmed roles if present, otherwise expand the requested roles."""
        if query.expanded_roles is not None:
            return query.expanded_roles, []
        target = default_company_normalizer().match(query.company)
        return expand_roles(
            query.roles,
            company=target.canonical if target.known else query.company,
            include_similar=query.include_similar_roles,
        )

    # -------------------------------------------------------------------- run
    async def run(self, query: StudyQuery, run_id: int | None = None, study_id: int | None = None) -> RunOutcome:
        run_id = run_id or self.create_run(query, study_id)
        counts = StageCounts()
        cost = RunCost(budget_usd=self.config.enrichment.budget_usd)
        errors: list[dict[str, Any]] = []
        expires = utcnow() + timedelta(days=self.config.privacy.raw_ttl_days)
        salt = pysecrets.token_hex(16)
        with self.sm() as s:
            run = s.get(Run, run_id)
            assert run is not None
            run.status, run.started_at = "running", utcnow()
            s.commit()

        try:
            roles, unrecognized = self.resolve_roles(query)
            if unrecognized:
                self.audit(run_id, "roles", "unrecognized_roles", {"roles": unrecognized}, "warning")

            # 1. discover ------------------------------------------------------
            self._emit(run_id, "discover", counts)
            disc: DiscoveryResult = await discover(query, self.config, self.secrets, adapter_kwargs=self.adapter_kwargs)
            errors.extend(disc.errors)
            counts.discovered = disc.discovered
            counts.fetched = len(disc.profiles)
            counts.source_errors = len(disc.errors)
            counts.job_postings = len(disc.postings)
            cost.api_requests = disc.requests
            cost.actual_usd += disc.llm_cost_usd
            self._store_discovery(run_id, disc, expires)
            self.audit(
                run_id,
                "discover",
                "completed",
                {
                    "fetched": counts.fetched,
                    "errors": counts.source_errors,
                    "skipped": disc.skipped_sources,
                    "postings": counts.job_postings,
                    "linked_enrichments": disc.linked_enrichments,
                },
            )
            if disc.robots_skips:
                # Host + reason only (never full URLs of personal pages).
                self.audit(
                    run_id,
                    "discover",
                    "pages_not_fetched",
                    {"count": len(disc.robots_skips), "examples": disc.robots_skips[:20]},
                    "warning",
                )
            self._emit(run_id, "resolve", counts, cost, errors=errors)

            # 2. resolve -------------------------------------------------------
            clusters = resolve(disc.profiles, threshold=self.config.pipeline.resolution_threshold)
            counts.resolved_persons = len(clusters)
            counts.merged_duplicates = len(disc.profiles) - len(clusters)
            self._emit(run_id, "normalize", counts)

            # 3. normalize + 4. filter -----------------------------------------
            target = TargetCompany(query.company, query.company_aliases, self.norm.companies)
            normalized: list[NormalizedProfile] = []
            decisions: list[FilterDecision] = []
            for cl in clusters:
                members = [disc.profiles[i] for i in cl.members]
                merged = merge_profiles(members)
                key = "|".join(sorted(f"{m.source}:{m.source_ref}" for m in members))
                anon = "P" + hmac.new(salt.encode(), key.encode(), hashlib.sha256).hexdigest()[:10]
                np_ = normalize_profile(
                    merged,
                    anon_id=anon,
                    sources=sorted({m.source for m in members}),
                    resolution_confidence=cl.confidence,
                    query=query,
                    target=target,
                    norm=self.norm,
                )
                normalized.append(np_)
                decisions.append(evaluate(np_, query, roles, target.canonical))
            passed = [p for p, d in zip(normalized, decisions, strict=True) if d.passed]
            counts.passed_filter = len(passed)
            counts.failed_filter = len(normalized) - len(passed)
            sampled = sample(passed, query.sample_cap, salt)
            counts.sampled = len(sampled)
            self._store_people(run_id, normalized, decisions, {p.anon_id for p in sampled}, expires)
            reasons = reason_summary(decisions)
            self.audit(
                run_id,
                "filter",
                "completed",
                {"passed": counts.passed_filter, "failed": counts.failed_filter, "reasons": reasons},
            )
            self._emit(run_id, "enrich", counts)

            # 5. enrich ---------------------------------------------------------
            enriched: dict[str, Any] = {}
            if self.enricher is not None and sampled:
                enriched = await self.enricher.enrich_many(run_id, sampled, cost, counts, expires)
            self._emit(run_id, "aggregate", counts, cost)

            # 6. aggregate ------------------------------------------------------
            report = self._aggregate(run_id, query, sampled, enriched, counts, cost, normalized)

            self._emit(run_id, "completed", counts, cost, status="completed", errors=errors)
            with self.sm() as s:
                run = s.get(Run, run_id)
                assert run is not None
                run.finished_at = utcnow()
                s.commit()
            return RunOutcome(
                run_id, counts, cost, errors, roles, reasons, sampled, decisions, report, disc.skipped_sources
            )
        except Exception as exc:
            errors.append(
                {"source": "pipeline", "stage": "run", "error": type(exc).__name__, "message": str(exc)[:300]}
            )
            self._emit(run_id, "failed", counts, cost, status="failed", errors=errors)
            self.audit(run_id, "run", "failed", {"error": type(exc).__name__}, "error")
            raise

    # ----------------------------------------------------------------- storage
    def _store_discovery(self, run_id: int, disc: DiscoveryResult, expires: datetime) -> None:
        cn = self.norm.companies
        with self.sm() as s:
            for p in disc.profiles:
                s.add(
                    RawRecord(
                        run_id=run_id,
                        source=p.source,
                        ref_hash=hashlib.sha256(f"{p.source}|{p.source_ref}".encode()).hexdigest(),
                        payload=p.model_dump(mode="json"),
                        expires_at=expires,
                    )
                )
            for jp in disc.postings:
                parsed = self.norm.roles.parse(jp.title, cn.match(jp.company).canonical)
                s.add(
                    JobPostingRow(
                        run_id=run_id,
                        source=jp.source,
                        title=jp.title[:300],
                        canonical_role=parsed.canonical_role,
                        level=parsed.level.value,
                        location=(jp.location or "")[:300] or None,
                        country=infer_country(jp.location),
                        skills=self.norm.skills.normalize(jp.skills),
                        posted_at=jp.posted_at,
                        url=jp.url,
                    )
                )
            s.commit()

    def _store_people(
        self,
        run_id: int,
        normalized: list[NormalizedProfile],
        decisions: list[FilterDecision],
        sampled_ids: set[str],
        expires: datetime,
    ) -> None:
        with self.sm() as s:
            for p, d in zip(normalized, decisions, strict=True):
                s.add(
                    Person(
                        run_id=run_id,
                        anon_id=p.anon_id,
                        resolution_confidence=p.resolution_confidence,
                        expires_at=expires,
                    )
                )
                s.add(
                    NormalizedProfileRow(
                        run_id=run_id,
                        anon_id=p.anon_id,
                        passed_filter=p.anon_id in sampled_ids,
                        data=p.model_dump(mode="json"),
                        expires_at=expires,
                    )
                )
                s.add(
                    FilterDecisionRow(
                        run_id=run_id,
                        anon_id=p.anon_id,
                        passed=d.passed,
                        checks=[c.model_dump(mode="json") for c in d.checks],
                        expires_at=expires,
                    )
                )
            s.commit()

    def _aggregate(
        self,
        run_id: int,
        query: StudyQuery,
        sampled: list[NormalizedProfile],
        enriched: dict[str, Any],
        counts: StageCounts,
        cost: RunCost,
        normalized: list[NormalizedProfile],
    ) -> dict[str, Any] | None:
        try:
            from dcr.analytics.store import build_and_store_reports
        except ModuleNotFoundError:
            return None
        return build_and_store_reports(self, run_id, query, sampled, enriched, counts, cost, normalized)


# ---------------------------------------------------------------------- CLI glue
def print_roles(console: Console, roles: list[RoleSpec], unrecognized: list[str]) -> None:
    t = Table("role", "levels", "origin", "requested as", "example titles", title="Expanded roles (confirm)")
    for r in roles:
        t.add_row(
            r.display_name,
            ", ".join(lv.value for lv in r.levels) or "any",
            r.origin,
            "; ".join(r.requested_as)[:40],
            ", ".join(r.titles[:4]),
        )
    console.print(t)
    if unrecognized:
        console.print(f"[yellow]Unrecognized roles (ignored): {', '.join(unrecognized)}[/yellow]")


def print_outcome(console: Console, out: RunOutcome) -> None:
    t = Table("stage", "count", title=f"Run #{out.run_id}: stage counts")
    for k, v in out.counts.model_dump().items():
        t.add_row(k, str(v))
    console.print(t)
    r = Table("check", "pass", "fail", "unknown", title="Filter audit (all resolved persons)")
    for check, v in out.reasons.items():
        if not check.startswith("_"):
            r.add_row(check, str(v["pass"]), str(v["fail"]), str(v["unknown"]))
    console.print(r)
    prim = out.reasons.get("_primary_reason", {})
    if prim:
        console.print(
            "Primary rejection reasons: "
            + ", ".join(f"{k}={v}" for k, v in sorted(prim.items(), key=lambda kv: -kv[1]))
        )
    for s in out.skipped_sources:
        console.print(f"[yellow]Skipped source {s['source']}: {s['reason']}[/yellow]")
    if out.errors:
        console.print(f"[yellow]{len(out.errors)} source errors (see run audit log)[/yellow]")
    if out.cost.llm_calls or out.cost.estimated_usd:
        console.print(
            f"LLM cost: ${out.cost.actual_usd:.4f} (estimated ${out.cost.estimated_usd:.4f}, "
            f"{out.cost.llm_calls} calls, budget ${out.cost.budget_usd:.2f})"
        )


async def run_study_cli(
    query: StudyQuery, console: Console, assume_yes: bool = False, save_as: str | None = None, out: Path | None = None
) -> RunOutcome:
    """CLI flow: expand + confirm roles, estimate cost, run, print aggregates."""
    runner = StudyRunner()
    roles, unrecognized = runner.resolve_roles(query)
    print_roles(console, roles, unrecognized)
    if not roles:
        console.print("[red]No recognizable roles; nothing to do.[/red]")
        raise typer.Exit(1)
    if not assume_yes and not typer.confirm("Use these expanded roles?", default=True):
        raise typer.Exit(1)
    query = query.model_copy(update={"expanded_roles": roles})
    try:
        from dcr.enrichment.service import build_enricher, confirm_cost
    except ModuleNotFoundError:
        pass
    else:
        enricher = build_enricher(runner.config, runner.secrets, runner.sm)
        if enricher is not None and not confirm_cost(console, enricher, query, assume_yes):
            raise typer.Exit(1)
        runner.enricher = enricher

    study_id = None
    if save_as:
        with runner.sm() as s:
            st = s.query(Study).filter_by(name=save_as).one_or_none()
            if st is None:
                st = Study(name=save_as, query_json=query.model_dump(mode="json"))
                s.add(st)
            else:
                st.query_json = query.model_dump(mode="json")
            s.commit()
            study_id = st.id
    with console.status("Running study…"):
        outcome = await runner.run(query, study_id=study_id)
    print_outcome(console, outcome)
    if outcome.report is not None:
        try:
            from dcr.analytics.console import print_report
        except ModuleNotFoundError:
            pass
        else:
            print_report(console, outcome.report)
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(outcome.report, indent=2, default=str))
            console.print(f"[green]Aggregate report written to {out}[/green]")
    return outcome
