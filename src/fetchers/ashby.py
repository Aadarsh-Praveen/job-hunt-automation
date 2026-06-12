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
            location = _build_location(raw)

            jobs.append(
                JobPosting(
                    company=slug,
                    role=raw.get("title", "").strip(),
                    jd_link=raw.get("jobUrl", ""),
                    ats_source="ashby",
                    location=location,
                    department=raw.get("departmentName", ""),
                    employment_type=raw.get("employmentType", ""),
                    description=(raw.get("descriptionPlain") or "")[:5000],
                    date_posted=date_posted,
                    raw=raw,
                )
            )
        logger.info(
            "ashby_fetched",
            slug=slug,
            count=len(jobs),
            empty_locations=sum(1 for j in jobs if not j.location),
        )
        return jobs


def _build_location(raw: dict) -> str:
    """Build a combined location string from all Ashby location fields.

    Ashby splits location info across multiple fields:
      - locationName: primary office
      - secondaryLocations: additional offices for multi-location postings
      - isRemote: boolean — append 'Remote' if true
      - address.postalAddress: structured fallback when locationName is empty
    """
    parts: list[str] = []

    # 1. Primary location
    primary = (raw.get("locationName") or "").strip()
    if primary:
        parts.append(primary)

    # 2. Secondary locations (multi-location postings — e.g. "SF | NY | Seattle")
    for sec in raw.get("secondaryLocations") or []:
        if not isinstance(sec, dict):
            continue
        loc = (sec.get("locationName") or "").strip()
        if loc and loc not in parts:
            parts.append(loc)

    # 3. Remote flag — append 'Remote' unless something already mentions it
    if raw.get("isRemote"):
        if not any("remote" in p.lower() for p in parts):
            parts.append("Remote")

    # 4. Fallback to structured postalAddress when nothing else was found
    if not parts:
        addr = raw.get("address") or {}
        postal = addr.get("postalAddress") if isinstance(addr, dict) else None
        if isinstance(postal, dict):
            city = (postal.get("addressLocality") or "").strip()
            region = (postal.get("addressRegion") or "").strip()
            country = (postal.get("addressCountry") or "").strip()
            fallback = ", ".join(p for p in [city, region, country] if p)
            if fallback:
                parts.append(fallback)

    return " | ".join(parts)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
