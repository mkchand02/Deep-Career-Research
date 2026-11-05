"""Synthetic source: generates fake profiles in-memory (development / demos / tests)."""

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar

from dcr.adapters.base import Candidate, JobPosting, JobPostingAdapter, SourceAdapter
from dcr.adapters.manual_import import record_to_profile
from dcr.adapters.registry import register_adapter
from dcr.models import RawProfile, StudyQuery
from dcr.synth.generator import SyntheticDataset, SyntheticGenerator

_CACHE: dict[tuple[int, int, str, str], SyntheticDataset] = {}


def synthetic_dataset(n: int, seed: int, ref: date, company: str) -> SyntheticDataset:
    key = (n, seed, ref.isoformat(), company)
    if key not in _CACHE:
        _CACHE[key] = SyntheticGenerator(seed=seed, reference_date=ref, target_company=company).generate(n)
    return _CACHE[key]


@register_adapter
class SyntheticAdapter(SourceAdapter):
    """Fake people. params: ``n`` (default 1000), ``seed`` (default 42)."""

    name: ClassVar[str] = "synthetic"
    description: ClassVar[str] = "Generated fake profiles for development (no real people)"
    tos_note: ClassVar[str] = "Synthetic data, no restrictions."

    def _data(self, query: StudyQuery) -> SyntheticDataset:
        return synthetic_dataset(
            int(self.params.get("n", 1000)), int(self.params.get("seed", 42)), query.ref_date(), query.company
        )

    async def search(self, query: StudyQuery, limit: int) -> list[Candidate]:
        self._ds = self._data(query)
        return [Candidate(self.name, str(i)) for i in range(min(limit, len(self._ds.records)))]

    async def fetch(self, candidate: Candidate) -> RawProfile | None:
        idx = int(candidate.ref)
        return record_to_profile(self._ds.records[idx], self.name, idx)


@register_adapter
class SyntheticJobsAdapter(JobPostingAdapter):
    """Fake job postings for the target company."""

    name: ClassVar[str] = "synthetic_jobs"
    description: ClassVar[str] = "Generated fake job postings"

    async def postings(self, query: StudyQuery, limit: int) -> list[JobPosting]:
        ds = synthetic_dataset(
            int(self.params.get("n", 1000)), int(self.params.get("seed", 42)), query.ref_date(), query.company
        )
        out = []
        for p in ds.job_postings[:limit]:
            out.append(
                JobPosting(
                    source=self.name,
                    company=p["company"],
                    title=p["title"],
                    location=p["location"],
                    skills=list(p["skills"]),
                    posted_at=_dt(p["posted_at"]),
                    url=p["url"],
                )
            )
        return out


def _dt(s: Any) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(s) if isinstance(s, str) else s
