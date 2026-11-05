"""Structured logging (structlog). JSON lines when ``DCR_LOG_JSON=1``, pretty console otherwise."""

from __future__ import annotations

import contextlib
import logging
import os
import sys

import structlog


class _StderrLogger:
    """Writes to whatever ``sys.stderr`` is *now* (robust to redirected/closed streams in tests and CLIs)."""

    def msg(self, message: str) -> None:
        with contextlib.suppress(ValueError):  # stream closed
            print(message, file=sys.stderr, flush=True)

    log = debug = info = warning = warn = error = critical = exception = fatal = msg


def _factory(*_: object) -> _StderrLogger:
    return _StderrLogger()


def configure_logging(level: str | None = None, json_logs: bool | None = None) -> None:
    """Configure structlog + stdlib logging once per process."""
    lvl = getattr(logging, (level or os.environ.get("DCR_LOG_LEVEL", "WARNING")).upper(), logging.WARNING)
    use_json = json_logs if json_logs is not None else os.environ.get("DCR_LOG_JSON") == "1"
    logging.basicConfig(level=lvl, stream=sys.stderr, format="%(message)s")
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if use_json else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        logger_factory=_factory,
        cache_logger_on_first_use=False,
    )
