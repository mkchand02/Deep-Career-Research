"""Adapter registry.

Adding a source is one file: subclass :class:`SourceAdapter` (or :class:`JobPostingAdapter`) and
decorate it with ``@register_adapter``. Third-party packages can also register adapters through
the ``dcr.adapters`` entry-point group.
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points
from typing import Any, TypeVar

from dcr.adapters.base import AdapterContext, JobPostingAdapter, SourceAdapter
from dcr.config import AppConfig, Secrets

AdapterT = TypeVar("AdapterT", bound=type[SourceAdapter] | type[JobPostingAdapter])

_REGISTRY: dict[str, type[SourceAdapter] | type[JobPostingAdapter]] = {}
_BUILTIN_MODULES = (
    "dcr.adapters.manual_import",
    "dcr.adapters.synthetic",
    "dcr.adapters.github",
    "dcr.adapters.web_scraper",
    "dcr.adapters.jobs.boards",
    "dcr.adapters.jobs.manual",
    "dcr.adapters.keyed.pdl",
    "dcr.adapters.keyed.brave",
    "dcr.adapters.keyed.x",
)
_loaded = False


def register_adapter(cls: AdapterT) -> AdapterT:
    """Class decorator registering an adapter under ``cls.name``."""
    _REGISTRY[cls.name] = cls
    return cls


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    for mod in _BUILTIN_MODULES:
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as exc:  # module not yet implemented
            if exc.name != mod:
                raise
    for ep in entry_points(group="dcr.adapters"):
        ep.load()


def all_adapters() -> dict[str, type[SourceAdapter] | type[JobPostingAdapter]]:
    _load()
    return dict(_REGISTRY)


def get_adapter_class(name: str) -> type[SourceAdapter] | type[JobPostingAdapter]:
    _load()
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"Unknown source adapter '{name}'. Known: {sorted(_REGISTRY)}") from exc


def build_adapter(
    name: str, config: AppConfig, secrets: Secrets, params: dict[str, Any] | None = None, **ctx_kwargs: Any
) -> SourceAdapter | JobPostingAdapter:
    cls = get_adapter_class(name)
    return cls(AdapterContext(name, config, secrets, **ctx_kwargs), params)


def describe_adapters(config: AppConfig, secrets: Secrets) -> list[dict[str, Any]]:
    """Adapter catalogue with availability (used by CLI ``dcr sources`` and the API)."""
    out = []
    for name, cls in sorted(all_adapters().items()):
        adapter = cls(AdapterContext(name, config, secrets), {})
        ok, reason = adapter.available()
        out.append(
            {
                "name": name,
                "kind": cls.kind,
                "available": ok,
                "reason": reason,
                "requires": cls.requires_secret.upper() if cls.requires_secret else None,
                "description": cls.description,
                "tos_note": config.source(name).tos_note or cls.tos_note,
            }
        )
    return out
