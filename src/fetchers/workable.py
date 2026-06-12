"""Workable direct-API fetcher.
Endpoint: https://apply.workable.com/api/v3/accounts/{slug}/jobs
No auth required.
"""
from __future__ import annotations

from datetime import datetime

from src.common.logger import get_logger
from src.fetchers.base import BaseFetcher, JobPosting

logger = get_logger("fetcher.workable")


class WorkableFetcher(BaseFetcher):
    ats_name = "workable"
    BASE_URL = "https://apply.workable.com/api/v3/accounts/{slug}/jobs"

    async def fetch_one(self, slug: str) -> list[JobPosting]:
        url = self.BASE_URL.format(slug=slug)
        resp = await self._get(url, params={"limit": 200})
        if resp.status_code == 404:
            logger.warning("workable_slug_not_found", slug=slug)
            return []
        resp.raise_for_status()
        data = resp.json()

        jobs: list[JobPosting] = []
        for raw in data.get("results", []):
            location = _build_location(raw)
            jobs.append(
                JobPosting(
                    company=slug,
                    role=(raw.get("title") or "").strip(),
                    jd_link=raw.get("application_url") or raw.get("url", ""),
                    ats_source="workable",
                    location=location,
                    department=raw.get("department", ""),
                    employment_type=raw.get("employment_type", ""),
                    description="",
                    date_posted=_parse_iso(raw.get("published_on") or raw.get("created_at")),
                    raw=raw,
                )
            )
        logger.info(
            "workable_fetched",
            slug=slug,
            count=len(jobs),
            empty_locations=sum(1 for j in jobs if not j.location),
        )
        return jobs


def _build_location(raw: dict) -> str:
    """Workable nests location under raw['location']."""
    loc = raw.get("location") or {}
    if not isinstance(loc, dict):
        return ""

    parts: list[str] = []
    city = (loc.get("city") or "").strip()
    region = (loc.get("region") or "").strip()
    country_code = (loc.get("country_code") or "").strip().upper()
    country = (loc.get("country") or "").strip()

    if city:
        parts.append(city)
    if region:
        parts.append(region)
    # Include country only if non-US (else redundant noise)
    if country and country_code not in ("US", "USA", ""):
        parts.append(country)

    base = ", ".join(parts) if parts else ""
    is_remote = (
        loc.get("telecommuting")
        or (loc.get("workplace_type") or "").lower() in ("remote", "fully_remote")
    )
    if is_remote:
        return f"{base} | Remote" if base else "Remote"
    return base


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
