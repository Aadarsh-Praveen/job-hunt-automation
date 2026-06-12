"""Cleanup: archive existing Notion Application rows whose location no longer passes the filter.

Usage:
    uv run python scripts/cleanup_locations.py            # dry-run, preview only
    uv run python scripts/cleanup_locations.py --apply    # actually archive
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx
from dotenv import load_dotenv

from src.common.logger import get_logger
from src.config import load_locations

logger = get_logger("cleanup_locations")
load_dotenv()

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
APPS_DB_ID = os.environ["NOTION_APPLICATIONS_DB_ID"]
HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


import re

def location_passes(location: str, loc_cfg: dict) -> bool:
    """Mirrors scripts/fetch_jobs.py::matches_location — four-tier priority."""
    if not location:
        return True

    loc = location.lower()

    def matches_any(terms: list[str]) -> bool:
        for t in terms:
            t = t.lower().strip()
            if not t:
                continue
            if t.isalpha() and " " not in t:
                if re.search(rf"\b{re.escape(t)}\b", loc):
                    return True
            else:
                if t in loc:
                    return True
        return False

    if matches_any(loc_cfg.get("us_strict_substrings", [])):
        return True
    if matches_any(loc_cfg.get("blocked_substrings", [])):
        return False
    if matches_any(loc_cfg.get("us_abbrev_substrings", [])):
        return True
    if matches_any(loc_cfg.get("weak_allowed_substrings", [])):
        return True
    return False


def extract_text(prop: dict, kind: str) -> str:
    """Extract plain text from a Notion property of the given kind ('title' or 'rich_text')."""
    items = prop.get(kind, [])
    return "".join(it.get("plain_text", "") for it in items)


def extract_company(prop: dict) -> str:
    """Company may be stored as rich_text, select, or relation. Handle gracefully."""
    if rt := prop.get("rich_text"):
        return "".join(it.get("plain_text", "") for it in rt)
    if sel := prop.get("select"):
        return sel.get("name", "")
    if prop.get("relation"):
        return "(relation)"
    return ""


async def query_all_applications(client: httpx.AsyncClient) -> list[dict]:
    """Fetch every (non-archived) row from the Applications DB."""
    rows: list[dict] = []
    cursor = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = await client.post(
            f"https://api.notion.com/v1/databases/{APPS_DB_ID}/query",
            json=body,
        )
        r.raise_for_status()
        data = r.json()
        rows.extend(data["results"])
        if not data.get("has_more"):
            break
        cursor = data["next_cursor"]
    return rows


async def archive_page(client: httpx.AsyncClient, page_id: str) -> None:
    r = await client.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        json={"archived": True},
    )
    r.raise_for_status()


async def main(apply: bool = False) -> None:
    loc_cfg = load_locations()

    async with httpx.AsyncClient(headers=HEADERS, timeout=30.0) as client:
        print("Querying Notion Applications DB...")
        rows = await query_all_applications(client)
        print(f"  Fetched {len(rows)} rows total\n")

        to_archive: list[dict] = []
        for row in rows:
            props = row.get("properties", {})
            location = extract_text(props.get("Location", {}), "rich_text")
            if not location_passes(location, loc_cfg):
                to_archive.append({
                    "id": row["id"],
                    "role": extract_text(props.get("Role", {}), "title"),
                    "company": extract_company(props.get("Company", {})),
                    "location": location,
                })

        if not to_archive:
            print("Nothing to archive. Notion is clean already.\n")
            return

        print(f"Would archive {len(to_archive)} rows. Sample:\n")
        for item in to_archive[:25]:
            print(f"  [{item['company']:15s}] {item['role'][:48]:48s} | {item['location']}")
        if len(to_archive) > 25:
            print(f"  ... and {len(to_archive) - 25} more\n")

        if not apply:
            print(
                f"\n[DRY RUN] Re-run with --apply to actually archive these "
                f"{len(to_archive)} rows.\n"
            )
            return

        print(f"\nArchiving {len(to_archive)} rows...")
        archived = 0
        failed = 0
        for item in to_archive:
            try:
                await archive_page(client, item["id"])
                archived += 1
                await asyncio.sleep(0.35)  # Stay under Notion's ~3 req/sec limit
            except Exception as e:
                logger.warning(
                    "archive_failed",
                    page_id=item["id"],
                    role=item["role"],
                    error=str(e),
                )
                failed += 1

        print("\n" + "=" * 72)
        print("  ✓ Cleanup complete")
        print("=" * 72)
        print(f"  Total rows scanned:  {len(rows)}")
        print(f"  Rows archived:       {archived}")
        if failed:
            print(f"  Failed:              {failed}  (see warnings above)")
        print("=" * 72 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Archive Notion Application rows that fail the current location filter."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually archive rows. Default is dry-run.",
    )
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
