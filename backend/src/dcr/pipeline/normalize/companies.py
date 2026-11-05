"""Company canonicalization (aliases, subsidiaries, company type)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import yaml
from rapidfuzz import fuzz, process

from dcr.config import data_path
from dcr.pipeline.normalize.text import company_key


@dataclass(frozen=True)
class CompanyMatch:
    canonical: str
    type: str
    confidence: float
    known: bool


class CompanyNormalizer:
    """Maps free-text employer names to canonical companies."""

    def __init__(self, table: dict[str, dict[str, object]] | None = None) -> None:
        if table is None:
            table = yaml.safe_load(data_path("companies.yaml").read_text())["companies"]
        self._by_key: dict[str, tuple[str, str]] = {}
        for canonical, info in table.items():
            ctype = str(info.get("type", "other"))
            aliases = info.get("aliases", []) or []
            assert isinstance(aliases, list)
            for name in [canonical, *aliases]:
                self._by_key[company_key(str(name))] = (canonical, ctype)
        self._keys = list(self._by_key)

    @lru_cache(maxsize=8192)  # noqa: B019 - normalizers are long-lived singletons
    def match(self, raw: str | None) -> CompanyMatch:
        key = company_key(raw)
        if not key:
            return CompanyMatch("", "unknown", 0.0, False)
        if key in self._by_key:
            c, t = self._by_key[key]
            return CompanyMatch(c, t, 1.0, True)
        if len(key) >= 5:
            cutoff = 88 if len(key) >= 8 else 92  # tolerate a transposition in longer names
            best = process.extractOne(key, self._keys, scorer=fuzz.ratio, score_cutoff=cutoff)
            if best:
                c, t = self._by_key[best[0]]
                return CompanyMatch(c, t, round(best[1] / 100, 3), True)
        # Unknown employer: keep a readable canonical form.
        return CompanyMatch((raw or "").strip(), "other", 0.5, False)

    def type_of(self, raw: str | None) -> str:
        return self.match(raw).type


class TargetCompany:
    """Matcher for the study's target company (+ user-supplied aliases/subsidiaries)."""

    def __init__(self, name: str, aliases: list[str], normalizer: CompanyNormalizer) -> None:
        self.normalizer = normalizer
        base = normalizer.match(name)
        self.canonical = base.canonical if base.known else name.strip()
        self.alias_keys = {company_key(a) for a in [name, *aliases] if a}

    def matches(self, raw: str | None) -> tuple[bool, float, str]:
        """(is_target, confidence, canonical_seen)."""
        key = company_key(raw)
        if key and key in self.alias_keys:
            return True, 1.0, self.canonical
        m = self.normalizer.match(raw)
        if m.canonical == self.canonical:
            return True, m.confidence, m.canonical
        return False, m.confidence, m.canonical


@lru_cache(maxsize=1)
def default_company_normalizer() -> CompanyNormalizer:
    return CompanyNormalizer()
