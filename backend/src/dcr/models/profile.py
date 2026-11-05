"""Profile models across the raw → normalized → enriched layers.

Raw and normalized profiles contain personal data. They live only in TTL-purged tables and are
never returned by the API or written to exports. Analytics consumes :class:`AnalysisRecord`,
which drops direct identifiers (name, URLs, free text).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from dcr.timeutil import utcnow


class Position(BaseModel):
    """One role held at one employer."""

    title: str
    company: str
    location: str | None = None
    country: str | None = None
    start_date: date | None = None
    end_date: date | None = None  # None = current
    is_internship: bool | None = None
    description: str | None = None

    @property
    def is_current(self) -> bool:
        return self.end_date is None


class Education(BaseModel):
    """One education entry."""

    institution: str
    degree: str | None = None
    field: str | None = None
    country: str | None = None
    start_year: int | None = None
    end_year: int | None = None


ProjectKind = Literal["personal", "academic", "professional", "unknown"]


class Project(BaseModel):
    """A project mentioned on a profile or found on GitHub."""

    name: str
    description: str | None = None
    url: str | None = None
    tags: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    focus: str | None = None
    kind: ProjectKind = "unknown"
    stars: int | None = None
    commits: int | None = None


class RawProfile(BaseModel):
    """A profile as returned by a source adapter (personal data, TTL-purged)."""

    source: str
    source_ref: str
    full_name: str | None = None
    headline: str | None = None
    location: str | None = None
    country: str | None = None
    pronouns: str | None = None
    links: list[str] = Field(default_factory=list)
    github_username: str | None = None
    positions: list[Position] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    summary: str | None = None
    text: str | None = None  # unstructured page text for LLM extraction
    fetched_at: datetime = Field(default_factory=utcnow)
    synthetic: bool = False


class LevelBand(StrEnum):
    """Company-independent seniority band."""

    ENTRY = "entry"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    UNKNOWN = "unknown"


class DegreeLevel(StrEnum):
    """Canonical degree level, ordered by rank."""

    NONE = "none"
    ASSOCIATE = "associate"
    BACHELOR = "bachelor"
    MASTER = "master"
    MBA = "mba"
    PHD = "phd"
    OTHER = "other"


DEGREE_RANK = {
    DegreeLevel.NONE: 0,
    DegreeLevel.OTHER: 0,
    DegreeLevel.ASSOCIATE: 1,
    DegreeLevel.BACHELOR: 2,
    DegreeLevel.MASTER: 3,
    DegreeLevel.MBA: 3,
    DegreeLevel.PHD: 4,
}


class NormalizedEducation(BaseModel):
    institution: str
    institution_known: bool = False
    degree_level: DegreeLevel = DegreeLevel.OTHER
    field: str | None = None
    country: str | None = None
    end_year: int | None = None


class NormalizedProfile(BaseModel):
    """A resolved person after normalization (personal data, TTL-purged)."""

    anon_id: str
    sources: list[str]
    resolution_confidence: float = 1.0
    first_name: str | None = None  # only for the optional aggregate gender estimator
    pronouns: str | None = None
    current_company: str | None = None
    current_company_raw: str | None = None
    current_title: str | None = None
    canonical_role: str | None = None
    level: LevelBand = LevelBand.UNKNOWN
    level_inferred: bool = False
    role_confidence: float = 0.0
    company_confidence: float = 0.0
    country: str | None = None
    location: str | None = None
    start_date: date | None = None
    exp_years: float | None = None
    exp_years_incl_internships: float | None = None
    is_new_grad: bool | None = None
    interned_at_target: bool = False
    skills: list[str] = Field(default_factory=list)
    education: list[NormalizedEducation] = Field(default_factory=list)
    highest_degree: DegreeLevel = DegreeLevel.NONE
    highest_degree_country: str | None = None
    prior_companies: list[str] = Field(default_factory=list)
    prior_company_types: list[str] = Field(default_factory=list)
    prior_work_countries: list[str] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    has_github: bool = False
    keywords_text: str = ""  # headline + summary + descriptions; purged with the profile


class ExperienceSummary(BaseModel):
    years: float | None = None
    prior_companies: list[str] = Field(default_factory=list)
    prior_company_types: list[str] = Field(default_factory=list)
    is_new_grad: bool | None = None


class EnrichedProfile(BaseModel):
    """LLM/heuristic enrichment output for one person (TTL-purged)."""

    anon_id: str
    projects: list[Project] = Field(default_factory=list)
    experience: ExperienceSummary = Field(default_factory=ExperienceSummary)
    education: list[NormalizedEducation] = Field(default_factory=list)
    extra_skills: list[str] = Field(default_factory=list)
    method: Literal["llm", "heuristic", "cache"] = "heuristic"


class AnalysisRecord(BaseModel):
    """De-identified view of a person used only inside the analytics engine.

    Never persisted after TTL and never returned directly by the API.
    """

    role: str
    level: str
    exp_years: float | None
    exp_band: str
    source: str
    sources: list[str]
    start_date: date | None
    edu_origin: Literal["domestic", "foreign", "unknown"]
    highest_degree: str
    fields: list[str]
    schools: list[str]
    skills: list[str]
    keywords_text: str = ""
    project_tags: list[str]
    project_kinds: list[str]
    project_stacks: list[str]
    project_focus: list[str]
    has_github: bool
    github_stars: list[int]
    github_commits: list[int]
    prior_companies: list[str]
    prior_company_types: list[str]
    hire_type: Literal["new_grad", "lateral", "unknown"]
    interned_at_target: bool
    worked_abroad: bool | None
    gender_self: Literal["woman", "man", "nonbinary"] | None = None
    p_female_name: float | None = None
