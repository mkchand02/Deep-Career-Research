"""Source adapter interface and shared HTTP plumbing.

Every adapter gets an :class:`AdapterContext` with:

* a token-bucket rate limiter (``rate_limit_per_minute`` from ``config.yaml``),
* retries with exponential backoff on 429/5xx/network errors (honouring ``Retry-After``),
* an HTTP response cache (SQLite ``http_cache``, TTL per source),
* an honest User-Agent and the per-source terms-of-service note.

Adapters must never log in to sites that forbid automated access, bypass CAPTCHAs or bot
detection, or ignore robots.txt.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar, Literal

import httpx
import structlog
from sqlalchemy.orm import Session, sessionmaker
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from dcr.config import AppConfig, Secrets, SourceConfig
from dcr.db.models import HttpCache
from dcr.models import RawProfile, StudyQuery
from dcr.timeutil import utcnow

log = structlog.get_logger(__name__)


class SourceError(Exception):
    """A recoverable failure in one source (the pipeline continues with other sources)."""


class RetryableHTTPError(Exception):
    def __init__(self, status: int, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


@dataclass
class Candidate:
    """A pointer to a profile discovered by ``search``; ``hint`` carries adapter-specific data."""

    source: str
    ref: str
    hint: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobPosting:
    source: str
    company: str
    title: str
    location: str | None = None
    skills: list[str] = field(default_factory=list)
    description: str | None = None
    posted_at: datetime | None = None
    url: str | None = None


@dataclass
class HttpResult:
    status: int
    text: str
    headers: dict[str, str]
    url: str
    from_cache: bool = False

    def json(self) -> Any:
        return json.loads(self.text)


class TokenBucket:
    """Async token bucket: ``rate_per_minute`` sustained, burst of ``burst``."""

    def __init__(self, rate_per_minute: float, burst: int = 1) -> None:
        self.rate = max(rate_per_minute, 0.001) / 60.0
        self.capacity = max(burst, 1)
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RetryableHTTPError | httpx.TransportError)


class AdapterContext:
    """Shared services for an adapter instance."""

    def __init__(
        self,
        name: str,
        config: AppConfig,
        secrets: Secrets,
        session_factory: sessionmaker[Session] | None = None,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        self.name = name
        self.app_config = config
        self.config: SourceConfig = config.source(name)
        self.secrets = secrets
        self.session_factory = session_factory
        self._client = client
        self._owns_client = client is None
        self.bucket = TokenBucket(self.config.rate_limit_per_minute, burst=int(self.config.options.get("burst", 2)))
        self.requests_made = 0
        self.user_agent = config.user_agent

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=20.0, follow_redirects=True, headers={"User-Agent": self.user_agent}
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # ---------------------------------------------------------------- caching
    @staticmethod
    def _cache_key(url: str, params: dict[str, Any] | None) -> str:
        raw = url + "?" + json.dumps(params or {}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_get(self, key: str) -> HttpResult | None:
        if not self.session_factory or self.config.cache_ttl_hours <= 0:
            return None
        with self.session_factory() as s:
            row = s.get(HttpCache, key)
            if row and row.fetched_at > utcnow() - timedelta(hours=self.config.cache_ttl_hours):
                return HttpResult(row.status, row.body, dict(row.headers), row.url, from_cache=True)
        return None

    def _cache_put(self, key: str, res: HttpResult) -> None:
        if not self.session_factory or self.config.cache_ttl_hours <= 0:
            return
        with self.session_factory() as s:
            row = s.get(HttpCache, key) or HttpCache(url_hash=key)
            row.url, row.status, row.body = res.url, res.status, res.text
            row.headers = {k: v for k, v in res.headers.items() if k.lower() in ("content-type", "etag")}
            row.fetched_at = utcnow()
            s.merge(row)
            s.commit()

    # ------------------------------------------------------------------- http
    async def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> HttpResult:
        """Rate-limited, retried, cached GET. Raises :class:`SourceError` on final failure."""
        key = self._cache_key(url, params)
        if use_cache and (cached := self._cache_get(key)):
            return cached

        async def _once() -> HttpResult:
            await self.bucket.acquire()
            self.requests_made += 1
            resp = await self.client.get(url, params=params, headers=headers)
            if resp.status_code == 429 or resp.status_code >= 500:
                ra = resp.headers.get("Retry-After")
                retry_after = float(ra) if ra and ra.isdigit() else None
                if retry_after:
                    await asyncio.sleep(min(retry_after, 60))
                raise RetryableHTTPError(resp.status_code, retry_after)
            return HttpResult(resp.status_code, resp.text, dict(resp.headers), str(resp.url))

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max(1, self.config.max_retries)),
                wait=wait_exponential(multiplier=0.5, max=8),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    result = await _once()
        except (RetryableHTTPError, httpx.HTTPError) as exc:
            raise SourceError(f"{self.name}: GET {url} failed: {exc}") from exc
        if result.status not in ok_statuses:
            raise SourceError(f"{self.name}: GET {url} returned {result.status}")
        if use_cache:
            self._cache_put(key, result)
        return result


class SourceAdapter(ABC):
    """Profile source. Implement ``search`` and ``fetch``; register with ``@register_adapter``."""

    name: ClassVar[str]
    kind: ClassVar[Literal["profiles", "jobs"]] = "profiles"
    requires_secret: ClassVar[str | None] = None  # attribute name on Secrets
    default_enabled: ClassVar[bool] = True
    tos_note: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def __init__(self, ctx: AdapterContext, params: dict[str, Any] | None = None) -> None:
        self.ctx = ctx
        self.params = params or {}

    def available(self) -> tuple[bool, str]:
        """Whether the adapter can run (enabled + credentials present)."""
        cfg = self.ctx.app_config.sources.get(self.name)
        enabled = cfg.enabled if cfg else self.default_enabled
        if not enabled:
            return False, f"{self.name} is disabled in config.yaml"
        if self.requires_secret and not getattr(self.ctx.secrets, self.requires_secret, None):
            return False, f"{self.name} requires {self.requires_secret.upper()} in .env"
        return True, "ok"

    @abstractmethod
    async def search(self, query: StudyQuery, limit: int) -> list[Candidate]:
        """Discover candidate profiles for ``query`` (at most ``limit``)."""

    @abstractmethod
    async def fetch(self, candidate: Candidate) -> RawProfile | None:
        """Fetch one candidate's raw profile (``None`` if unavailable)."""

    async def aclose(self) -> None:
        await self.ctx.aclose()


class JobPostingAdapter(ABC):
    """Public job-postings source (complementary, non-personal signal)."""

    name: ClassVar[str]
    kind: ClassVar[Literal["profiles", "jobs"]] = "jobs"
    requires_secret: ClassVar[str | None] = None
    default_enabled: ClassVar[bool] = True
    tos_note: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def __init__(self, ctx: AdapterContext, params: dict[str, Any] | None = None) -> None:
        self.ctx = ctx
        self.params = params or {}

    def available(self) -> tuple[bool, str]:
        cfg = self.ctx.app_config.sources.get(self.name)
        enabled = cfg.enabled if cfg else self.default_enabled
        if not enabled:
            return False, f"{self.name} is disabled in config.yaml"
        if self.requires_secret and not getattr(self.ctx.secrets, self.requires_secret, None):
            return False, f"{self.name} requires {self.requires_secret.upper()} in .env"
        return True, "ok"

    @abstractmethod
    async def postings(self, query: StudyQuery, limit: int) -> list[JobPosting]:
        """Return public job postings for the query's company."""

    async def aclose(self) -> None:
        await self.ctx.aclose()
