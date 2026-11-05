"""Job-title parsing: title → (canonical role, level band, confidence, internship flag)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml
from rapidfuzz import fuzz, process

from dcr.config import data_path
from dcr.models import LevelBand
from dcr.pipeline.normalize.text import norm_key

_INTERN = re.compile(r"\b(intern|internship|co op|coop|summer analyst|apprentice|trainee)\b")
_LEVEL_CODE = re.compile(r"\b(l\d{1,2}|e\d|ic\d|level \d)\b")
_NUM = re.compile(r"\b(5[0-9]|6[0-9]|[1-4])\b")
_ROMAN = re.compile(r"\b(i{1,3}|iv)\b")
_WORDS = re.compile(
    r"\b(new grad|entry level|senior|sr|staff|principal|junior|jr|associate|lead|distinguished|fellow|graduate)\b"
)
_FILLER = re.compile(r"\b(at|the|of|and|&|for|in|team|level|grade)\b|[-/|]")


@dataclass(frozen=True)
class TitleParse:
    canonical_role: str | None
    level: LevelBand
    confidence: float
    is_internship: bool
    base: str
    level_tokens: tuple[str, ...]


class RoleCatalog:
    """Role synonym map + company level ladders loaded from ``role_synonyms.yaml``."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or yaml.safe_load(data_path("role_synonyms.yaml").read_text())
        self.roles: dict[str, dict[str, Any]] = data["roles"]
        self.request_aliases: dict[str, str] = data.get("aliases", {})
        self.ladders: dict[str, dict[str, str]] = {
            k: {str(t).lower(): v for t, v in lad.items()} for k, lad in data.get("ladders", {}).items()
        }
        self.title_to_role: dict[str, str] = {}
        for role, info in self.roles.items():
            for t in info.get("titles", []):
                self.title_to_role.setdefault(norm_key(t), role)
        # longest-first for containment matching
        self._titles_by_len = sorted(self.title_to_role, key=len, reverse=True)

    def display(self, role: str) -> str:
        return str(self.roles.get(role, {}).get("display", role))

    def similar(self, role: str) -> list[str]:
        return list(self.roles.get(role, {}).get("similar", []))

    def titles(self, role: str) -> list[str]:
        return list(self.roles.get(role, {}).get("titles", []))

    # ----------------------------------------------------------------- levels
    def _band_for(self, token: str, company: str | None) -> LevelBand | None:
        ladder = self.ladders.get(company or "", {})
        if token in ladder:
            return LevelBand(ladder[token])
        generic = self.ladders.get("generic", {})
        if token in generic:
            return LevelBand(generic[token])
        return None

    def parse(self, title: str | None, company: str | None = None) -> TitleParse:
        key = norm_key(title)
        if not key:
            return TitleParse(None, LevelBand.UNKNOWN, 0.0, False, "", ())
        is_intern = bool(_INTERN.search(key))
        key = _INTERN.sub(" ", key)

        tokens: list[tuple[int, str]] = []  # (priority, token); lower priority value wins
        for m in _LEVEL_CODE.finditer(key):
            tokens.append((0, m.group(1).replace("level ", "")))
        for m in _ROMAN.finditer(key):
            tokens.append((1, m.group(1)))
        for m in _NUM.finditer(key):
            tokens.append((1, m.group(1)))
        for m in _WORDS.finditer(key):
            tokens.append((2, m.group(1)))
        base = _LEVEL_CODE.sub(" ", key)
        base = _ROMAN.sub(" ", base)
        base = _NUM.sub(" ", base)
        base = _WORDS.sub(" ", base)
        base = re.sub(r"\s+", " ", _FILLER.sub(" ", base)).strip()

        level = LevelBand.UNKNOWN
        # Explicit ladder codes / numerals beat words, but "senior/staff/principal" words beat a bare numeral
        # ladder mismatch, so evaluate in priority order and take the first token that maps.
        for _, tok in sorted(tokens, key=lambda t: t[0]):
            band = self._band_for(tok, company)
            if band:
                level = band
                break
        word_tokens = [t for p, t in tokens if p == 2]
        if level in (LevelBand.ENTRY, LevelBand.MID) and any(w in ("senior", "sr") for w in word_tokens):
            level = LevelBand.SENIOR
        if any(w in ("staff",) for w in word_tokens) and level in (LevelBand.ENTRY, LevelBand.MID, LevelBand.UNKNOWN):
            level = LevelBand.STAFF
        if any(w in ("principal", "distinguished", "fellow") for w in word_tokens):
            level = LevelBand.PRINCIPAL

        role, conf = self._match_role(base)
        return TitleParse(role, level, conf, is_intern, base, tuple(t for _, t in tokens))

    @lru_cache(maxsize=16384)  # noqa: B019
    def _match_role(self, base: str) -> tuple[str | None, float]:
        if not base:
            return None, 0.0
        if base in self.title_to_role:
            return self.title_to_role[base], 1.0
        if base in self.request_aliases:
            return self.request_aliases[base], 0.95
        padded = f" {base} "
        for t in self._titles_by_len:
            if len(t) > 3 and f" {t} " in padded:
                # penalize when lots of extra words remain
                extra = len(base.split()) - len(t.split())
                return self.title_to_role[t], max(0.6, 0.92 - 0.05 * extra)
        best = process.extractOne(base, self._titles_by_len, scorer=fuzz.token_set_ratio, score_cutoff=88)
        if best:
            return self.title_to_role[best[0]], round(0.85 * best[1] / 100, 3)
        return None, 0.0


@lru_cache(maxsize=1)
def default_catalog() -> RoleCatalog:
    return RoleCatalog()


def infer_level_from_experience(exp_years: float | None) -> LevelBand:
    """Fallback band when a title carries no level token."""
    if exp_years is None:
        return LevelBand.UNKNOWN
    if exp_years < 2:
        return LevelBand.ENTRY
    if exp_years < 5:
        return LevelBand.MID
    if exp_years < 9:
        return LevelBand.SENIOR
    return LevelBand.STAFF
