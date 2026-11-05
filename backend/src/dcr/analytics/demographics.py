"""Aggregate-only demographic estimates.

* **Pronouns.** Self-stated pronouns only ("she/her", "he/him", "they/them"), reported as written, with
  "not stated" as its own bucket.
* **Name-based estimate** (optional, off by default). For people without stated pronouns, the probability
  that a first name belongs to a woman is looked up in a public name-frequency table (US SSA baby names).
  Only the *aggregate* expected share is reported, with a 95% CI that combines per-name uncertainty and
  sampling error. Names missing from the table, or too rare, go to an "unknown" bucket. Estimates are
  never attached to individuals, never shown per person, and never inferred from photos.
* **International proxies.** The share whose highest degree was earned outside the target country, and
  the share with prior work experience outside it. Visa or citizenship status cannot be known from
  profiles and is never reported.
"""

from __future__ import annotations

import csv
import io
import math
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dcr.analytics.kanon import distribution, proportion
from dcr.analytics.stats import Z95
from dcr.config import data_path
from dcr.models import AnalysisRecord

NAME_TABLE_FILE = "name_gender_ssa.csv"
SSA_URL = "https://www.ssa.gov/oact/babynames/names.zip"


def pronoun_group(pronouns: str | None) -> str | None:
    if not pronouns:
        return None
    p = pronouns.lower().replace(" ", "")
    if p.startswith("she"):
        return "she"
    if p.startswith("he"):
        return "he"
    if p.startswith("they") or p.startswith("xe") or p.startswith("ze"):
        return "they"
    return None


@dataclass
class NameEstimate:
    p_female: float
    count: int


class NameGenderTable:
    """first name → (female count, male count)."""

    def __init__(self, rows: dict[str, tuple[int, int]], source: str) -> None:
        self.rows = rows
        self.source = source

    @classmethod
    def load(cls, path: Path | None = None) -> NameGenderTable | None:
        p = path or data_path(NAME_TABLE_FILE)
        if not p.exists():
            return None
        rows: dict[str, tuple[int, int]] = {}
        with p.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows[r["name"].lower()] = (int(r["female"]), int(r["male"]))
        return cls(rows, source=p.name)

    def estimate(self, first_name: str | None, min_count: int) -> NameEstimate | None:
        if not first_name:
            return None
        row = self.rows.get(first_name.lower())
        if not row:
            return None
        f, m = row
        total = f + m
        if total < min_count:
            return None
        # Jeffreys-smoothed probability
        return NameEstimate(p_female=(f + 0.5) / (total + 1), count=total)


def build_ssa_table(zip_bytes: bytes, out: Path, min_year: int = 1960) -> int:
    """Aggregate SSA ``yobYYYY.txt`` files (name,sex,count) into ``name,female,male``."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            fname = info.filename
            if not (fname.startswith("yob") and fname.endswith(".txt")):
                continue
            if int(fname[3:7]) < min_year:
                continue
            for line in zf.read(info).decode("utf-8").splitlines():
                name, sex, c = line.split(",")
                counts[name.lower()][0 if sex == "F" else 1] += int(c)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "female", "male"])
        for name, (f, m) in sorted(counts.items()):
            w.writerow([name, f, m])
    return len(counts)


def pronoun_distribution(records: list[AnalysisRecord], k: int) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    labels = {"she": "she/her (self-stated)", "he": "he/him (self-stated)", "they": "they/them (self-stated)"}
    for r in records:
        counts[labels[r.pronoun_group] if r.pronoun_group else "not stated"] += 1
    return distribution(
        "pronouns_self_stated",
        counts,
        len(records),
        k,
        partition=True,
        note="Only publicly self-stated pronouns; most profiles do not state any.",
    ).model_dump()


def name_based_estimate(records: list[AnalysisRecord], k: int) -> dict[str, Any]:
    """Aggregate expected share of women among people without stated pronouns."""
    pool = [r for r in records if r.pronoun_group is None]
    est = [r.p_female_name for r in pool if r.name_estimable and r.p_female_name is not None]
    m = len(est)
    unknown = len(pool) - m
    base = {
        "label": "ESTIMATE: name-based, aggregate only. Not a measurement of any individual's gender.",
        "pool_n": len(pool) if len(pool) >= k else None,
        "unknown_n": unknown if unknown >= k or unknown == 0 else None,
        "unknown_suppressed": 0 < unknown < k,
    }
    if m < k:
        return base | {"suppressed": True, "note": f"fewer than {k} estimable names"}
    p_hat = sum(est) / m
    var_class = sum(p * (1 - p) for p in est) / (m * m)  # per-name uncertainty
    var_samp = p_hat * (1 - p_hat) / m  # sampling
    half = Z95 * math.sqrt(var_class + var_samp)
    return base | {
        "suppressed": False,
        "estimable_n": m,
        "pct_women_est": round(100 * p_hat, 1),
        "pct_men_est": round(100 * (1 - p_hat), 1),
        "ci_low": round(100 * max(0.0, p_hat - half), 1),
        "ci_high": round(100 * min(1.0, p_hat + half), 1),
        "note": (
            "Probabilistic estimate from a US name-frequency table; biased for names that are uncommon "
            "in the US, and it assumes a binary reference. Treat as rough context only."
        ),
    }


def international_proxies(records: list[AnalysisRecord], country: str, k: int) -> dict[str, Any]:
    known_edu = [r for r in records if r.edu_origin != "unknown"]
    foreign_edu = sum(1 for r in known_edu if r.edu_origin == "foreign")
    known_work = [r for r in records if r.worked_abroad is not None]
    abroad = sum(1 for r in known_work if r.worked_abroad)
    return {
        "highest_degree_outside_country": proportion(
            "highest_degree_outside_country",
            foreign_edu,
            len(known_edu),
            k,
            label=f"% whose highest degree was earned outside {country}",
            note="Proxy only. Says nothing about visa or citizenship status.",
        ).model_dump(),
        "prior_work_outside_country": proportion(
            "prior_work_outside_country",
            abroad,
            len(known_work),
            k,
            label=f"% with prior full-time work experience outside {country}",
            note="Proxy only; based on stated job locations.",
        ).model_dump(),
        "disclaimer": "Visa or citizenship status is not knowable from profiles and is never estimated.",
    }
