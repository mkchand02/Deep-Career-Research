"""School and degree canonicalization."""

from __future__ import annotations

import csv
import re
from functools import lru_cache

from rapidfuzz import fuzz, process

from dcr.config import data_path
from dcr.models import DEGREE_RANK, DegreeLevel, Education, NormalizedEducation
from dcr.pipeline.normalize.text import norm_key

_DEGREE_PATTERNS: list[tuple[re.Pattern[str], DegreeLevel]] = [
    (re.compile(r"\b(ph ?d|doctor(ate)?|dphil|d phil)\b"), DegreeLevel.PHD),
    (re.compile(r"\b(mba|master of business administration)\b"), DegreeLevel.MBA),
    (re.compile(r"\b(m ?s|m ?sc|m ?eng|m ?tech|m ?a|mcs|mca|master|masters|master s|me)\b"), DegreeLevel.MASTER),
    (
        re.compile(r"\b(b ?s|b ?sc|b ?a|b ?eng|b ?tech|b ?e|bachelor|bachelors|bachelor s|ab|undergraduate)\b"),
        DegreeLevel.BACHELOR,
    ),
    (re.compile(r"\b(associate|a ?s|a ?a)\b"), DegreeLevel.ASSOCIATE),
]

_FIELD_CANON = {
    "cs": "Computer Science",
    "computer science": "Computer Science",
    "computer science and engineering": "Computer Science",
    "cse": "Computer Science",
    "eecs": "Electrical Engineering & Computer Science",
    "electrical and computer engineering": "Electrical & Computer Engineering",
    "ece": "Electrical & Computer Engineering",
    "electrical engineering": "Electrical Engineering",
    "ee": "Electrical Engineering",
    "computer engineering": "Computer Engineering",
    "data science": "Data Science",
    "machine learning": "Machine Learning",
    "artificial intelligence": "Artificial Intelligence",
    "information technology": "Information Technology",
    "it": "Information Technology",
    "statistics": "Statistics",
    "mathematics": "Mathematics",
    "math": "Mathematics",
    "applied mathematics": "Mathematics",
    "physics": "Physics",
    "software engineering": "Software Engineering",
    "information systems": "Information Systems",
    "mechanical engineering": "Mechanical Engineering",
}


def degree_level(raw: str | None) -> DegreeLevel:
    key = norm_key(raw)
    if not key:
        return DegreeLevel.OTHER
    for pat, lvl in _DEGREE_PATTERNS:
        if pat.search(key):
            return lvl
    return DegreeLevel.OTHER


def canonical_field(raw: str | None) -> str | None:
    key = norm_key(raw)
    if not key:
        return None
    return _FIELD_CANON.get(key, raw.strip().title() if raw else None)


class SchoolNormalizer:
    """Maps institution names to canonical names + country using ``schools.csv``."""

    def __init__(self) -> None:
        self._by_key: dict[str, tuple[str, str]] = {}
        with data_path("schools.csv").open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                canon, country = row["canonical"], row["country"]
                for name in [canon, *[a for a in (row.get("aliases") or "").split("|") if a]]:
                    self._by_key[norm_key(name)] = (canon, country)
        self._keys = list(self._by_key)

    @lru_cache(maxsize=8192)  # noqa: B019
    def match(self, raw: str) -> tuple[str, str | None, bool]:
        """(canonical, country, known)."""
        key = norm_key(raw)
        if key in self._by_key:
            c, k = self._by_key[key]
            return c, k, True
        if len(key) >= 6:
            best = process.extractOne(key, self._keys, scorer=fuzz.token_sort_ratio, score_cutoff=90)
            if best:
                c, k = self._by_key[best[0]]
                return c, k, True
        return raw.strip(), None, False

    def normalize(self, entries: list[Education]) -> list[NormalizedEducation]:
        out = []
        for e in entries:
            canon, country, known = self.match(e.institution)
            out.append(
                NormalizedEducation(
                    institution=canon,
                    institution_known=known,
                    degree_level=degree_level(e.degree),
                    field=canonical_field(e.field),
                    country=e.country or country,
                    end_year=e.end_year,
                )
            )
        return out


def highest_degree(edu: list[NormalizedEducation]) -> NormalizedEducation | None:
    """Highest-ranked degree (ties broken by latest end year)."""
    ranked = [e for e in edu if DEGREE_RANK.get(e.degree_level, 0) > 0]
    if not ranked:
        return None
    return max(ranked, key=lambda e: (DEGREE_RANK[e.degree_level], e.end_year or 0))


@lru_cache(maxsize=1)
def default_school_normalizer() -> SchoolNormalizer:
    return SchoolNormalizer()
