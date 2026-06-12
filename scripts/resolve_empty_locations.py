"""Backfill empty Notion Application locations using gpt-4o-mini.

For each row with empty Location:
  1. Fetch the JD page
  2. Ask gpt-4o-mini to extract the location
  3. Write the resolved location back to Notion
  4. If the resolved location fails the location filter, archive the row

Usage:
    uv run python scripts/resolve_empty_locations.py            # dry-run
    uv run python scripts/resolve_empty_locations.py --apply    # write + archive
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx
from dotenv import load_dotenv

from src.common.llm_location import HTTP_HEADERS, resolve_location
from src.common.logger import get_logger
from src.config import load_locations
from scripts.cleanup_locations import location_passes

logger = get_logger("resolve_empty_locations")
load_dotenv()

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
APPS_DB_ID = os.environ["NOTION_APPLICATIONS_DB_ID"]
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def extract_text(prop: dict, kind: str) -> str:
    items = prop.get(kind, [])
    return "".join(it.get("plain_text", "") for it in items)


async def query_empty_location_rows(client: httpx.AsyncClient) -> list[dict]:
    rows: list[dict] = []
    cursor = None
    while True:
        body: dict = {
            "page_size": 100,
            "filter": {
                "property": "Location",
                "rich_text": {"is_empty": True},
            },
        }
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


async def update_location(client: httpx.AsyncClient, page_id: str, location: str) -> None:
    r = await client.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        json={
            "properties": {
                "Location": {"rich_text": [{"text": {"content": location}}]}
            }
        },
    )
    r.raise_for_status()


async def archive_page(client: httpx.AsyncClient, page_id: str) -> None:
    r = await client.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        json={"archived": True},
    )
    r.raise_for_status()


async def main(apply: bool = False) -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set in .env")

    loc_cfg = load_locations()

    async with httpx.AsyncClient(headers=NOTION_HEADERS, timeout=30.0) as notion:
        print("Querying Notion for rows with empty Location...")
        rows = await query_empty_location_rows(notion)
        print(f"  Found {len(rows)} empty-location rows\n")

        if not rows:
            print("Nothing to resolve.\n")
            return

        async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=20.0,
                                     follow_redirects=True) as jd_client:
            sem = asyncio.Semaphore(10)

            async def process(row: dict) -> dict:
                props = row.get("properties", {})
                jd_link = props.get("JD Link", {}).get("url") or ""
                role = extract_text(props.get("Role", {}), "title")
                if not jd_link:
                    return {"id": row["id"], "role": role, "location": "", "passes": None}
                async with sem:
                    loc = await resolve_location(jd_link, jd_client)
                passes = location_passes(loc, loc_cfg) if loc else None
                return {"id": row["id"], "role": role, "location": loc, "passes": passes}

            resolved = await asyncio.gather(*[process(r) for r in rows])

        # Preview
        print(f"  {'Action':<8} | {'Role':<48} | Resolved Location")
        print(f"  {'-'*8} + {'-'*48} + {'-'*30}")
        for r in resolved[:30]:
            if r["passes"] is True:
                action = "KEEP"
            elif r["passes"] is False:
                action = "ARCHIVE"
            else:
                action = "SKIP"
            print(f"  {action:<8} | {r['role'][:48]:<48} | {r['location'] or '(not found)'}")
        if len(resolved) > 30:
            print(f"  ... and {len(resolved) - 30} more")

        keep = [r for r in resolved if r["passes"] is True]
        archive = [r for r in resolved if r["passes"] is False]
        skip = [r for r in resolved if r["passes"] is None]

        print(f"\n  KEEP (update only):    {len(keep)}")
        print(f"  ARCHIVE (foreign):     {len(archive)}")
        print(f"  SKIP (no location):    {len(skip)}")

        if not apply:
            print("\n[DRY RUN] Re-run with --apply to write changes.\n")
            return

        print("\nApplying...")
        for r in resolved:
            try:
                if r["passes"] is True:
                    await update_location(notion, r["id"], r["location"])
                elif r["passes"] is False:
                    await update_location(notion, r["id"], r["location"])
                    await archive_page(notion, r["id"])
                await asyncio.sleep(0.35)
            except Exception as e:
                logger.warning("update_failed", page_id=r["id"], error=str(e))

        print("\n" + "=" * 72)
        print("  ✓ Resolve complete")
        print("=" * 72)
        print(f"  Updated:    {len(keep) + len(archive)}")
        print(f"  Archived:   {len(archive)}")
        print(f"  Skipped:    {len(skip)}")
        print("=" * 72 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Actually update Notion + archive foreign rows.")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
