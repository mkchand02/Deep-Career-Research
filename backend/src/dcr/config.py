"""Application configuration.

Two layers:

* ``.env`` / environment variables (secrets, database URL) via :class:`Secrets`.
* ``config.yaml`` (source enablement, rate limits, thresholds, TTL, budget) via :class:`AppConfig`.

Privacy floors (e.g. ``k_anonymity >= 5`` and robots.txt compliance) are enforced here
and cannot be configured away.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

K_ANONYMITY_FLOOR = 5
PACKAGE_DATA_DIR = Path(__file__).parent / "data"


def _find_upwards(name: str) -> Path | None:
    """Find ``name`` in the cwd or any parent directory."""
    cur = Path.cwd().resolve()
    for d in (cur, *cur.parents):
        candidate = d / name
        if candidate.exists():
            return candidate
    return None


class Secrets(BaseSettings):
    """Secrets and deployment settings read from the environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=str(_find_upwards(".env") or ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = Field(default="", alias="DATABASE_URL")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    pdl_api_key: str | None = Field(default=None, alias="PDL_API_KEY")
    brave_api_key: str | None = Field(default=None, alias="BRAVE_API_KEY")
    x_bearer_token: str | None = Field(default=None, alias="X_BEARER_TOKEN")
    dcr_config: str | None = Field(default=None, alias="DCR_CONFIG")


class PrivacyConfig(BaseModel):
    """Privacy rules. ``k_anonymity`` is clamped to the hard floor of 5."""

    k_anonymity: int = K_ANONYMITY_FLOOR
    raw_ttl_days: int = 30
    gender_name_estimator: bool = False
    gender_name_min_count: int = 50
    audit_mode: bool = False

    @field_validator("k_anonymity")
    @classmethod
    def _floor_k(cls, v: int) -> int:
        return max(int(v), K_ANONYMITY_FLOOR)

    @field_validator("raw_ttl_days")
    @classmethod
    def _positive_ttl(cls, v: int) -> int:
        return max(int(v), 0)


class PipelineConfig(BaseModel):
    """Defaults for pipeline behaviour (overridable per study)."""

    include_unknown_start_date: bool = False
    exclude_internships: bool = True
    min_confidence: float = 0.6
    default_sample_cap: int = 500
    trend_windows: list[int] = Field(default_factory=lambda: [30, 60, 90])
    resolution_threshold: float = 0.82


class ModelPricing(BaseModel):
    """USD per million tokens."""

    input_per_mtok: float
    output_per_mtok: float


class EnrichmentConfig(BaseModel):
    """LLM enrichment settings."""

    enabled: bool = True
    provider: str = "anthropic"
    model: str = "claude-opus-5-5"
    effort: str = "low"
    max_output_tokens: int = 8000
    budget_usd: float = 5.0
    concurrency: int = 4
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)


class SourceConfig(BaseModel):
    """Per-source settings. Unknown keys are kept in ``options``."""

    model_config = {"extra": "allow"}

    enabled: bool = True
    rate_limit_per_minute: float = 60.0
    max_retries: int = 3
    cache_ttl_hours: float = 24.0
    tos_note: str = ""

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class AppConfig(BaseModel):
    """Root of ``config.yaml``."""

    database_url: str = "sqlite:///./dcr.db"
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    user_agent: str = "DeepCareerResearch/0.1 (+https://github.com/mkchand02/deep-career-research)"

    def source(self, name: str) -> SourceConfig:
        """Return the config for ``name`` (defaults if absent)."""
        return self.sources.get(name, SourceConfig())


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load ``config.yaml`` (explicit path, ``$DCR_CONFIG``, or found upwards from cwd)."""
    secrets = Secrets()
    candidate: Path | None
    if path:
        candidate = Path(path)
    elif secrets.dcr_config:
        candidate = Path(secrets.dcr_config)
    else:
        candidate = _find_upwards("config.yaml")
    data: dict[str, Any] = {}
    if candidate and candidate.exists():
        data = yaml.safe_load(candidate.read_text()) or {}
    cfg = AppConfig.model_validate(data)
    if secrets.database_url:
        cfg.database_url = secrets.database_url
    return cfg


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Process-wide cached config."""
    return load_config()


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    """Process-wide cached secrets."""
    return Secrets()


def reset_caches() -> None:
    """Clear cached config/secrets (used by tests and the CLI ``--config`` flag)."""
    get_config.cache_clear()
    get_secrets.cache_clear()


def data_path(name: str) -> Path:
    """Path to a bundled data file."""
    override = os.environ.get("DCR_DATA_DIR")
    if override and (Path(override) / name).exists():
        return Path(override) / name
    return PACKAGE_DATA_DIR / name
