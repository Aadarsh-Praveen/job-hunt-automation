"""Apify universal ATS fetcher — bovi/greenhouse-lever-ashby-job-scraper.

One bulk actor call across the actor's curated "ai-ml" company preset
(Greenhouse + Lever + Ashby), not a per-slug sweep — doesn't subclass
BaseFetcher, which is built around fetch_one(slug).

MAX_TOTAL_ITEMS is passed as max_items to actor.call() — the Apify
platform stops the run once that many dataset items are produced, so
this caps both what we ingest AND the pay-per-result cost.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from apify_client import ApifyClientAsync

from src.common.logger import get_logger
from src.config import get_settings
from src.fetchers.base import JobPosting

logger = get_logger("fetcher.apify_universal")

PRESET_LISTS = ["ai-ml"]
MAX_JOBS_PER_COMPANY = 25
MAX_TOTAL_ITEMS = 500


class ApifyUniversalFetcher:
    """Bulk multi-ATS fetch via Apify's curated company presets."""

    ats_name = "apify_universal"

    async def fetch_all(self) -> list[JobPosting]:
        settings = get_settings()
        if not settings.apify_api_key or not settings.apify_universal_actor_id:
            logger.info("apify_universal_skipped", reason="missing_config")
            return []

        actor_input = {
            "presetLists": PRESET_LISTS,
            "maxJobsPerCompany": MAX_JOBS_PER_COMPANY,
            "includeDescriptions": True,
        }

        logger.info("apify_universal_started", actor=settings.apify_universal_actor_id)

        try:
            client = ApifyClientAsync(token=settings.apify_api_key)
            run = await client.actor(settings.apify_universal_actor_id).call(
                run_input=actor_input,
                max_items=MAX_TOTAL_ITEMS,
                run_timeout=timedelta(seconds=600),
            )
            if run is None:
                logger.warning("apify_universal_failed", error="call_returned_none")
                return []
            dataset = client.dataset(run.default_dataset_id)
            page = await dataset.list_items(limit=MAX_TOTAL_ITEMS)
            items = page.items
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "apify_universal_failed", error=str(e), error_type=type(e).__name__
            )
            return []

        jobs = [_to_job_posting(item) for item in items]
        logger.info("apify_universal_fetched", count=len(jobs))
        return jobs


def _to_job_posting(raw: dict) -> JobPosting:
    return JobPosting(
        company=raw.get("company", ""),
        role=(raw.get("title") or "").strip(),
        jd_link=raw.get("url", ""),
        ats_source="apify_universal",
        location=raw.get("location", ""),
        description=(raw.get("description_text") or "")[:5000],
        date_posted=_parse_iso(raw.get("posted_at")),
        raw=raw,
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
