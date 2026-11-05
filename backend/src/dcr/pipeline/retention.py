"""TTL purge of personal data. Aggregates, run metadata and job postings are kept."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from dcr.config import AppConfig
from dcr.db.models import PERSONAL_TABLES, AuditLog, EnrichmentCache, HttpCache, NormalizedProfileRow, Run
from dcr.timeutil import utcnow


def purge_expired(
    sm: sessionmaker[Session],
    config: AppConfig,
    *,
    now: Any = None,
    run_id: int | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """Delete personal rows past ``expires_at`` (or all rows of ``run_id`` with ``force``)."""
    now = now or utcnow()
    counts: dict[str, int] = {}
    with sm() as s:
        for table in PERSONAL_TABLES:
            cond = table.expires_at <= now
            if run_id is not None and hasattr(table, "run_id"):
                cond = (table.run_id == run_id) if force else (cond & (table.run_id == run_id))
            n = s.scalar(select(func.count()).select_from(table).where(cond)) or 0
            counts[table.__tablename__] = int(n)
            if n and not dry_run:
                s.execute(delete(table).where(cond))
        cache_cutoff = now - timedelta(days=config.privacy.raw_ttl_days)
        stale_http = s.scalar(select(func.count()).select_from(HttpCache).where(HttpCache.fetched_at <= cache_cutoff))
        counts["http_cache"] = int(stale_http or 0)
        exp_enr = s.scalar(select(func.count()).select_from(EnrichmentCache).where(EnrichmentCache.expires_at <= now))
        counts["enrichment_cache"] = int(exp_enr or 0)
        if not dry_run:
            s.execute(delete(HttpCache).where(HttpCache.fetched_at <= cache_cutoff))
            s.execute(delete(EnrichmentCache).where(EnrichmentCache.expires_at <= now))
            # Mark runs whose personal rows are now gone.
            remaining = {r for (r,) in s.execute(select(NormalizedProfileRow.run_id).distinct())}
            for run in s.query(Run).filter(Run.purged_at.is_(None), Run.status.in_(("completed", "failed"))):
                if run.id not in remaining:
                    run.purged_at = now
            s.add(AuditLog(run_id=None, stage="retention", event="purge", detail=counts))
            s.commit()
    return counts
