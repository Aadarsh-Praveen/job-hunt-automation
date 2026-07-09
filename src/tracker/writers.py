"""Append-only writes to Notion databases.

Converts our typed Pydantic rows into Notion's property-dict format
and creates new pages. Never updates or deletes — append only.
"""

from __future__ import annotations

from datetime import datetime

from src.common.logger import get_logger
from src.config import get_settings
from src.tracker.notion_client import create_page, get_database_properties
from src.tracker.schemas import ApplicationRow

logger = get_logger("tracker.writers")

# Cached per database_id — checked once per process, not once per write.
# "Description" is optional (added after this codebase already had writers
# without it); Notion rejects the ENTIRE page-create payload if it
# references a property the database doesn't have, so this must be
# checked before adding the key, not discovered via a failed write.
_optional_columns_cache: dict[str, set[str]] = {}


async def _has_column(database_id: str, column: str) -> bool:
    if database_id not in _optional_columns_cache:
        try:
            props = await get_database_properties(database_id)
            _optional_columns_cache[database_id] = set(props.keys())
        except Exception as e:  # noqa: BLE001
            logger.warning("notion_schema_check_failed", error=str(e))
            return False
    return column in _optional_columns_cache[database_id]


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

    db_id = settings.notion_applications_db_id
    if await _has_column(db_id, "Description"):
        properties["Description"] = _rich_text(row.description)
    else:
        logger.warning(
            "notion_description_column_missing",
            detail="add a 'Description' rich_text column to Notion to persist JD text",
        )

    page = await create_page(db_id, properties)
    return page