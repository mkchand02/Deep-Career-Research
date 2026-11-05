"""Manual job-postings import (CSV/JSON): title, location, skills, posted_at, url, description."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from dcr.adapters.base import JobPosting, JobPostingAdapter, SourceError
from dcr.adapters.registry import register_adapter
from dcr.models import StudyQuery
from dcr.pipeline.normalize.skills import default_skill_normalizer


@register_adapter
class ManualJobsAdapter(JobPostingAdapter):
    name: ClassVar[str] = "manual_jobs"
    description: ClassVar[str] = "CSV/JSON import of job postings"

    async def postings(self, query: StudyQuery, limit: int) -> list[JobPosting]:
        path = self.params.get("path")
        if not path or not Path(path).exists():
            raise SourceError(f"manual_jobs: file not found: {path}")
        p = Path(path)
        if p.suffix.lower() == ".csv":
            with p.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
        else:
            data = json.loads(p.read_text())
            rows = data.get("postings", []) if isinstance(data, dict) else data
        sn = default_skill_normalizer()
        out = []
        for r in rows[:limit]:
            skills = r.get("skills") or []
            if isinstance(skills, str):
                skills = [s.strip() for s in skills.replace(";", "|").split("|") if s.strip()]
            if not skills and r.get("description"):
                skills = sn.find_in_text(r["description"])
            posted = r.get("posted_at")
            out.append(
                JobPosting(
                    source=self.name,
                    company=r.get("company") or query.company,
                    title=r.get("title", ""),
                    location=r.get("location"),
                    skills=list(skills),
                    posted_at=datetime.fromisoformat(posted) if posted else None,
                    url=r.get("url"),
                )
            )
        return out
