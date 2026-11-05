"""Small statistics helpers (no SciPy dependency)."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of pre-sorted values (q in [0, 1])."""
    if not sorted_vals:
        raise ValueError("empty")
    pos = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return float(sorted_vals[lo])
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo))


def bootstrap_median_ci(values: Sequence[float], iters: int = 500, seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap 95% CI for the median."""
    if not values:
        return math.nan, math.nan
    rng = random.Random(seed)
    n = len(values)
    meds = []
    for _ in range(iters):
        s = sorted(values[rng.randrange(n)] for _ in range(n))
        meds.append(percentile(s, 0.5))
    meds.sort()
    return percentile(meds, 0.025), percentile(meds, 0.975)


def chapman_estimate(n1: int, n2: int, m: int) -> tuple[float, float] | None:
    """Chapman capture-recapture population estimate and its standard error."""
    if n1 <= 0 or n2 <= 0:
        return None
    est = (n1 + 1) * (n2 + 1) / (m + 1) - 1
    var = (n1 + 1) * (n2 + 1) * (n1 - m) * (n2 - m) / ((m + 1) ** 2 * (m + 2))
    return est, math.sqrt(max(var, 0.0))
