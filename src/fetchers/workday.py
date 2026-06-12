"""Workday direct-API fetcher.

Workday slugs are dicts: {"tenant": "nvidia", "wd": 5, "site": "NVIDIAExternalCareerSite"}
Endpoint: https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
POST request with JSON body. No auth required.
"""
from __future__ import annotations

import httpx

from src.common.logger import get_logger
from src.fetchers.base import BaseFetcher, JobPosting

logger = get_logger("fetcher.workday")

WORKDAY_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; JobHuntBot/1.0)",
}


class WorkdayFetcher(BaseFetcher):
    ats_name = "workday"

    async def fetch_one(self, slug) -> list[JobPosting]:
        # Workday "slugs" are dicts, not strings
        if not isinstance(slug, dict):
            logger.warning("workday_invalid_slug", slug=str(slug))
            return []

        tenant = (slug.get("tenant") or "").strip()
        wd = slug.get("wd", 5)
        site = (slug.get("site") or "").strip()

        if not tenant or not site:
            logger.warning("workday_missing_fields", slug=slug)
            return []

        api_url = f"https://{tenant}.wd{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        base_view = f"https://{tenant}.wd{wd}.myworkdayjobs.com/en-US/{site}"

        all_postings: list[dict] = []
        offset = 0
        limit = 20  # Workday caps page size at 20
        max_jobs = 400  # Safety cap per tenant

        async with httpx.AsyncClient(timeout=30.0, headers=WORKDAY_HEADERS) as client:
            while offset < max_jobs:
                body = {
                    "appliedFacets": {},
                    "limit": limit,
                    "offset": offset,
                    "searchText": "",
                }
                try:
                    resp = await client.post(api_url, json=body)
                except httpx.RequestError as e:
                    logger.warning(
                        "workday_request_failed",
                        tenant=tenant, site=site, error=str(e),
                    )
                    break

                if resp.status_code == 404:
                    logger.warning("workday_not_found", tenant=tenant, site=site)
                    return []
                if resp.status_code != 200:
                    logger.warning(
                        "workday_bad_status",
                        tenant=tenant, site=site, status=resp.status_code,
                    )
                    break

                data = resp.json()
                postings = data.get("jobPostings") or []
                if not postings:
                    break
                all_postings.extend(postings)
                total = data.get("total", 0)
                offset += limit
                if offset >= total:
                    break

        jobs: list[JobPosting] = []
        for raw in all_postings:
            title = (raw.get("title") or "").strip()
            if not title:
                continue
            external_path = raw.get("externalPath") or ""
            jobs.append(
                JobPosting(
                    company=tenant,
                    role=title,
                    jd_link=base_view + external_path if external_path else "",
                    ats_source="workday",
                    location=(raw.get("locationsText") or "").strip(),
                    department="",
                    employment_type="",
                    description="",
                    date_posted=None,  # Workday returns relative dates ("3 days ago"); skip
                    raw=raw,
                )
            )

        logger.info(
            "workday_fetched",
            tenant=tenant, site=site, count=len(jobs),
            empty_locations=sum(1 for j in jobs if not j.location),
        )
        return jobs
