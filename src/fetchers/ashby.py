"""Ashby direct-API fetcher.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
No auth required.
"""

from __future__ import annotations

from datetime import datetime

from src.common.logger import get_logger
from src.fetchers.base import BaseFetcher, JobPosting

logger = get_logger("fetcher.ashby")


class AshbyFetcher(BaseFetcher):
    ats_name = "ashby"
    BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

    async def fetch_one(self, slug: str) -> list[JobPosting]:
        url = self.BASE_URL.format(slug=slug)
        resp = await self._get(url, params={"includeCompensation": "true"})
        if resp.status_code == 404:
            logger.warning("ashby_slug_not_found", slug=slug)
            return []
        resp.raise_for_status()
        data = resp.json()

        jobs: list[JobPosting] = []
        for raw in data.get("jobs", []):
            date_posted = _parse_iso(raw.get("publishedAt"))

            jobs.append(
                JobPosting(
                    company=slug,
                    role=raw.get("title", "").strip(),
                    jd_link=raw.get("jobUrl", ""),
                    ats_source="ashby",
                    location=raw.get("locationName", ""),
                    department=raw.get("departmentName", ""),
                    employment_type=raw.get("employmentType", ""),
                    description=(raw.get("descriptionPlain") or "")[:5000],
                    date_posted=date_posted,
                    raw=raw,
                )
            )

        logger.info("ashby_fetched", slug=slug, count=len(jobs))
        return jobs


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
