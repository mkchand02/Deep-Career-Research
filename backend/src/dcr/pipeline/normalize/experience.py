"""Total professional experience from position intervals."""

from __future__ import annotations

from datetime import date

from dcr.models import Position


def merged_years(intervals: list[tuple[date, date]]) -> float:
    """Total length of the union of [start, end] intervals, in years."""
    if not intervals:
        return 0.0
    ivs = sorted((s, e) for s, e in intervals if e >= s)
    total = 0
    cur_s, cur_e = ivs[0]
    for s, e in ivs[1:]:
        if s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            total += (cur_e - cur_s).days
            cur_s, cur_e = s, e
    total += (cur_e - cur_s).days
    return round(total / 365.25, 2)


def experience_years(
    positions: list[Position], ref: date, *, exclude_internships: bool = True, until: date | None = None
) -> float | None:
    """Years of experience up to ``until`` (default ``ref``), overlapping roles counted once.

    Returns ``None`` when no position has a usable start date.
    """
    cutoff = until or ref
    intervals = []
    for p in positions:
        if exclude_internships and p.is_internship:
            continue
        if not p.start_date or p.start_date > cutoff:
            continue
        end = min(p.end_date or ref, cutoff)
        intervals.append((p.start_date, end))
    if not intervals:
        return None if not any(p.start_date for p in positions) else 0.0
    return merged_years(intervals)
