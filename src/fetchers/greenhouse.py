"""Greenhouse direct-API fetcher.

Endpoint: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
No auth required.
"""

from __future__ import annotations

from datetime import datetime

from src.common.logger import get_logger
from src.fetchers.base import BaseFetcher, JobPosting

logger = get_logger("fetcher.greenhouse")


class GreenhouseFetcher(BaseFetcher):
    ats_name = "greenhouse"
    BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

    async def fetch_one(self, slug: str) -> list[JobPosting]:
        url = self.BASE_URL.format(slug=slug)
        resp = await self._get(url, params={"content": "true"})
        if resp.status_code == 404:
            logger.warning("greenhouse_slug_not_found", slug=slug)
            return []
        resp.raise_for_status()
        data = resp.json()

        jobs: list[JobPosting] = []
        for raw in data.get("jobs", []):
            posted_str = raw.get("updated_at") or raw.get("first_published")
            date_posted = _parse_iso(posted_str)

            location_obj = raw.get("location") or {}
            location = location_obj.get("name", "") if isinstance(location_obj, dict) else ""

            departments = ", ".join(
                d.get("name", "") for d in (raw.get("departments") or [])
            )

            jobs.append(
                JobPosting(
                    company=slug,
                    role=raw.get("title", "").strip(),
                    jd_link=raw.get("absolute_url", ""),
                    ats_source="greenhouse",
                    location=location,
                    department=departments,
                    description=(raw.get("content") or "")[:5000],
                    date_posted=date_posted,
                    raw=raw,
                )
            )

        logger.info("greenhouse_fetched", slug=slug, count=len(jobs))
        return jobs


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
