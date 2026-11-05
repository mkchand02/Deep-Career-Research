"""Study query models."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field, model_validator

from dcr.models.profile import LevelBand


class RoleSpec(BaseModel):
    """One canonical role (optionally restricted to level bands) with the titles it covers."""

    canonical_role: str
    display_name: str
    levels: list[LevelBand] = Field(default_factory=list)  # empty = any level
    titles: list[str] = Field(default_factory=list)
    origin: str = "user"  # user | synonym | similar | llm
    requested_as: list[str] = Field(default_factory=list)

    def matches(self, role: str | None, level: LevelBand) -> bool:
        """True if (role, level) is covered by this spec. Unknown level matches any-level specs only."""
        if role != self.canonical_role:
            return False
        if not self.levels:
            return True
        return level in self.levels


class SourceSpec(BaseModel):
    """A source adapter to run, with adapter-specific params (e.g. a file path, seed URLs)."""

    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class StudyQuery(BaseModel):
    """User-configurable study parameters."""

    company: str
    company_aliases: list[str] = Field(default_factory=list)
    country: str = "US"
    regions: list[str] = Field(default_factory=list)
    joined_within_days: int | None = 60
    joined_from: date | None = None
    joined_to: date | None = None
    exp_min: float = 0.0
    exp_max: float | None = 3.0
    exclude_internships: bool = True
    roles: list[str] = Field(default_factory=list)
    include_similar_roles: bool = True
    expanded_roles: list[RoleSpec] | None = None
    sample_cap: int = 500
    sources: list[SourceSpec] = Field(default_factory=list)
    min_confidence: float = 0.6
    include_unknown_start_date: bool = False
    reference_date: date | None = None
    gender_name_estimator: bool | None = None  # None = use config default

    @model_validator(mode="after")
    def _check(self) -> StudyQuery:
        self.country = self.country.upper()
        if self.exp_max is not None and self.exp_max < self.exp_min:
            raise ValueError("exp_max must be >= exp_min")
        if self.joined_from and self.joined_to and self.joined_from > self.joined_to:
            raise ValueError("joined_from must be <= joined_to")
        if self.sample_cap <= 0:
            raise ValueError("sample_cap must be positive")
        return self

    def ref_date(self) -> date:
        return self.reference_date or date.today()

    def window(self) -> tuple[date, date]:
        """Inclusive [start, end] join-date window."""
        if self.joined_from or self.joined_to:
            end = self.joined_to or self.ref_date()
            start = self.joined_from or (end - timedelta(days=self.joined_within_days or 60))
            return start, end
        end = self.ref_date()
        return end - timedelta(days=self.joined_within_days or 60), end

    def company_names(self) -> list[str]:
        return [self.company, *self.company_aliases]
