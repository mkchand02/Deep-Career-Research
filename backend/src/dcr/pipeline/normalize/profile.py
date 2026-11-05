"""Merged RawProfile → NormalizedProfile."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from dcr.models import DegreeLevel, LevelBand, NormalizedProfile, Position, RawProfile, StudyQuery
from dcr.pipeline.normalize.companies import CompanyNormalizer, TargetCompany, default_company_normalizer
from dcr.pipeline.normalize.education import SchoolNormalizer, default_school_normalizer, highest_degree
from dcr.pipeline.normalize.experience import experience_years
from dcr.pipeline.normalize.geo import infer_country
from dcr.pipeline.normalize.skills import SkillNormalizer, default_skill_normalizer
from dcr.roles.titles import RoleCatalog, default_catalog, infer_level_from_experience


@dataclass
class Normalizers:
    companies: CompanyNormalizer = field(default_factory=default_company_normalizer)
    skills: SkillNormalizer = field(default_factory=default_skill_normalizer)
    schools: SchoolNormalizer = field(default_factory=default_school_normalizer)
    roles: RoleCatalog = field(default_factory=default_catalog)


def _first_name(full: str | None) -> str | None:
    if not full:
        return None
    parts = [p for p in full.replace(",", " ").split() if p and not p.endswith(".")]
    return parts[0].title() if parts else None


def _mark_internships(positions: list[Position], cat: RoleCatalog) -> list[Position]:
    out = []
    for p in positions:
        if p.is_internship is None:
            p = p.model_copy(update={"is_internship": cat.parse(p.title).is_internship})
        out.append(p)
    return out


def normalize_profile(
    raw: RawProfile,
    *,
    anon_id: str,
    sources: list[str],
    resolution_confidence: float,
    query: StudyQuery,
    target: TargetCompany,
    norm: Normalizers | None = None,
) -> NormalizedProfile:
    """Canonicalize one merged person against the study's target company."""
    n = norm or Normalizers()
    ref: date = query.ref_date()
    positions = _mark_internships(raw.positions, n.roles)

    current = [p for p in positions if p.end_date is None or p.end_date >= ref]
    target_current = [(p, target.matches(p.company)) for p in current if not p.is_internship]
    target_hits = [(p, m) for p, m in target_current if m[0]]
    if target_hits:
        cur, (_, comp_conf, comp_canon) = max(target_hits, key=lambda x: x[0].start_date or date.min)
    elif current:
        cur = max(current, key=lambda p: p.start_date or date.min)
        _, comp_conf, comp_canon = target.matches(cur.company)
    else:
        cur, comp_conf, comp_canon = None, 0.0, ""

    exp = experience_years(positions, ref, exclude_internships=True)
    exp_all = experience_years(positions, ref, exclude_internships=False)

    role = None
    level_conf = 0.0
    level_inferred = False
    parsed = n.roles.parse(cur.title, comp_canon) if cur else None
    if parsed:
        role = parsed.canonical_role
        level = parsed.level
        level_conf = parsed.confidence
        if level.value == "unknown":
            level = infer_level_from_experience(exp)
            level_inferred = level.value != "unknown"
    else:
        level = LevelBand.UNKNOWN

    country = None
    location = None
    if cur and cur.location:
        location = cur.location
        country = cur.country or infer_country(cur.location)
    if not country:
        country = raw.country or infer_country(raw.location)
        location = location or raw.location

    start = cur.start_date if cur else None
    prior = [
        p
        for p in positions
        if p is not cur and not p.is_internship and (not start or not p.start_date or p.start_date < start)
    ]
    prior_companies, prior_types, prior_countries = [], [], []
    for p in prior:
        m = n.companies.match(p.company)
        if m.canonical and m.canonical != target.canonical and m.canonical not in prior_companies:
            prior_companies.append(m.canonical)
            prior_types.append(m.type)
        c = p.country or infer_country(p.location)
        if c and c not in prior_countries:
            prior_countries.append(c)
    interned_at_target = any(p.is_internship and target.matches(p.company)[0] for p in positions)

    edu = n.schools.normalize(raw.education)
    hd = highest_degree(edu)
    prior_ft_years = experience_years(prior, ref, until=start) if start else None
    latest_grad = max((e.end_year for e in edu if e.end_year), default=None)
    is_new_grad: bool | None
    if start is None or (prior_ft_years is None and latest_grad is None):
        is_new_grad = None
    else:
        recent_grad = latest_grad is not None and latest_grad >= start.year - 1
        is_new_grad = (prior_ft_years or 0.0) < 0.5 and (recent_grad or latest_grad is None)

    skills = n.skills.normalize(raw.skills)
    text_parts = [
        raw.headline or "",
        raw.summary or "",
        *(p.description or "" for p in positions),
        *(pr.description or "" for pr in raw.projects),
    ]
    keywords_text = " ".join(t for t in text_parts if t)
    for s in n.skills.find_in_text(keywords_text):
        if s not in skills:
            skills.append(s)
    projects = []
    for pr in raw.projects:
        projects.append(pr.model_copy(update={"tech_stack": n.skills.normalize(pr.tech_stack)}))

    has_github = bool(raw.github_username) or any("github.com/" in link for link in raw.links)

    return NormalizedProfile(
        anon_id=anon_id,
        sources=sources,
        resolution_confidence=resolution_confidence,
        first_name=_first_name(raw.full_name),
        pronouns=raw.pronouns,
        current_company=comp_canon or None,
        current_company_raw=cur.company if cur else None,
        current_title=cur.title if cur else None,
        canonical_role=role,
        level=level,
        level_inferred=level_inferred,
        role_confidence=level_conf,
        company_confidence=comp_conf,
        country=country,
        location=location,
        start_date=start,
        exp_years=exp,
        exp_years_incl_internships=exp_all,
        is_new_grad=is_new_grad,
        interned_at_target=interned_at_target,
        skills=skills,
        education=edu,
        highest_degree=hd.degree_level if hd else (edu[0].degree_level if edu else DegreeLevel.NONE),
        highest_degree_country=hd.country if hd else None,
        prior_companies=prior_companies,
        prior_company_types=prior_types,
        prior_work_countries=prior_countries,
        projects=projects,
        has_github=has_github,
        keywords_text=keywords_text,
    )
