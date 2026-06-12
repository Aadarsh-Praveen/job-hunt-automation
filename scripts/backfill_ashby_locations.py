"""Backfill empty Ashby locations in Notion by re-fetching from the Ashby API.

For each Notion row that has empty Location and an ashbyhq.com JD link:
  1. Re-fetch all Ashby jobs (uses the fixed AshbyFetcher)
  2. Match the Notion row by jd_link
  3. Write the now-populated location back to Notion
  4. Archive if the resolved location fails the filter

Usage:
    uv run python scripts/backfill_ashby_locations.py            # dry-run
    uv run python scripts/backfill_ashby_locations.py --apply    # write + archive
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx
from dotenv import load_dotenv

from scripts.cleanup_locations import location_passes
from src.common.logger import get_logger
from src.config import load_locations, load_slugs
from src.fetchers.ashby import AshbyFetcher

logger = get_logger("backfill_ashby_locations")
load_dotenv()

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
APPS_DB_ID = os.environ["NOTION_APPLICATIONS_DB_ID"]
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def extract_text(prop: dict, kind: str) -> str:
    return "".join(it.get("plain_text", "") for it in prop.get(kind, []))


async def query_empty_ashby_rows(client: httpx.AsyncClient) -> list[dict]:
    rows: list[dict] = []
    cursor = None
    while True:
        body: dict = {
            "page_size": 100,
            "filter": {
                "and": [
                    {"property": "Location", "rich_text": {"is_empty": True}},
                    {"property": "JD Link", "url": {"contains": "ashbyhq.com"}},
                ]
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
        json={"properties": {"Location": {"rich_text": [{"text": {"content": location}}]}}},
    )
    r.raise_for_status()


async def archive_page(client: httpx.AsyncClient, page_id: str) -> None:
    r = await client.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        json={"archived": True},
    )
    r.raise_for_status()


async def main(apply: bool = False) -> None:
    loc_cfg = load_locations()

    # 1. Re-fetch all Ashby data with the fixed fetcher
    print("Re-fetching Ashby job listings...")
    ab = AshbyFetcher(load_slugs("ashby"))
    ashby_jobs = await ab.fetch_all()
    by_url: dict[str, str] = {
        j.jd_link: j.location for j in ashby_jobs if j.jd_link and j.location
    }
    print(f"  Got {len(by_url)} Ashby jobs with location data\n")

    async with httpx.AsyncClient(headers=NOTION_HEADERS, timeout=30.0) as notion:
        print("Querying Notion for empty-location Ashby rows...")
        rows = await query_empty_ashby_rows(notion)
        print(f"  Found {len(rows)} rows to backfill\n")

        if not rows:
            print("Nothing to backfill.\n")
            return

        plan = []
        for row in rows:
            props = row.get("properties", {})
            jd_link = props.get("JD Link", {}).get("url") or ""
            role = extract_text(props.get("Role", {}), "title")
            new_loc = by_url.get(jd_link, "")
            passes = location_passes(new_loc, loc_cfg) if new_loc else None
            plan.append({
                "id": row["id"],
                "role": role,
                "new_location": new_loc,
                "passes": passes,
            })

        # Preview
        for r in plan[:30]:
            if r["passes"] is True:
                action = "KEEP"
            elif r["passes"] is False:
                action = "ARCHIVE"
            else:
                action = "SKIP"
            print(f"  {action:<8} | {r['role'][:48]:<48} | {r['new_location'] or '(not in API — job removed?)'}")
        if len(plan) > 30:
            print(f"  ... and {len(plan) - 30} more")

        keep = [r for r in plan if r["passes"] is True]
        archive = [r for r in plan if r["passes"] is False]
        skip = [r for r in plan if r["passes"] is None]

        print(f"\n  KEEP (update only):    {len(keep)}")
        print(f"  ARCHIVE (foreign):     {len(archive)}")
        print(f"  SKIP (gone from API):  {len(skip)}")

        if not apply:
            print("\n[DRY RUN] Re-run with --apply to write changes.\n")
            return

        print("\nApplying...")
        for r in plan:
            try:
                if r["passes"] is True:
                    await update_location(notion, r["id"], r["new_location"])
                elif r["passes"] is False:
                    await update_location(notion, r["id"], r["new_location"])
                    await archive_page(notion, r["id"])
                await asyncio.sleep(0.35)
            except Exception as e:
                logger.warning("update_failed", page_id=r["id"], error=str(e))

        print(f"\n✓ Backfill complete: {len(keep)} updated, {len(archive)} archived, {len(skip)} skipped\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
