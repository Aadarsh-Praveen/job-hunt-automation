"""One-time setup: create the 3 Notion databases for the tracker.

Prerequisites:
1. Create a Notion integration at https://www.notion.so/my-integrations
2. Copy the "Internal Integration Token" into NOTION_API_KEY in .env
3. Create (or pick) a Notion page where the databases will live
4. On that page: click ••• → Connections → Add connections → select your integration
5. Copy the page ID from the URL (the 32-char hex at the end, before any '?')
6. Set NOTION_PARENT_PAGE_ID in .env

Usage:
    uv run python scripts/seed_notion.py

After it runs, copy the printed DB IDs into your .env:
    NOTION_COMPANIES_DB_ID=...
    NOTION_APPLICATIONS_DB_ID=...
    NOTION_CONTACTS_DB_ID=...
"""

from __future__ import annotations

import argparse
import asyncio

from src.common.logger import get_logger
from src.config import get_settings
from src.tracker.notion_client import create_database
from src.tracker.schemas import (
    APPLICATIONS_SCHEMA,
    COMPANIES_SCHEMA,
    CONTACTS_SCHEMA,
)

logger = get_logger("seed_notion")


async def main(parent_page_id: str) -> None:
    logger.info("seeding_notion_databases", parent_page_id=parent_page_id)

    companies = await create_database(
        parent_page_id, "Companies", COMPANIES_SCHEMA
    )
    applications = await create_database(
        parent_page_id, "Applications", APPLICATIONS_SCHEMA
    )
    contacts = await create_database(
        parent_page_id, "Contacts", CONTACTS_SCHEMA
    )

    print("\n" + "=" * 72)
    print("  ✓ Notion databases created successfully")
    print("=" * 72)
    print("\n  Copy these into your .env file:\n")
    print(f"    NOTION_COMPANIES_DB_ID={companies['id'].replace('-', '')}")
    print(f"    NOTION_APPLICATIONS_DB_ID={applications['id'].replace('-', '')}")
    print(f"    NOTION_CONTACTS_DB_ID={contacts['id'].replace('-', '')}")
    print()
    print("  View them in your Notion workspace under the parent page.")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--parent-page-id",
        default="",
        help="Notion page ID where DBs will be created (defaults to NOTION_PARENT_PAGE_ID env var)",
    )
    args = parser.parse_args()

    parent_page_id = args.parent_page_id or get_settings().notion_parent_page_id
    if not parent_page_id:
        raise SystemExit(
            "ERROR: provide --parent-page-id or set NOTION_PARENT_PAGE_ID in .env"
        )
    asyncio.run(main(parent_page_id))