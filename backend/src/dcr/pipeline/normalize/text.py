"""Shared text-normalization helpers."""

from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s&+#/.]")
_WS = re.compile(r"\s+")
LEGAL_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|co|company|plc|gmbh|ag|sa|pbc|lp|llp|pvt|private)\b\.?", re.I
)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_key(s: str | None) -> str:
    """Lowercase, accent-free, punctuation-light, single-spaced key."""
    if not s:
        return ""
    s = strip_accents(s).lower().replace("&", " & ")
    s = _PUNCT.sub(" ", s)
    s = s.replace(".", " ").replace("/", " / ")
    return _WS.sub(" ", s).strip()


def company_key(s: str | None) -> str:
    """Key for company matching: drops legal suffixes and parentheticals."""
    if not s:
        return ""
    s = re.sub(r"\(.*?\)", " ", s)
    s = LEGAL_SUFFIXES.sub(" ", strip_accents(s))
    return norm_key(s).replace(" & ", " and ")
