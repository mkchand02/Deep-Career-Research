"""Study filter with an explainable audit trail.

Each person gets one :class:`FilterCheck` per criterion (pass / fail / unknown + reason). Reasons never
contain names, URLs or exact personal dates, only categories and relative values.
"""

from __future__ import annotations

import hashlib
import math

from dcr.models import CheckResult, FilterCheck, FilterDecision, NormalizedProfile, RoleSpec, StudyQuery
from dcr.pipeline.normalize.geo import region_matches
from dcr.roles.expansion import role_matches

CHECKS = ("company", "country", "start_date", "experience", "role", "confidence")


def _check(name: str, result: CheckResult, reason: str) -> FilterCheck:
    return FilterCheck(check=name, result=result, reason=reason)


def evaluate(p: NormalizedProfile, q: StudyQuery, roles: list[RoleSpec], target_canonical: str) -> FilterDecision:
    checks: list[FilterCheck] = []
    P, F, U = CheckResult.PASS, CheckResult.FAIL, CheckResult.UNKNOWN

    # company
    if p.current_company == target_canonical:
        checks.append(_check("company", P, f"current employer matches {target_canonical}"))
    elif p.current_company:
        checks.append(_check("company", F, "current employer is a different company"))
    else:
        checks.append(_check("company", U, "no current position found"))

    # country / region
    if p.country is None:
        checks.append(_check("country", U, "location unknown"))
    elif p.country != q.country:
        checks.append(_check("country", F, f"located in {p.country}, not {q.country}"))
    elif q.regions and not region_matches(p.location, q.regions):
        checks.append(_check("country", F, "outside requested regions"))
    else:
        checks.append(_check("country", P, f"located in {q.country}" + (" (region match)" if q.regions else "")))

    # start date
    start, end = q.window()
    if p.start_date is None:
        checks.append(_check("start_date", U, "start date unknown"))
    elif start <= p.start_date <= end:
        checks.append(_check("start_date", P, f"joined {(end - p.start_date).days}d before window end"))
    else:
        rel = (end - p.start_date).days
        checks.append(
            _check(
                "start_date",
                F,
                f"joined {rel}d before window end (window {(end - start).days}d)"
                if rel >= 0
                else "start date after window",
            )
        )

    # experience (completed years)
    exp = p.exp_years if q.exclude_internships else p.exp_years_incl_internships
    if exp is None:
        checks.append(_check("experience", U, "experience unknown"))
    else:
        yrs = math.floor(exp + 1e-9)
        hi = q.exp_max if q.exp_max is not None else math.inf
        if q.exp_min <= yrs <= hi:
            checks.append(_check("experience", P, f"{yrs} completed years in range"))
        else:
            checks.append(_check("experience", F, f"{yrs} completed years outside range"))

    # role
    spec = role_matches(roles, p.canonical_role, p.level)
    if p.canonical_role is None:
        checks.append(_check("role", F, "title not recognized"))
    elif spec:
        lvl = f"{p.level.value}{' (inferred)' if p.level_inferred else ''}"
        checks.append(_check("role", P, f"{spec.display_name} @ {lvl}"))
    else:
        checks.append(_check("role", F, f"role {p.canonical_role} @ {p.level.value} not requested"))

    # confidence
    conf = min(p.resolution_confidence, p.role_confidence or 0.0, p.company_confidence or 0.0)
    if p.level_inferred:
        conf *= 0.9
    if conf >= q.min_confidence:
        checks.append(_check("confidence", P, f"confidence {conf:.2f}"))
    else:
        checks.append(_check("confidence", F, f"confidence {conf:.2f} < {q.min_confidence:.2f}"))

    passed = True
    for c in checks:
        if c.result == P:
            continue
        if c.result == U and c.check == "start_date" and q.include_unknown_start_date:
            continue
        passed = False
    return FilterDecision(anon_id=p.anon_id, passed=passed, checks=checks)


def sample(passed: list[NormalizedProfile], cap: int, salt: str = "") -> list[NormalizedProfile]:
    """Deterministic (hash-ordered) sample of at most ``cap`` profiles."""
    if len(passed) <= cap:
        return passed
    return sorted(passed, key=lambda p: hashlib.sha256((salt + p.anon_id).encode()).hexdigest())[:cap]


def reason_summary(decisions: list[FilterDecision]) -> dict[str, dict[str, int]]:
    """Counts of fail/unknown by check (first failing check is the primary reason)."""
    out: dict[str, dict[str, int]] = {c: {"pass": 0, "fail": 0, "unknown": 0} for c in CHECKS}
    primary: dict[str, int] = {}
    for d in decisions:
        for c in d.checks:
            out[c.check][c.result.value] += 1
        failed = d.failed_checks()
        if not d.passed and failed:
            primary[failed[0]] = primary.get(failed[0], 0) + 1
    out["_primary_reason"] = primary
    return out
