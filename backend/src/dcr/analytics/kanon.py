"""k-anonymity enforcement for every published number.

Rules (k ≥ 5, enforced in config):

1. **Population rule.** A filtered view (any cross-filter intersection) is published only if it has
   at least k people. It also needs, for every *parent* view (the same filters with one removed), a
   difference that is either 0 or at least k. Otherwise someone could subtract two views to isolate
   fewer than k people (a differencing attack).
2. **Primary cell suppression.** Any bucket with fewer than k people is suppressed.
3. **Complementary suppression** (partitions only). If exactly one bucket of a partition is suppressed,
   the smallest remaining bucket is suppressed too, so the hidden cell can't be recovered as
   ``total - sum(visible)``.
4. **Proportions.** A yes/no proportion is published only if both the "yes" and the "no" groups have
   at least k people.
5. **Numeric summaries** (quartiles, medians) need n ≥ k. Minimum and maximum are never published.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from dcr.analytics.stats import wilson
from dcr.models import Bucket, Distribution, Proportion


def population_ok(n: int, k: int, parent_ns: Iterable[int] = ()) -> tuple[bool, str]:
    if n < k:
        return False, f"fewer than {k} people match these filters"
    for pn in parent_ns:
        diff = pn - n
        if 0 < diff < k:
            return False, f"suppressed to prevent differencing (a related view differs by fewer than {k} people)"
    return True, ""


def distribution(
    name: str,
    counts: Mapping[str, int],
    base_n: int,
    k: int,
    *,
    partition: bool,
    top: int | None = None,
    note: str = "",
    with_ci: bool = True,
) -> Distribution:
    """Build a k-anonymized distribution.

    ``partition=True`` means buckets are mutually exclusive and sum to ``base_n``
    (an ``unknown`` bucket should be included by the caller). Multi-label distributions (skills, schools)
    report "% of profiles" and only apply primary suppression.
    """
    if base_n < k:
        return Distribution(
            name=name,
            base_n=None,
            buckets=[],
            multi_label=not partition,
            suppressed_buckets=0,
            note=f"suppressed: fewer than {k} people",
        )
    items = sorted(((key, c) for key, c in counts.items() if c > 0), key=lambda kv: (-kv[1], kv[0]))
    suppressed = {key for key, c in items if c < k}
    if partition and len(suppressed) == 1:
        visible = [(key, c) for key, c in items if key not in suppressed]
        if visible:
            suppressed.add(min(visible, key=lambda kv: (kv[1], kv[0]))[0])
    buckets: list[Bucket] = []
    for key, c in items:
        if key in suppressed:
            continue
        lo, hi = wilson(c, base_n) if with_ci else (None, None)
        buckets.append(
            Bucket(
                key=key,
                n=c,
                pct=round(100 * c / base_n, 1),
                ci_low=None if lo is None else round(100 * lo, 1),
                ci_high=None if hi is None else round(100 * hi, 1),
            )
        )
    if top is not None:
        buckets = buckets[:top]
    return Distribution(
        name=name,
        base_n=base_n,
        buckets=buckets,
        multi_label=not partition,
        suppressed_buckets=len(suppressed),
        note=note,
    )


def proportion(name: str, yes: int, base_n: int, k: int, *, label: str = "", note: str = "") -> Proportion:
    if base_n < k or yes < k or (base_n - yes) < k:
        return Proportion(
            name=name,
            n=None,
            base_n=base_n if base_n >= k else None,
            pct=None,
            suppressed=True,
            label=label,
            note=note or f"suppressed: a group has fewer than {k} people",
        )
    lo, hi = wilson(yes, base_n)
    return Proportion(
        name=name,
        n=yes,
        base_n=base_n,
        pct=round(100 * yes / base_n, 1),
        ci_low=round(100 * lo, 1),
        ci_high=round(100 * hi, 1),
        label=label,
        note=note,
    )


def check_report_cells(report_sections: dict[str, object], k: int) -> list[str]:
    """Return a list of violations (bucket n < k) found anywhere in a report; used by tests and exports."""
    problems: list[str] = []

    def walk(obj: object, path: str) -> None:
        if isinstance(obj, dict):
            if "buckets" in obj and isinstance(obj["buckets"], list):
                for b in obj["buckets"]:
                    if isinstance(b, dict) and b.get("n") is not None and b["n"] < k:
                        problems.append(f"{path}.{b.get('key')}={b['n']}")
            if obj.get("n") is not None and isinstance(obj.get("n"), int) and obj["n"] < k and "buckets" not in obj:
                problems.append(f"{path}.n={obj['n']}")
            for key, v in obj.items():
                walk(v, f"{path}.{key}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")

    walk(report_sections, "report")
    return problems
