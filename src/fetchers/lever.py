"""Lever direct-API fetcher.

Endpoint: https://api.lever.co/v0/postings/{slug}?mode=json
No auth required.
"""

from __future__ import annotations

from datetime import datetime

from src.common.logger import get_logger
from src.fetchers.base import BaseFetcher, JobPosting

logger = get_logger("fetcher.lever")


class LeverFetcher(BaseFetcher):
    ats_name = "lever"
    BASE_URL = "https://api.lever.co/v0/postings/{slug}"

    async def fetch_one(self, slug: str) -> list[JobPosting]:
        url = self.BASE_URL.format(slug=slug)
        resp = await self._get(url, params={"mode": "json"})
        if resp.status_code == 404:
            logger.warning("lever_slug_not_found", slug=slug)
            return []
        resp.raise_for_status()
        data = resp.json()

        jobs: list[JobPosting] = []
        for raw in data:
            posted_ts = raw.get("createdAt")
            date_posted = (
                datetime.fromtimestamp(posted_ts / 1000) if posted_ts else None
            )

            categories = raw.get("categories") or {}

            jobs.append(
                JobPosting(
                    company=slug,
                    role=raw.get("text", "").strip(),
                    jd_link=raw.get("hostedUrl", ""),
                    ats_source="lever",
                    location=categories.get("location", ""),
                    department=categories.get("department", ""),
                    employment_type=categories.get("commitment", ""),
                    description=(raw.get("descriptionPlain") or "")[:5000],
                    date_posted=date_posted,
                    raw=raw,
                )
            )

        logger.info("lever_fetched", slug=slug, count=len(jobs))
        return jobs
