"""Pipeline and analytics result models (aggregate-only, safe to expose)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class CheckResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class FilterCheck(BaseModel):
    check: str
    result: CheckResult
    reason: str


class FilterDecision(BaseModel):
    """Why one (anonymized) person passed or failed the study filter."""

    anon_id: str
    passed: bool
    checks: list[FilterCheck]

    def failed_checks(self) -> list[str]:
        return [c.check for c in self.checks if c.result != CheckResult.PASS]


class Bucket(BaseModel):
    """One bar in a distribution. ``n`` is None when suppressed."""

    key: str
    n: int | None
    pct: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    suppressed: bool = False


class Distribution(BaseModel):
    """A categorical distribution with k-anonymity applied."""

    name: str
    base_n: int | None
    buckets: list[Bucket] = Field(default_factory=list)
    multi_label: bool = False  # buckets can overlap (e.g. skills); pct is "% of profiles"
    suppressed_buckets: int = 0
    note: str = ""


class NumericSummary(BaseModel):
    name: str
    n: int | None
    p25: float | None = None
    median: float | None = None
    p75: float | None = None
    mean: float | None = None
    ci_low: float | None = None  # CI for the median (bootstrap)
    ci_high: float | None = None
    suppressed: bool = False
    note: str = ""


class Proportion(BaseModel):
    name: str
    n: int | None
    base_n: int | None
    pct: float | None
    ci_low: float | None = None
    ci_high: float | None = None
    suppressed: bool = False
    note: str = ""
    label: str = ""


class StageCounts(BaseModel):
    discovered: int = 0
    fetched: int = 0
    source_errors: int = 0
    resolved_persons: int = 0
    merged_duplicates: int = 0
    passed_filter: int = 0
    failed_filter: int = 0
    sampled: int = 0
    enriched: int = 0
    enrichment_cached: int = 0
    job_postings: int = 0


class RunCost(BaseModel):
    estimated_usd: float = 0.0
    actual_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    budget_usd: float = 0.0
    budget_exhausted: bool = False
    api_requests: dict[str, int] = Field(default_factory=dict)


class Report(BaseModel):
    """A fully aggregated, k-anonymized report for one filter selection."""

    run_id: int | None = None
    filters: dict[str, str] = Field(default_factory=dict)
    suppressed: bool = False
    suppression_reason: str = ""
    n: int | None = None
    sections: dict[str, Any] = Field(default_factory=dict)
    methodology: dict[str, Any] = Field(default_factory=dict)
