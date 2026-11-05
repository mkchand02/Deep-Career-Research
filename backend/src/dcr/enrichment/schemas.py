"""Structured-output schemas for LLM enrichment (validated with Pydantic)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "enrich-v1"

ProjectTag = Literal[
    "ml",
    "llm",
    "computer-vision",
    "nlp",
    "infra",
    "cloud",
    "distributed-systems",
    "web",
    "mobile",
    "data",
    "analytics",
    "systems",
    "security",
    "devtools",
    "research",
    "fintech",
    "open-source",
    "customer",
    "graphics",
    "robotics",
    "other",
]
FocusCategory = Literal[
    "machine_learning",
    "infrastructure",
    "web_app",
    "data_engineering",
    "mobile",
    "systems",
    "security",
    "research",
    "developer_tools",
    "customer_solutions",
    "other",
]
CompanyType = Literal["big_tech", "enterprise", "startup", "finance", "consulting", "academia", "government", "other"]
DegreeLevelLit = Literal["associate", "bachelor", "master", "mba", "phd", "other", "none"]


class LLMProject(BaseModel):
    name: str
    description: str = Field(description="One short sentence, no personal names or URLs")
    tags: list[ProjectTag]
    tech_stack: list[str]
    focus: FocusCategory
    kind: Literal["personal", "academic", "professional", "unknown"]


class LLMExperience(BaseModel):
    total_years: float | None = Field(description="Full-time professional years, excluding internships")
    prior_companies: list[str]
    prior_company_types: list[CompanyType]
    is_new_grad: bool | None


class LLMEducation(BaseModel):
    institution: str
    degree_level: DegreeLevelLit
    field: str | None
    institution_country: str | None = Field(description="ISO 3166-1 alpha-2 code, or null if unknown")


class EnrichmentOutput(BaseModel):
    projects: list[LLMProject]
    experience: LLMExperience
    education: list[LLMEducation]
    skills: list[str] = Field(description="Technical skills evidenced in the profile, canonical names")


class PagePosition(BaseModel):
    title: str
    company: str
    start: str | None = Field(description="YYYY-MM or YYYY-MM-DD, else null")
    end: str | None = Field(description="YYYY-MM or null if current")
    location: str | None


class PageEducation(BaseModel):
    institution: str
    degree: str | None
    field: str | None
    end_year: int | None


class PageProject(BaseModel):
    name: str
    description: str | None


class LLMPageProfile(BaseModel):
    """Structured facts extracted from a public web page (used by the web scraper)."""

    is_professional_profile: bool = Field(description="True only if the page describes one person's career")
    current_title: str | None
    current_company: str | None
    current_start_date: str | None = Field(description="YYYY-MM or YYYY-MM-DD if stated, else null")
    location: str | None
    pronouns: str | None = Field(description="Only if explicitly self-stated on the page, else null")
    prior_positions: list[PagePosition]
    education: list[PageEducation]
    skills: list[str]
    projects: list[PageProject]


class RoleSuggestion(BaseModel):
    canonical_role: str = Field(description="One of the given canonical role ids")
    titles: list[str] = Field(description="Equivalent job titles used in industry")


class RoleSuggestions(BaseModel):
    suggestions: list[RoleSuggestion]


def strict_json_schema(model: type[BaseModel]) -> dict[str, object]:
    """Pydantic JSON schema with ``additionalProperties: false`` on every object (structured-output friendly)."""
    schema = model.model_json_schema()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node.setdefault("additionalProperties", False)
                node["required"] = list(node["properties"].keys())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema
