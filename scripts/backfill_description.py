"""One-time backfill: populate Description for existing 'To Apply' rows
written before the Description column existed.

Mirrors scripts/cleanup_with_llm.py — raw httpx, os.environ, 0.35s sleep
between writes. Idempotent: only targets rows where Description is still
empty, so safe to rerun (e.g. to pick up rows that failed the JD fetch
on a prior pass, in case the site is reachable now).

Rows whose JD can't be fetched (bot-protected sites, dead links, SPA
shells with no server-rendered body) are skipped and stay empty — they
remain unscored, which is an intrinsic limit of URL-based fetching, not
a bug.

Usage:
    uv run python scripts/backfill_description.py            # dry-run preview
    uv run python scripts/backfill_description.py --apply    # actually write
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re

import httpx
from dotenv import load_dotenv

from src.common.logger import get_logger

logger = get_logger("backfill_description")
load_dotenv()

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
APPS_DB_ID = os.environ["NOTION_APPLICATIONS_DB_ID"]
HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def extract_text(prop: dict, kind: str) -> str:
    items = prop.get(kind, [])
    return "".join(it.get("plain_text", "") for it in items)


def extract_url(prop: dict) -> str:
    return prop.get("url") or ""


def extract_company(prop: dict) -> str:
    if rt := prop.get("rich_text"):
        return "".join(it.get("plain_text", "") for it in rt)
    if sel := prop.get("select"):
        return sel.get("name", "")
    if prop.get("relation"):
        return "(relation)"
    return ""


async def db_has_description_column(client: httpx.AsyncClient) -> bool:
    """Pre-flight check, same pattern as scripts/score_jobs.py — checked via
    GET /databases/{id} rather than letting a filtered query fail on a
    nonexistent property."""
    r = await client.get(f"https://api.notion.com/v1/databases/{APPS_DB_ID}")
    r.raise_for_status()
    properties = r.json().get("properties", {})
    if "Description" not in properties:
        logger.warning(
            "notion_column_missing",
            missing="Description",
            detail="add a Description (rich_text) column to Notion first, skipping",
        )
        return False
    return True


async def query_rows_missing_description(client: httpx.AsyncClient) -> list[dict]:
    rows: list[dict] = []
    cursor = None
    body_filter = {
        "and": [
            {"property": "Status", "select": {"equals": "To Apply"}},
            {"property": "Description", "rich_text": {"is_empty": True}},
        ]
    }
    while True:
        body: dict = {"page_size": 100, "filter": body_filter}
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


async def fetch_jd_text(url: str, client: httpx.AsyncClient) -> str:
    try:
        r = await client.get(url, follow_redirects=True, timeout=15.0)
        if r.status_code != 200:
            return ""
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text)
        # Notion's rich_text content limit is 2000, but measured in UTF-16
        # code units, not Python's per-codepoint len() — a handful of wide
        # Unicode chars (smart quotes, em-dashes, etc.) in scraped JD text
        # can push a text[:2000] slice over the real limit (confirmed live:
        # a 2000-char slice was rejected as length 2013). 1900 leaves
        # headroom without meaningfully shrinking the JD context.
        return text[:1900]
    except Exception as e:  # noqa: BLE001
        logger.warning("jd_fetch_failed", url=url[:80], error=str(e))
        return ""


async def write_description(client: httpx.AsyncClient, page_id: str, description: str) -> None:
    r = await client.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        json={
            "properties": {
                "Description": {
                    "rich_text": [{"type": "text", "text": {"content": description}}]
                }
            }
        },
    )
    r.raise_for_status()


async def fetch_one(row: dict, web: httpx.AsyncClient, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        props = row.get("properties", {})
        role = extract_text(props.get("Role", {}), "title")
        company = extract_company(props.get("Company", {}))
        jd_url = extract_url(props.get("JD Link", {}))

        if not jd_url:
            return None

        description = await fetch_jd_text(jd_url, web)
        if not description:
            return None

        return {"id": row["id"], "role": role, "company": company, "description": description}


async def main(apply: bool = False) -> None:
    async with (
        httpx.AsyncClient(headers=HEADERS, timeout=30.0) as notion,
        httpx.AsyncClient(timeout=20.0) as web,
    ):
        if not await db_has_description_column(notion):
            print(
                "\nDescription column is missing. Add a Description "
                "(rich_text) column to Notion first, skipping.\n"
            )
            return

        print("Querying 'To Apply' rows with no Description...")
        rows = await query_rows_missing_description(notion)
        print(f"  Found {len(rows)} rows\n")

        if not rows:
            print("Nothing to backfill.\n")
            return

        print(f"Fetching JD text for {len(rows)} rows (concurrency=5)...")
        sem = asyncio.Semaphore(5)
        results = await asyncio.gather(*[fetch_one(row, web, sem) for row in rows])
        to_write = [r for r in results if r]
        skipped = len(rows) - len(to_write)

        print(
            f"\n  Fetched: {len(to_write)}/{len(rows)} "
            f"({skipped} skipped — bot-protected / dead link / no JD URL)\n"
        )

        if not to_write:
            print("Nothing to write.\n")
            return

        print(f"Sample of {min(20, len(to_write))} rows to backfill:\n")
        for item in to_write[:20]:
            print(f"  [{item['company'][:15]:15s}] {item['role'][:45]:45s} | {len(item['description'])} chars")
        if len(to_write) > 20:
            print(f"  ... and {len(to_write) - 20} more\n")

        if not apply:
            print(
                f"\n[DRY RUN] Re-run with --apply to write Description for "
                f"these {len(to_write)} rows.\n"
            )
            return

        print(f"\nWriting {len(to_write)} descriptions to Notion...")
        written = failed = 0
        for item in to_write:
            try:
                await write_description(notion, item["id"], item["description"])
                written += 1
                await asyncio.sleep(0.35)  # Stay under Notion's ~3 req/sec limit
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "write_failed", page_id=item["id"], role=item["role"], error=str(e)
                )
                failed += 1

        print("\n" + "=" * 72)
        print("  ✓ Backfill complete")
        print("=" * 72)
        print(f"  Total rows scanned:  {len(rows)}")
        print(f"  Fetched:             {len(to_write)}")
        print(f"  Written:             {written}")
        print(f"  Skipped (no JD):     {skipped}")
        if failed:
            print(f"  Failed to write:     {failed}  (see warnings above)")
        print("=" * 72 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill Description for 'To Apply' rows written before that column existed."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write descriptions to Notion. Default is dry-run.",
    )
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
