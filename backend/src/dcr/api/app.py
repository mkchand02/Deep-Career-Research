"""FastAPI application.

Every endpoint returns aggregate data only. Per-person rows are never served. The optional audit
endpoint (``privacy.audit_mode``) returns anonymized IDs and filter reasons, nothing else.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from dcr.adapters.registry import describe_adapters
from dcr.analytics.engine import DIMENSIONS
from dcr.analytics.store import get_report
from dcr.config import AppConfig, Secrets, get_config, get_secrets
from dcr.db.models import AuditLog, FilterDecisionRow, Run, Study
from dcr.db.session import get_sessionmaker
from dcr.models import RoleSpec, StudyQuery
from dcr.pipeline.normalize.companies import default_company_normalizer
from dcr.roles.expansion import expand_roles
from dcr.tasks.queue import LocalTaskQueue

RUN_PUBLIC_FIELDS = (
    "id",
    "study_id",
    "status",
    "stage",
    "stage_counts",
    "errors",
    "cost",
    "created_at",
    "started_at",
    "finished_at",
    "purged_at",
)


class ExpandRequest(BaseModel):
    roles: list[str]
    company: str | None = None
    include_similar: bool = True
    use_llm: bool = False


class RunRequest(BaseModel):
    query: StudyQuery
    study_name: str | None = None


class StudyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    query: StudyQuery


def _run_public(run: Run) -> dict[str, Any]:
    out = {f: getattr(run, f) for f in RUN_PUBLIC_FIELDS}
    q = run.query_json or {}
    out["summary"] = {
        "company": q.get("company"),
        "country": q.get("country"),
        "joined_within_days": q.get("joined_within_days"),
        "joined_from": q.get("joined_from"),
        "joined_to": q.get("joined_to"),
        "reference_date": q.get("reference_date") or (run.created_at.date().isoformat() if run.created_at else None),
        "exp": [q.get("exp_min"), q.get("exp_max")],
        "roles": q.get("roles"),
        "sources": [s.get("name") for s in q.get("sources", [])],
    }
    return out


def create_app(
    config: AppConfig | None = None,
    secrets: Secrets | None = None,
    session_factory: sessionmaker[Session] | None = None,
    start_queue: bool = True,
) -> FastAPI:
    cfg = config or get_config()
    sec = secrets or get_secrets()
    sm = session_factory or get_sessionmaker()
    queue = LocalTaskQueue(sm, cfg, sec)

    async def purge_loop() -> None:
        from dcr.pipeline.retention import purge_expired

        while True:
            with contextlib.suppress(Exception):  # never let retention crash the server
                await asyncio.to_thread(purge_expired, sm, cfg)
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        purger = None
        if start_queue:
            await queue.start()
            purger = asyncio.create_task(purge_loop())
        yield
        if start_queue:
            if purger:
                purger.cancel()
            await queue.stop()

    app = FastAPI(
        title="Deep Career Research API",
        version="0.1.0",
        lifespan=lifespan,
        description="Aggregate-only hiring-trend research. No per-person data is ever returned.",
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.queue = queue
    app.state.config = cfg

    def db() -> Any:
        s = sm()
        try:
            yield s
        finally:
            s.close()

    # ------------------------------------------------------------------ meta
    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    def public_config() -> dict[str, Any]:
        return {
            "k_anonymity": cfg.privacy.k_anonymity,
            "raw_ttl_days": cfg.privacy.raw_ttl_days,
            "gender_name_estimator_default": cfg.privacy.gender_name_estimator,
            "audit_mode": cfg.privacy.audit_mode,
            "trend_windows": cfg.pipeline.trend_windows,
            "defaults": cfg.pipeline.model_dump(),
            "enrichment": {
                "enabled": cfg.enrichment.enabled,
                "model": cfg.enrichment.model,
                "budget_usd": cfg.enrichment.budget_usd,
                "llm_available": bool(sec.anthropic_api_key),
            },
            "dimensions": list(DIMENSIONS),
        }

    @app.get("/api/sources")
    def sources() -> list[dict[str, Any]]:
        return describe_adapters(cfg, sec)

    @app.post("/api/roles/expand")
    def roles_expand(req: ExpandRequest) -> dict[str, Any]:
        m = default_company_normalizer().match(req.company) if req.company else None
        suggester = None
        if req.use_llm:
            try:
                from dcr.enrichment.service import llm_role_suggester

                suggester = llm_role_suggester(cfg, sec, sm)
            except ModuleNotFoundError:
                suggester = None
        specs, unrec = expand_roles(
            req.roles,
            company=m.canonical if m and m.known else req.company,
            include_similar=req.include_similar,
            suggester=suggester,
        )
        return {"roles": [s.model_dump(mode="json") for s in specs], "unrecognized": unrec}

    @app.post("/api/estimate")
    def estimate(req: RunRequest) -> dict[str, Any]:
        try:
            from dcr.enrichment.service import estimate_run_cost
        except ModuleNotFoundError:
            return {"estimated_usd": 0.0, "llm_calls": 0, "note": "enrichment not available"}
        return estimate_run_cost(cfg, sec, sm, req.query)

    @app.get("/api/methodology")
    def methodology() -> dict[str, Any]:
        from dcr.analytics.store import methodology as meth

        return meth(StudyQuery(company="(any)"), cfg, None, cfg.privacy.gender_name_estimator, False, [])

    # --------------------------------------------------------------- studies
    @app.get("/api/studies")
    def list_studies(s: Session = Depends(db)) -> list[dict[str, Any]]:
        out = []
        for st in s.query(Study).order_by(Study.updated_at.desc()).all():
            runs = s.query(Run).filter_by(study_id=st.id).order_by(Run.id.desc()).all()
            out.append(
                {
                    "id": st.id,
                    "name": st.name,
                    "query": st.query_json,
                    "created_at": st.created_at,
                    "updated_at": st.updated_at,
                    "runs": [_run_public(r) for r in runs],
                }
            )
        return out

    @app.post("/api/studies")
    def save_study(req: StudyRequest, s: Session = Depends(db)) -> dict[str, Any]:
        st = s.query(Study).filter_by(name=req.name).one_or_none()
        if st is None:
            st = Study(name=req.name, query_json=req.query.model_dump(mode="json"))
            s.add(st)
        else:
            st.query_json = req.query.model_dump(mode="json")
        s.commit()
        return {"id": st.id, "name": st.name}

    @app.get("/api/studies/{study_id}")
    def get_study(study_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        st = s.get(Study, study_id)
        if st is None:
            raise HTTPException(404, "study not found")
        runs = s.query(Run).filter_by(study_id=st.id).order_by(Run.id.desc()).all()
        return {"id": st.id, "name": st.name, "query": st.query_json, "runs": [_run_public(r) for r in runs]}

    @app.delete("/api/studies/{study_id}")
    def delete_study(study_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        st = s.get(Study, study_id)
        if st is None:
            raise HTTPException(404, "study not found")
        for r in s.query(Run).filter_by(study_id=study_id).all():
            r.study_id = None
        s.delete(st)
        s.commit()
        return {"deleted": study_id}

    @app.post("/api/studies/{study_id}/rerun")
    def rerun_study(study_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        st = s.get(Study, study_id)
        if st is None:
            raise HTTPException(404, "study not found")
        q = StudyQuery.model_validate(st.query_json)
        # re-runs move the reference date to today unless the study pinned a custom window
        if not (q.joined_from or q.joined_to):
            q = q.model_copy(update={"reference_date": None})
        return _enqueue(s, q, st.id)

    # ------------------------------------------------------------------ runs
    def _enqueue(s: Session, q: StudyQuery, study_id: int | None) -> dict[str, Any]:
        if q.expanded_roles is None:
            m = default_company_normalizer().match(q.company)
            specs, _ = expand_roles(
                q.roles, company=m.canonical if m.known else q.company, include_similar=q.include_similar_roles
            )
            q = q.model_copy(update={"expanded_roles": specs})
        if not q.expanded_roles:
            raise HTTPException(422, "no recognizable roles")
        run = Run(
            study_id=study_id,
            query_json=q.model_dump(mode="json"),
            status="queued",
            config_snapshot=cfg.model_dump(mode="json", exclude={"database_url"}),
        )
        s.add(run)
        s.commit()
        queue.submit(run.id, q)
        return {"run_id": run.id, "status": "queued"}

    @app.post("/api/runs")
    def create_run(req: RunRequest, s: Session = Depends(db)) -> dict[str, Any]:
        study_id = None
        if req.study_name:
            st = s.query(Study).filter_by(name=req.study_name).one_or_none()
            if st is None:
                st = Study(name=req.study_name, query_json=req.query.model_dump(mode="json"))
                s.add(st)
            else:
                st.query_json = req.query.model_dump(mode="json")
            s.commit()
            study_id = st.id
        return _enqueue(s, req.query, study_id)

    @app.get("/api/runs")
    def list_runs(limit: int = 50, s: Session = Depends(db)) -> list[dict[str, Any]]:
        return [_run_public(r) for r in s.query(Run).order_by(Run.id.desc()).limit(limit).all()]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        run = s.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        out = _run_public(run)
        out["roles"] = [
            RoleSpec.model_validate(r).model_dump(mode="json") for r in (run.query_json.get("expanded_roles") or [])
        ]
        out["query"] = run.query_json
        return out

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: int, request: Request) -> StreamingResponse:
        async def gen() -> AsyncIterator[str]:
            last = None
            while True:
                if await request.is_disconnected():
                    break
                with sm() as s:
                    run = s.get(Run, run_id)
                    if run is None:
                        yield 'event: error\ndata: {"error": "not found"}\n\n'
                        return
                    payload = json.dumps(
                        {
                            "status": run.status,
                            "stage": run.stage,
                            "counts": run.stage_counts,
                            "cost": run.cost,
                            "errors": len(run.errors or []),
                        },
                        default=str,
                    )
                if payload != last:
                    yield f"data: {payload}\n\n"
                    last = payload
                if run.status in ("completed", "failed"):
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/runs/{run_id}/log")
    def run_log(run_id: int, s: Session = Depends(db)) -> list[dict[str, Any]]:
        rows = s.query(AuditLog).filter_by(run_id=run_id).order_by(AuditLog.id).all()
        return [{"ts": r.ts, "stage": r.stage, "level": r.level, "event": r.event, "detail": r.detail} for r in rows]

    @app.get("/api/runs/{run_id}/report")
    def run_report(run_id: int, request: Request) -> dict[str, Any]:
        filters = {k: v for k, v in request.query_params.items() if k in DIMENSIONS and v}
        unknown = [k for k in request.query_params if k not in DIMENSIONS]
        if unknown:
            raise HTTPException(400, f"unknown filter(s): {unknown}")
        try:
            return get_report(sm, run_id, filters, cfg)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/runs/{run_id}/export.{fmt}")
    def export(run_id: int, fmt: str, request: Request) -> Response:
        from dcr.exports.report import ExportPrivacyError, charts_zip, to_csv, to_html, to_json, to_pdf
        from dcr.timeutil import utcnow

        filters = {k: v for k, v in request.query_params.items() if k in DIMENSIONS and v}
        try:
            rep = get_report(sm, run_id, filters, cfg)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        k = cfg.privacy.k_anonymity
        stamp = utcnow().strftime("%Y-%m-%d %H:%M UTC")
        name = f"dcr-run{run_id}" + ("-" + "-".join(f"{a}_{b}" for a, b in sorted(filters.items())) if filters else "")
        try:
            if fmt == "csv":
                return Response(
                    to_csv(rep, k),
                    media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
                )
            if fmt == "json":
                return Response(
                    to_json(rep, k),
                    media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
                )
            if fmt == "html":
                return HTMLResponse(to_html(rep, k, stamp))
            if fmt == "pdf":
                return Response(
                    to_pdf(rep, k, stamp),
                    media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'},
                )
            if fmt == "zip":
                return Response(
                    charts_zip(rep, k),
                    media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}-charts.zip"'},
                )
        except ExportPrivacyError as exc:
            raise HTTPException(500, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(501, str(exc)) from exc
        raise HTTPException(400, "format must be csv, json, html, pdf or zip")

    @app.get("/api/compare")
    def compare(request: Request, runs: str = Query(..., description="comma-separated run ids")) -> dict[str, Any]:
        from dcr.analytics.compare import compare_runs

        filters = {k: v for k, v in request.query_params.items() if k in DIMENSIONS and v}
        try:
            ids = [int(x) for x in runs.split(",") if x.strip()]
            return compare_runs(sm, ids, filters, cfg)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/purge")
    def purge(dry_run: bool = False) -> dict[str, Any]:
        from dcr.pipeline.retention import purge_expired

        return {
            "dry_run": dry_run,
            "deleted" if not dry_run else "would_delete": purge_expired(sm, cfg, dry_run=dry_run),
        }

    @app.get("/api/runs/{run_id}/audit")
    def run_audit(run_id: int, limit: int = Query(200, le=2000), s: Session = Depends(db)) -> dict[str, Any]:
        if not cfg.privacy.audit_mode:
            raise HTTPException(403, "audit mode is disabled (privacy.audit_mode in config.yaml)")
        rows = s.query(FilterDecisionRow).filter_by(run_id=run_id).limit(limit).all()
        return {
            "note": "Anonymized IDs and filter reasons only.",
            "rows": [{"anon_id": r.anon_id, "passed": r.passed, "checks": r.checks} for r in rows],
        }

    return app
