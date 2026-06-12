"""Notion API wrapper using raw httpx against the stable v2022-06-28 API.

We avoid notion-client entirely because version 2.4+ defaults to Notion API
2025-09-03, which introduced the "data sources" split — properties live on a
data_source nested inside the database, and page creation against the
database_id parent fails to find them. Calling the REST endpoints directly
with Notion-Version: 2022-06-28 gives us stable, well-documented behavior
where properties live on the database itself and parent: {database_id} works.
"""

from __future__ import annotations

import httpx

from src.common.logger import get_logger
from src.config import get_settings

logger = get_logger("notion.client")

NOTION_API_VERSION = "2022-06-28"
NOTION_API_BASE = "https://api.notion.com/v1"


def _headers() -> dict[str, str]:
    settings = get_settings()
    if not settings.notion_api_key:
        raise RuntimeError(
            "NOTION_API_KEY not set in .env — set it before using Notion."
        )
    return {
        "Authorization": f"Bearer {settings.notion_api_key}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


async def create_database(
    parent_page_id: str,
    title: str,
    properties: dict,
) -> dict:
    """Create a database under a parent page."""
    url = f"{NOTION_API_BASE}/databases"
    body = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": properties,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=_headers(), json=body)
        resp.raise_for_status()
        db = resp.json()
    logger.info("notion_database_created", title=title, id=db["id"])
    return db


async def query_database(
    database_id: str,
    filter_obj: dict | None = None,
) -> list[dict]:
    """Query a database, paginating through all results."""
    url = f"{NOTION_API_BASE}/databases/{database_id}/query"
    headers = _headers()

    results: list[dict] = []
    cursor: str | None = None

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            body: dict = {"page_size": 100}
            if filter_obj:
                body["filter"] = filter_obj
            if cursor:
                body["start_cursor"] = cursor

            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

            results.extend(data.get("results", []))

            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

    return results


async def create_page(database_id: str, properties: dict) -> dict:
    """Create a new page (row) in a database."""
    url = f"{NOTION_API_BASE}/pages"
    body = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=_headers(), json=body)
        resp.raise_for_status()
        return resp.json()