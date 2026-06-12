"""Append-only writes to Notion databases.

Converts our typed Pydantic rows into Notion's property-dict format
and creates new pages. Never updates or deletes — append only.
"""

from __future__ import annotations

from datetime import datetime

from src.common.logger import get_logger
from src.config import get_settings
from src.tracker.notion_client import create_page
from src.tracker.schemas import ApplicationRow

logger = get_logger("tracker.writers")


# ---------------------------------------------------------------------------
# Property builders — convert Python values into Notion property dicts.
# Notion is picky: empty strings on URLs cause errors, dates need ISO format.
# ---------------------------------------------------------------------------

def _title(value: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": (value or "")[:2000]}}]}


def _rich_text(value: str) -> dict:
    if not value:
        return {"rich_text": []}
    return {"rich_text": [{"type": "text", "text": {"content": value[:2000]}}]}


def _url(value: str) -> dict:
    return {"url": value if value else None}


def _select(value: str) -> dict:
    return {"select": {"name": value} if value else None}


def _date(value: datetime | None) -> dict:
    if value is None:
        return {"date": None}
    return {"date": {"start": value.isoformat()}}


def _number(value: float | None) -> dict:
    return {"number": value}


# ---------------------------------------------------------------------------
# Row → Notion property dict
# ---------------------------------------------------------------------------

def application_to_notion(row: ApplicationRow) -> dict:
    """Convert an ApplicationRow into Notion property dict for create_page."""
    return {
        "Role": _title(row.role),
        "Company": _rich_text(row.company),
        "JD Link": _url(row.jd_link),
        "ATS Source": _select(row.ats_source),
        "Date Posted": _date(row.date_posted),
        "Date Discovered": _date(row.date_discovered),
        "Date Applied": _date(row.date_applied),
        "Resume Version": _url(row.resume_version_url),
        "Resume LaTeX": _url(row.resume_latex_url),
        "Diff Link": _url(row.diff_url),
        "Self ATS Score": _number(row.self_ats_score),
        "Status": _select(row.status),
        "Location": _rich_text(row.location),
        "Department": _rich_text(row.department),
        "Notes": _rich_text(row.notes),
        "Dedupe Hash": _rich_text(row.dedupe_hash),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def write_application(row: ApplicationRow) -> dict:
    """Create a new Applications row in Notion. Returns the created page."""
    settings = get_settings()
    if not settings.notion_applications_db_id:
        raise RuntimeError("NOTION_APPLICATIONS_DB_ID not set in .env")

    properties = application_to_notion(row)
    page = await create_page(settings.notion_applications_db_id, properties)
    return page