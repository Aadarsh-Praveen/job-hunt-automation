"""Dedupe: check if a job is already in the Notion Applications DB.

Strategy: load all existing dedupe_hash values into memory once per run,
then check incoming jobs against that set. Avoids one Notion query per job.
"""

from __future__ import annotations

from src.common.logger import get_logger
from src.config import get_settings
from src.tracker.notion_client import query_database

logger = get_logger("tracker.dedupe")


async def load_existing_hashes() -> set[str]:
    """Return the set of dedupe_hash values already in the Applications DB."""
    settings = get_settings()
    if not settings.notion_applications_db_id:
        raise RuntimeError("NOTION_APPLICATIONS_DB_ID not set in .env")

    pages = await query_database(settings.notion_applications_db_id)

    hashes: set[str] = set()
    for page in pages:
        props = page.get("properties", {})
        hash_prop = props.get("Dedupe Hash", {})
        rich_text = hash_prop.get("rich_text", [])
        if rich_text:
            value = rich_text[0].get("plain_text", "").strip()
            if value:
                hashes.add(value)

    logger.info("dedupe_hashes_loaded", count=len(hashes))
    return hashes