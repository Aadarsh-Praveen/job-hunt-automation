"""Recruitee direct-API fetcher.
Endpoint: https://{slug}.recruitee.com/api/offers/
No auth required.
"""
from __future__ import annotations

from datetime import datetime

from src.common.logger import get_logger
from src.fetchers.base import BaseFetcher, JobPosting

logger = get_logger("fetcher.recruitee")


class RecruiteeFetcher(BaseFetcher):
    ats_name = "recruitee"
    BASE_URL = "https://{slug}.recruitee.com/api/offers/"

    async def fetch_one(self, slug: str) -> list[JobPosting]:
        url = self.BASE_URL.format(slug=slug)
        resp = await self._get(url)
        if resp.status_code == 404:
            logger.warning("recruitee_slug_not_found", slug=slug)
            return []
        resp.raise_for_status()
        data = resp.json()

        jobs: list[JobPosting] = []
        for raw in data.get("offers", []):
            location = _build_location(raw)
            jobs.append(
                JobPosting(
                    company=slug,
                    role=(raw.get("title") or "").strip(),
                    jd_link=raw.get("careers_apply_url") or raw.get("careers_url", ""),
                    ats_source="recruitee",
                    location=location,
                    department=raw.get("department", ""),
                    employment_type=raw.get("employment_type_code", ""),
                    description="",
                    date_posted=_parse_iso(raw.get("created_at")),
                    raw=raw,
                )
            )
        logger.info(
            "recruitee_fetched",
            slug=slug,
            count=len(jobs),
            empty_locations=sum(1 for j in jobs if not j.location),
        )
        return jobs


def _build_location(raw: dict) -> str:
    """Recruitee has multi-location postings in 'locations', plus top-level city/country."""
    parts: list[str] = []

    # Multi-location postings
    locations = raw.get("locations") or []
    if isinstance(locations, list):
        seen = set()
        for loc in locations:
            if not isinstance(loc, dict):
                continue
            city = (loc.get("city") or "").strip()
            country = (loc.get("country") or "").strip()
            s = ", ".join(p for p in [city, country] if p)
            if s and s not in seen:
                seen.add(s)
                parts.append(s)

    # Fall back to top-level city/country if no locations array
    if not parts:
        city = (raw.get("city") or "").strip()
        country = (raw.get("country_code") or raw.get("country") or "").strip()
        s = ", ".join(p for p in [city, country] if p)
        if s:
            parts.append(s)

    # Remote flag
    is_remote = bool(raw.get("remote")) or (raw.get("on_site") is False)
    base = " | ".join(parts)
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
