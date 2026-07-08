"""JobSpy Google Jobs fetcher — best-effort, Google Jobs only.

Standalone (bulk call per role keyword), not BaseFetcher — same pattern as
ApifyUniversalFetcher. python-jobspy scrapes Google's job search results
directly; there's no official API. Google changes that layout periodically
and breaks this until upstream patches it — see
https://github.com/speedyapply/JobSpy/issues/302 (Google Jobs scraping
broken since Sep 2025, confirmed still broken as of this writing: every
query returns 0 rows with "initial cursor not found"). When that happens
this returns [] and logs a warning; it never crashes the pipeline.

Google Jobs only. Do NOT add LinkedIn/Indeed/Glassdoor/ZipRecruiter here.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime

import pandas as pd

from src.common.logger import get_logger
from src.fetchers.base import JobPosting

logger = get_logger("fetcher.jobspy_google")

ROLE_KEYWORDS = [
    "AI Engineer",
    "ML Engineer",
    "Data Scientist",
    "Data Analyst",
    "Gen AI Engineer",
    "AI Developer",
]

RESULTS_WANTED = 50
HOURS_OLD = 48


class JobSpyGoogleFetcher:
    """Best-effort Google Jobs aggregator fetch, one keyword at a time."""

    ats_name = "jobspy_google"

    async def fetch_all(self) -> list[JobPosting]:
        try:
            records = await self._scrape_all_keywords()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "jobspy_google_failed", error=str(e), error_type=type(e).__name__
            )
            return []

        jobs = [_to_job_posting(r) for r in records]
        logger.info("jobspy_google_fetched", count=len(jobs))
        return jobs

    async def _scrape_all_keywords(self) -> list[dict]:
        records: list[dict] = []
        for kw in ROLE_KEYWORDS:
            try:
                df = await asyncio.to_thread(_scrape_one, kw)
                records.extend(df.to_dict("records"))
            except Exception as e:  # noqa: BLE001
                logger.warning("jobspy_keyword_failed", keyword=kw, error=str(e))
                continue
        return records


def _scrape_one(keyword: str) -> pd.DataFrame:
    from jobspy import scrape_jobs

    return scrape_jobs(
        site_name=["google"],
        search_term=keyword,
        google_search_term=f"{keyword} jobs near United States since yesterday",
        results_wanted=RESULTS_WANTED,
        country_indeed="USA",  # required arg even for google-only scraping
        hours_old=HOURS_OLD,
    )


def _to_job_posting(raw: dict) -> JobPosting:
    return JobPosting(
        company=str(raw.get("company") or ""),
        role=str(raw.get("title") or "").strip(),
        jd_link=str(raw.get("job_url") or ""),
        ats_source="jobspy_google",
        location=str(raw.get("location") or ""),
        description=str(raw.get("description") or "")[:5000],
        date_posted=_parse_date(raw.get("date_posted")),
        raw=raw,
    )


def _parse_date(value: object) -> datetime | None:
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return None
