"""Manual CSV/JSON import adapter for data the user obtained legally.

JSON: a list of profile objects (or ``{"profiles": [...]}``) using the :class:`RawProfile` field names.
CSV: one row per profile with these columns (all optional except one of ``full_name``/``source_ref``):

``source_ref, full_name, headline, location, country, pronouns, links, github_username, skills,
current_title, current_company, current_start_date, current_location, positions_json, education_json,
projects_json, summary``

List columns (``links``, ``skills``) use ``|`` or ``;`` as separators. ``*_json`` columns hold JSON arrays
of position/education/project objects.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, ClassVar

from dcr.adapters.base import Candidate, SourceAdapter, SourceError
from dcr.adapters.registry import register_adapter
from dcr.models import RawProfile, StudyQuery


def _split(v: str | None) -> list[str]:
    if not v:
        return []
    sep = "|" if "|" in v else ";"
    return [x.strip() for x in v.split(sep) if x.strip()]


def _json_list(v: str | None) -> list[Any]:
    if not v:
        return []
    try:
        data = json.loads(v)
    except json.JSONDecodeError as exc:
        raise SourceError(f"invalid JSON column: {exc}") from exc
    return data if isinstance(data, list) else [data]


def csv_row_to_record(row: dict[str, str]) -> dict[str, Any]:
    """Convert one flat CSV row to a RawProfile-shaped dict."""
    positions = _json_list(row.get("positions_json"))
    if row.get("current_title") and row.get("current_company"):
        positions.append(
            {
                "title": row["current_title"],
                "company": row["current_company"],
                "start_date": row.get("current_start_date") or None,
                "end_date": None,
                "location": row.get("current_location") or row.get("location") or None,
            }
        )
    return {
        "source_ref": row.get("source_ref") or "",
        "full_name": row.get("full_name") or None,
        "headline": row.get("headline") or None,
        "location": row.get("location") or None,
        "country": row.get("country") or None,
        "pronouns": row.get("pronouns") or None,
        "links": _split(row.get("links")),
        "github_username": row.get("github_username") or None,
        "skills": _split(row.get("skills")),
        "positions": positions,
        "education": _json_list(row.get("education_json")),
        "projects": _json_list(row.get("projects_json")),
        "summary": row.get("summary") or None,
        "source_hint": row.get("source_hint") or None,
        "synthetic": (row.get("synthetic") or "").lower() in ("1", "true", "yes"),
    }


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Load records from a ``.json``/``.jsonl``/``.csv`` file."""
    p = Path(path)
    if not p.exists():
        raise SourceError(f"manual import file not found: {p}")
    if p.suffix.lower() == ".csv":
        with p.open(newline="", encoding="utf-8") as fh:
            return [csv_row_to_record(r) for r in csv.DictReader(fh)]
    if p.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    data = json.loads(p.read_text())
    if isinstance(data, dict):
        data = data.get("profiles", [])
    if not isinstance(data, list):
        raise SourceError("JSON import must be a list of profiles or {'profiles': [...]}")
    return data


def record_to_profile(rec: dict[str, Any], source: str, idx: int) -> RawProfile:
    ref = rec.get("source_ref") or hashlib.sha1(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()[:16]
    hint = rec.get("source_hint")
    fields = {k: v for k, v in rec.items() if k in RawProfile.model_fields and k not in ("source", "source_ref")}
    return RawProfile(source=f"{source}:{hint}" if hint else source, source_ref=str(ref or idx), **fields)


@register_adapter
class ManualImportAdapter(SourceAdapter):
    """Reads profiles from a CSV/JSON file given by ``params.path``."""

    name: ClassVar[str] = "manual"
    description: ClassVar[str] = "CSV/JSON upload of legally obtained data"
    tos_note: ClassVar[str] = "You are responsible for having the right to process imported data."

    def __init__(self, ctx: Any, params: dict[str, Any] | None = None) -> None:
        super().__init__(ctx, params)
        self._records: list[dict[str, Any]] | None = None

    def _load(self) -> list[dict[str, Any]]:
        if self._records is None:
            if "records" in self.params:
                self._records = list(self.params["records"])
            elif "path" in self.params:
                self._records = load_records(self.params["path"])
            else:
                raise SourceError("manual adapter needs params.path (or params.records)")
        return self._records

    async def search(self, query: StudyQuery, limit: int) -> list[Candidate]:
        return [Candidate(self.name, str(i)) for i in range(min(limit, len(self._load())))]

    async def fetch(self, candidate: Candidate) -> RawProfile | None:
        idx = int(candidate.ref)
        return record_to_profile(self._load()[idx], self.name, idx)
