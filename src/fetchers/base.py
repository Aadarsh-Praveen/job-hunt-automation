"""Base classes shared by all ATS fetchers."""

from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.common.logger import get_logger

logger = get_logger("fetcher.base")

# Errors worth retrying — transient network/DNS failures
RETRYABLE_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


@dataclass
class JobPosting:
    """Normalized job posting across all ATSes."""

    company: str
    role: str
    jd_link: str
    ats_source: str
    location: str = ""
    department: str = ""
    employment_type: str = ""
    description: str = ""
    date_posted: datetime | None = None
    date_discovered: datetime = field(default_factory=datetime.now)
    raw: dict = field(default_factory=dict)

    @property
    def dedupe_hash(self) -> str:
        """Stable 16-char hash for dedupe — company + role + jd_link."""
        key = f"{self.company.lower()}|{self.role.lower()}|{self.jd_link}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


class BaseFetcher(ABC):
    """Abstract base for all ATS fetchers."""

    ats_name: str = ""

    def __init__(self, slugs: list[str]):
        self.slugs = slugs

    @retry(
        retry=retry_if_exception_type(RETRYABLE_ERRORS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    async def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        """HTTP GET with retry on transient errors."""
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.get(url, params=params)

    @abstractmethod
    async def fetch_one(self, slug: str) -> list[JobPosting]:
        """Fetch all currently-open jobs for a single company slug."""
        ...

    async def fetch_all(self, concurrency: int = 5) -> list[JobPosting]:
        """Fetch jobs across all slugs concurrently, isolating per-company failures."""
        sem = asyncio.Semaphore(concurrency)

        async def _bounded(slug: str) -> tuple[str, list[JobPosting] | Exception]:
            async with sem:
                try:
                    return slug, await self.fetch_one(slug)
                except Exception as e:  # noqa: BLE001
                    return slug, e

        results = await asyncio.gather(*[_bounded(s) for s in self.slugs])

        jobs: list[JobPosting] = []
        for slug, outcome in results:
            if isinstance(outcome, Exception):
                logger.warning(
                    "fetcher_company_failed",
                    ats=self.ats_name,
                    slug=slug,
                    error=str(outcome),
                    error_type=type(outcome).__name__,
                )
                continue
            jobs.extend(outcome)
        return jobs
