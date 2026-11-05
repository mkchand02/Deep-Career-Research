"""SQLAlchemy ORM tables.

Layers:

* **Personal (TTL-purged)**: ``raw_records``, ``persons``, ``person_sources``, ``normalized_profiles``,
  ``enriched_profiles``, ``filter_decisions``. Every row carries ``expires_at``.
* **Non-personal (retained)**: ``studies``, ``runs``, ``aggregates``, ``job_postings``, ``audit_log``,
  ``enrichment_cache``, ``http_cache``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from dcr.timeutil import utcnow


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Study(Base):
    __tablename__ = "studies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    query_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int | None] = mapped_column(ForeignKey("studies.id"), nullable=True)
    query_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|completed|failed
    stage: Mapped[str] = mapped_column(String(40), default="")
    stage_counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    errors: Mapped[list[Any]] = mapped_column(JSON, default=list)
    cost: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RawRecord(Base):
    __tablename__ = "raw_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(50))
    ref_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Person(Base):
    __tablename__ = "persons"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    anon_id: Mapped[str] = mapped_column(String(20))
    resolution_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class PersonSource(Base):
    __tablename__ = "person_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id", ondelete="CASCADE"), index=True)
    raw_record_id: Mapped[int] = mapped_column(ForeignKey("raw_records.id", ondelete="CASCADE"))
    match_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class NormalizedProfileRow(Base):
    __tablename__ = "normalized_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    anon_id: Mapped[str] = mapped_column(String(20))
    passed_filter: Mapped[bool] = mapped_column(default=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class EnrichedProfileRow(Base):
    __tablename__ = "enriched_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    anon_id: Mapped[str] = mapped_column(String(20))
    method: Mapped[str] = mapped_column(String(20))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class FilterDecisionRow(Base):
    __tablename__ = "filter_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    anon_id: Mapped[str] = mapped_column(String(20))
    passed: Mapped[bool] = mapped_column(default=False)
    checks: Mapped[list[Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class AggregateRow(Base):
    """Pre-computed, already k-anonymized report for one filter selection. Survives purge."""

    __tablename__ = "aggregates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    filter_key: Mapped[str] = mapped_column(String(400))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    __table_args__ = (Index("ix_aggregates_run_key", "run_id", "filter_key", unique=True),)


class JobPostingRow(Base):
    __tablename__ = "job_postings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    canonical_role: Mapped[str | None] = mapped_column(String(60), nullable=True)
    level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    country: Mapped[str | None] = mapped_column(String(4), nullable=True)
    skills: Mapped[list[Any]] = mapped_column(JSON, default=list)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLog(Base):
    """Run audit trail. Never contains names or URLs of people, only counts and anon IDs."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    stage: Mapped[str] = mapped_column(String(40))
    level: Mapped[str] = mapped_column(String(10), default="info")
    event: Mapped[str] = mapped_column(String(200))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EnrichmentCache(Base):
    __tablename__ = "enrichment_cache"
    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    output: Mapped[dict[str, Any]] = mapped_column(JSON)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class HttpCache(Base):
    __tablename__ = "http_cache"
    url_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer)
    headers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    body: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


PERSONAL_TABLES = (
    FilterDecisionRow,
    EnrichedProfileRow,
    NormalizedProfileRow,
    PersonSource,
    Person,
    RawRecord,
)
