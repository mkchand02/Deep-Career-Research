"""Rule-based enrichment (no API key / over budget / LLM failure)."""

from __future__ import annotations

from dcr.models import EnrichedProfile, ExperienceSummary, NormalizedProfile, Project
from dcr.pipeline.normalize.skills import default_skill_normalizer
from dcr.pipeline.normalize.text import norm_key

TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("llm", ("llm", "language model", "rag", "langchain", "gpt", "prompt", "fine tun")),
    ("ml", ("machine learning", "pytorch", "tensorflow", "jax", "model", "neural", "scikit", "xgboost", "learning")),
    ("computer-vision", ("vision", "image", "segmentation", "opencv", "detection")),
    ("nlp", ("nlp", "text", "language", "sentiment")),
    ("infra", ("kubernetes", "terraform", "infra", "autoscal", "cluster", "devops", "ci cd")),
    ("cloud", ("cloud", "gcp", "aws", "azure", "google cloud")),
    ("distributed-systems", ("distributed", "raft", "consensus", "key value", "replicat")),
    ("web", ("react", "next js", "web", "frontend", "website", "portfolio", "django", "node js", "chat app")),
    ("mobile", ("android", "ios", "kotlin", "swift", "mobile")),
    ("data", ("etl", "pipeline", "spark", "kafka", "airflow", "warehouse", "dbt", "bigquery")),
    ("analytics", ("dashboard", "analytics", "looker", "tableau")),
    ("systems", ("compiler", "rust", "c++", "operating system", "raytracer", "kernel")),
    ("security", ("security", "crypto", "auth", "vulnerab")),
    ("fintech", ("fraud", "payment", "trading", "finance")),
    ("open-source", ("open source", "contribution")),
]
FOCUS_BY_TAG = {
    "llm": "machine_learning",
    "ml": "machine_learning",
    "computer-vision": "machine_learning",
    "nlp": "machine_learning",
    "infra": "infrastructure",
    "cloud": "infrastructure",
    "distributed-systems": "systems",
    "systems": "systems",
    "web": "web_app",
    "mobile": "mobile",
    "data": "data_engineering",
    "analytics": "data_engineering",
    "security": "security",
    "fintech": "other",
    "open-source": "developer_tools",
    "customer": "customer_solutions",
}


def tag_project(pr: Project) -> Project:
    text = f" {norm_key(' '.join([pr.name, pr.description or '', ' '.join(pr.tech_stack)]))} "
    tags = list(pr.tags)
    for tag, kws in TAG_RULES:
        if tag not in tags and any(f" {norm_key(k)}" in text for k in kws):
            tags.append(tag)
    focus = pr.focus or next((FOCUS_BY_TAG[t] for t in tags if t in FOCUS_BY_TAG), "other")
    return pr.model_copy(update={"tags": tags or ["other"], "focus": focus})


def heuristic_enrich(p: NormalizedProfile) -> EnrichedProfile:
    sn = default_skill_normalizer()
    projects = [tag_project(pr) for pr in p.projects]
    extra = []
    for pr in projects:
        for s in sn.find_in_text(f"{pr.name} {pr.description or ''}"):
            if s not in p.skills and s not in extra:
                extra.append(s)
    return EnrichedProfile(
        anon_id=p.anon_id,
        projects=projects,
        experience=ExperienceSummary(
            years=p.exp_years,
            prior_companies=p.prior_companies,
            prior_company_types=p.prior_company_types,
            is_new_grad=p.is_new_grad,
        ),
        education=p.education,
        extra_skills=extra,
        method="heuristic",
    )
