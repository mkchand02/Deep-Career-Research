"""Skill canonicalization to a fixed taxonomy (e.g. "k8s" → Kubernetes)."""

from __future__ import annotations

from functools import lru_cache

import yaml
from rapidfuzz import fuzz, process

from dcr.config import data_path
from dcr.pipeline.normalize.text import norm_key

# Aliases too ambiguous to detect in free text (still fine in explicit skill lists).
_AMBIGUOUS_IN_TEXT = {
    "go",
    "rest",
    "node",
    "spring",
    "beam",
    "shell",
    "elastic",
    "scale",
    "spark",
    "express",
    "containers",
    "transformers",
    "prompting",
    "torch",
    "kube",
    "dsa",
    "mle",
}


class SkillNormalizer:
    """Maps skill strings to canonical skill names; unknown skills are title-cased passthroughs."""

    def __init__(self, table: dict[str, dict[str, object]] | None = None) -> None:
        if table is None:
            table = yaml.safe_load(data_path("skills.yaml").read_text())["skills"]
        self.category: dict[str, str] = {}
        self._by_key: dict[str, str] = {}
        for canonical, info in table.items():
            self.category[canonical] = str(info.get("category", "other"))
            aliases = info.get("aliases", []) or []
            assert isinstance(aliases, list)
            for name in [canonical, *aliases]:
                self._by_key[norm_key(str(name))] = canonical
        self._keys = list(self._by_key)

    @lru_cache(maxsize=16384)  # noqa: B019
    def canonical(self, raw: str) -> tuple[str, bool]:
        """(canonical_name, known)."""
        key = norm_key(raw)
        if not key:
            return "", False
        if key in self._by_key:
            return self._by_key[key], True
        if len(key) >= 4:
            best = process.extractOne(key, self._keys, scorer=fuzz.ratio, score_cutoff=92)
            if best:
                return self._by_key[best[0]], True
        clean = raw.strip()
        return (clean if any(c.isupper() for c in clean) else clean.title()), False

    def normalize(self, skills: list[str]) -> list[str]:
        """Canonicalize and de-duplicate, preserving first-seen order."""
        out: list[str] = []
        seen: set[str] = set()
        for s in skills:
            c, _ = self.canonical(s)
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def find_in_text(self, text: str) -> list[str]:
        """Find taxonomy skills mentioned in free text (whole-word, alias-aware)."""
        key = f" {norm_key(text)} "
        found = []
        for alias, canonical in self._by_key.items():
            if len(alias) <= 2 or alias in _AMBIGUOUS_IN_TEXT:
                continue
            if f" {alias} " in key and canonical not in found:
                found.append(canonical)
        return found


@lru_cache(maxsize=1)
def default_skill_normalizer() -> SkillNormalizer:
    return SkillNormalizer()
