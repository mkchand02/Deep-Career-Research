"""Pipeline orchestration (Phase 1: discovery + raw storage)."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from dcr.config import get_config, get_secrets
from dcr.db.models import AuditLog, RawRecord, Run
from dcr.db.session import get_sessionmaker
from dcr.models import StudyQuery
from dcr.pipeline.discover import discover
from dcr.timeutil import utcnow


async def run_study_cli(
    query: StudyQuery, console: Console, assume_yes: bool = False, save_as: str | None = None, out: Path | None = None
) -> dict[str, Any]:
    cfg, secrets = get_config(), get_secrets()
    sm = get_sessionmaker()
    expires = utcnow() + timedelta(days=cfg.privacy.raw_ttl_days)
    with sm() as s:
        run = Run(query_json=query.model_dump(mode="json"), status="running", started_at=utcnow())
        s.add(run)
        s.commit()
        run_id = run.id
    res = await discover(query, cfg, secrets, adapter_kwargs={"session_factory": sm})
    with sm() as s:
        for p in res.profiles:
            s.add(
                RawRecord(
                    run_id=run_id,
                    source=p.source,
                    ref_hash=hashlib.sha256(f"{p.source}|{p.source_ref}".encode()).hexdigest(),
                    payload=p.model_dump(mode="json"),
                    expires_at=expires,
                )
            )
        s.add(
            AuditLog(
                run_id=run_id,
                stage="discover",
                event="completed",
                detail={"fetched": len(res.profiles), "errors": len(res.errors)},
            )
        )
        row = s.get(Run, run_id)
        assert row is not None
        run = row
        run.status = "completed"
        run.stage_counts = {"discovered": res.discovered, "fetched": len(res.profiles)}
        run.errors = res.errors
        run.finished_at = utcnow()
        s.commit()
    t = Table("stage", "count")
    t.add_row("discovered", str(res.discovered))
    t.add_row("fetched", str(len(res.profiles)))
    t.add_row("source errors", str(len(res.errors)))
    console.print(f"Run #{run_id}")
    console.print(t)
    return {"run_id": run_id}
