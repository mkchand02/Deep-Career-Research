"""Discovery stage: run each enabled source adapter, isolating per-source failures."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from dcr.adapters.base import JobPosting, JobPostingAdapter, SourceAdapter, SourceError
from dcr.adapters.registry import build_adapter
from dcr.config import AppConfig, Secrets
from dcr.models import RawProfile, StudyQuery

log = structlog.get_logger(__name__)


@dataclass
class DiscoveryResult:
    profiles: list[RawProfile] = field(default_factory=list)
    postings: list[JobPosting] = field(default_factory=list)
    discovered: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    requests: dict[str, int] = field(default_factory=dict)
    skipped_sources: list[dict[str, str]] = field(default_factory=list)


def _safe_error(source: str, stage: str, exc: BaseException) -> dict[str, Any]:
    """Error record without personal data (messages are adapter-controlled, URLs truncated)."""
    msg = str(exc)
    if len(msg) > 300:
        msg = msg[:300] + "…"
    return {"source": source, "stage": stage, "error": type(exc).__name__, "message": msg}


async def discover(
    query: StudyQuery,
    config: AppConfig,
    secrets: Secrets,
    *,
    adapter_kwargs: dict[str, Any] | None = None,
    max_candidates_per_source: int | None = None,
    fetch_concurrency: int = 8,
    on_progress: Any = None,
) -> DiscoveryResult:
    """Search + fetch across all sources in ``query.sources``. A failing source never aborts the run."""
    result = DiscoveryResult()
    limit = max_candidates_per_source or max(query.sample_cap * 5, 200)
    sem = asyncio.Semaphore(fetch_concurrency)

    async def run_profiles(adapter: SourceAdapter) -> None:
        try:
            candidates = await adapter.search(query, limit)
        except (SourceError, Exception) as exc:
            result.errors.append(_safe_error(adapter.name, "search", exc))
            log.warning("source_search_failed", source=adapter.name, error=str(exc)[:200])
            return
        result.discovered += len(candidates)

        async def one(c: Any) -> None:
            async with sem:
                try:
                    prof = await adapter.fetch(c)
                except (SourceError, Exception) as exc:
                    result.errors.append(_safe_error(adapter.name, "fetch", exc))
                    return
                if prof is not None:
                    result.profiles.append(prof)
                    if on_progress and len(result.profiles) % 100 == 0:
                        on_progress("discover", {"fetched": len(result.profiles)})

        await asyncio.gather(*(one(c) for c in candidates))

    async def run_jobs(adapter: JobPostingAdapter) -> None:
        try:
            result.postings.extend(await adapter.postings(query, limit=2000))
        except (SourceError, Exception) as exc:
            result.errors.append(_safe_error(adapter.name, "postings", exc))

    adapters: list[SourceAdapter | JobPostingAdapter] = []
    for spec in query.sources:
        try:
            adapter = build_adapter(spec.name, config, secrets, spec.params, **(adapter_kwargs or {}))
        except KeyError as exc:
            result.errors.append(_safe_error(spec.name, "init", exc))
            continue
        ok, reason = adapter.available()
        if not ok:
            result.skipped_sources.append({"source": spec.name, "reason": reason})
            continue
        adapters.append(adapter)

    tasks = [run_profiles(a) if isinstance(a, SourceAdapter) else run_jobs(a) for a in adapters]
    try:
        await asyncio.gather(*tasks)
    finally:
        for a in adapters:
            result.requests[a.name] = a.ctx.requests_made
            await a.aclose()
    return result
