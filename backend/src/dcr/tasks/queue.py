"""Local asyncio task queue with DB-persisted job state (no Redis needed).

* ``submit(run_id, query)`` enqueues a run whose row already exists with status ``queued``.
* One or more worker coroutines (``concurrency``) execute runs via :class:`StudyRunner`.
* On startup, runs left ``running`` by a crashed process are marked ``failed`` (interrupted), and
  ``queued`` runs are re-enqueued, so state survives restarts.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy.orm import Session, sessionmaker

from dcr.config import AppConfig, Secrets
from dcr.db.models import Run
from dcr.models import StudyQuery
from dcr.timeutil import utcnow

log = structlog.get_logger(__name__)

RunnerFactory = Callable[[], Any]


class LocalTaskQueue:
    def __init__(
        self,
        sm: sessionmaker[Session],
        config: AppConfig,
        secrets: Secrets,
        runner_factory: RunnerFactory | None = None,
        concurrency: int = 1,
    ) -> None:
        self.sm = sm
        self.config = config
        self.secrets = secrets
        self.runner_factory = runner_factory
        self.concurrency = concurrency
        self.queue: asyncio.Queue[tuple[int, StudyQuery]] = asyncio.Queue()
        self.workers: list[asyncio.Task[None]] = []

    def _make_runner(self, run_id: int) -> Any:
        if self.runner_factory:
            return self.runner_factory()
        from dcr.pipeline.runner import StudyRunner

        runner = StudyRunner(self.config, self.secrets, self.sm)
        try:
            from dcr.enrichment.service import build_enricher
        except ModuleNotFoundError:
            return runner
        runner.enricher = build_enricher(self.config, self.secrets, self.sm)
        return runner

    async def start(self) -> None:
        with self.sm() as s:
            for run in s.query(Run).filter(Run.status == "running").all():
                run.status = "failed"
                run.errors = [
                    *(run.errors or []),
                    {
                        "source": "queue",
                        "stage": "run",
                        "error": "Interrupted",
                        "message": "process restarted during run",
                    },
                ]
                run.finished_at = utcnow()
            queued = s.query(Run).filter(Run.status == "queued").all()
            s.commit()
            for run in queued:
                self.queue.put_nowait((run.id, StudyQuery.model_validate(run.query_json)))
        for _ in range(self.concurrency):
            self.workers.append(asyncio.create_task(self._worker()))

    async def stop(self) -> None:
        for w in self.workers:
            w.cancel()
        for w in self.workers:
            with contextlib.suppress(asyncio.CancelledError):
                await w
        self.workers.clear()

    def submit(self, run_id: int, query: StudyQuery) -> None:
        self.queue.put_nowait((run_id, query))

    async def _worker(self) -> None:
        while True:
            run_id, query = await self.queue.get()
            try:
                runner = self._make_runner(run_id)
                # Run in a worker thread with its own event loop so CPU-heavy stages (resolution, aggregation)
                # never block the API. Progress is persisted to the runs table and streamed via SSE polling.
                await asyncio.to_thread(_run_in_new_loop, runner, query, run_id)
            except Exception as exc:  # run row already marked failed by the runner
                log.error("run_failed", run_id=run_id, error=str(exc)[:200])
            finally:
                self.queue.task_done()

    async def join(self) -> None:
        await self.queue.join()


def _run_in_new_loop(runner: Any, query: StudyQuery, run_id: int) -> Any:
    return asyncio.run(runner.run(query, run_id=run_id))
