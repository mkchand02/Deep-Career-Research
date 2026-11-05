"""Structured logging (structlog). JSON lines when ``DCR_LOG_JSON=1``, pretty console otherwise."""

from __future__ import annotations

import logging
import os
import sys

import structlog


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
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
