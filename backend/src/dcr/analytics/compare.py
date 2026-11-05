"""Compare studies over time (re-runs) or across studies, using stored k-anonymized aggregates only."""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from dcr.analytics.store import get_report
from dcr.config import AppConfig
from dcr.db.models import Run

METRICS: list[tuple[str, tuple[str, ...]]] = [
    ("Roles", ("overview", "roles")),
    ("Levels", ("overview", "levels")),
    ("Experience bands", ("overview", "exp_bands")),
    ("Top skills", ("keywords", "top_skills")),
    ("Hire type", ("experience", "hire_type")),
    ("Highest degree", ("education", "degree_levels")),
    ("Education origin", ("education", "origin")),
    ("Prior company types", ("experience", "prior_company_types")),
    ("Project tags", ("projects", "tags")),
]


def two_prop_p(k1: int, n1: int, k2: int, n2: int) -> float | None:
    """Two-sided p-value of a two-proportion z-test (pooled)."""
    if min(n1, n2) == 0:
        return None
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = (k1 / n1 - k2 / n2) / se
    return math.erfc(abs(z) / math.sqrt(2))


def _get(d: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any] | None:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur if isinstance(cur, dict) else None


def compare_runs(
    sm: sessionmaker[Session], run_ids: list[int], filters: dict[str, str], config: AppConfig
) -> dict[str, Any]:
    if not 2 <= len(run_ids) <= 3:
        raise ValueError("compare 2 or 3 runs")
    runs: list[dict[str, Any]] = []
    for rid in run_ids:
        with sm() as s:
            run = s.get(Run, rid)
            if run is None:
                raise LookupError(f"run {rid} not found")
            meta = {
                "run_id": rid,
                "study_id": run.study_id,
                "created_at": run.created_at.isoformat(),
                "company": run.query_json.get("company"),
                "country": run.query_json.get("country"),
                "window_days": run.query_json.get("joined_within_days"),
            }
        rep = get_report(sm, rid, filters, config)
        runs.append({"meta": meta, "report": rep})
    out: dict[str, Any] = {
        "runs": [r["meta"] | {"n": r["report"].get("n"), "suppressed": r["report"].get("suppressed")} for r in runs],
        "filters": filters,
        "metrics": [],
        "headline": [],
    }
    a, b = runs[0]["report"], runs[-1]["report"]
    # headline: volume normalized per 30 days
    for r in runs:
        m = r["report"].get("methodology") or {}
        w = m.get("window") or {}
        days = None
        if w.get("start") and w.get("end"):
            from datetime import date

            days = (date.fromisoformat(w["end"]) - date.fromisoformat(w["start"])).days or None
        n = r["report"].get("n")
        out["headline"].append(
            {
                "run_id": r["meta"]["run_id"],
                "n": n,
                "window_days": days,
                "per_30_days": round(n * 30 / days, 1) if n and days else None,
            }
        )
    for label, path in METRICS:
        dists = [_get(r["report"].get("sections") or {}, path) for r in runs]
        series = [{"run_id": r["meta"]["run_id"], "dist": d} for r, d in zip(runs, dists, strict=True)]
        deltas = []
        da, db = dists[0], dists[-1]
        if da and db and da.get("base_n") and db.get("base_n") and not a.get("suppressed") and not b.get("suppressed"):
            bb = {x["key"]: x for x in db["buckets"]}
            for x in da["buckets"]:
                y = bb.get(x["key"])
                if not y or x["n"] is None or y["n"] is None:
                    continue
                p = two_prop_p(x["n"], da["base_n"], y["n"], db["base_n"])
                deltas.append(
                    {
                        "key": x["key"],
                        "from_pct": x["pct"],
                        "to_pct": y["pct"],
                        "delta_pp": round(y["pct"] - x["pct"], 1),
                        "p_value": round(p, 4) if p is not None else None,
                        "significant": p is not None and p < 0.05,
                    }
                )
            deltas.sort(key=lambda d: -abs(d["delta_pp"]))
        out["metrics"].append({"label": label, "path": list(path), "series": series, "deltas": deltas[:12]})
    out["note"] = (
        "Deltas compare the first and last selected runs on buckets visible (≥k) in both. p-values come "
        "from two-proportion z-tests and assume independent random samples, which these are not. "
        "Read them as screening signals."
    )
    return out
