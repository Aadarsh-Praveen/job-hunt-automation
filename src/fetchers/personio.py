"""Personio direct-API fetcher.
Endpoint: https://{slug}.jobs.personio.com/xml
Returns XML. No auth.
"""
from __future__ import annotations

from datetime import datetime
from xml.etree import ElementTree as ET

from src.common.logger import get_logger
from src.fetchers.base import BaseFetcher, JobPosting

logger = get_logger("fetcher.personio")


class PersonioFetcher(BaseFetcher):
    ats_name = "personio"
    BASE_URL = "https://{slug}.jobs.personio.com/xml"

    async def fetch_one(self, slug: str) -> list[JobPosting]:
        url = self.BASE_URL.format(slug=slug)
        resp = await self._get(url)
        if resp.status_code == 404:
            logger.warning("personio_slug_not_found", slug=slug)
            return []
        resp.raise_for_status()

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            logger.warning("personio_parse_failed", slug=slug, error=str(e))
            return []

        jobs: list[JobPosting] = []
        for position in root.findall("position"):
            title = (position.findtext("name") or "").strip()
            if not title:
                continue
            pid = (position.findtext("id") or "").strip()
            jobs.append(
                JobPosting(
                    company=slug,
                    role=title,
                    jd_link=f"https://{slug}.jobs.personio.com/job/{pid}" if pid else "",
                    ats_source="personio",
                    location=(position.findtext("office") or "").strip(),
                    department=(position.findtext("department") or "").strip(),
                    employment_type=(position.findtext("employmentType") or "").strip(),
                    description="",
                    date_posted=_parse_iso(position.findtext("createdAt")),
                    raw={},
                )
            )
        logger.info(
            "personio_fetched",
            slug=slug,
            count=len(jobs),
            empty_locations=sum(1 for j in jobs if not j.location),
        )
        return jobs


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
