"""Cleanup: archive existing Notion 'To Apply' rows that an LLM judges should
be excluded (7+ years required, PhD required, senior-level signals in body).

Mirrors scripts/cleanup_locations.py exactly — raw httpx, os.environ,
0.35s sleep between archives.

Usage:
    uv run python scripts/cleanup_with_llm.py            # dry-run preview
    uv run python scripts/cleanup_with_llm.py --apply    # actually archive
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

from src.common.llm_filter import should_exclude
from src.common.logger import get_logger

logger = get_logger("cleanup_with_llm")
load_dotenv()

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
APPS_DB_ID = os.environ["NOTION_APPLICATIONS_DB_ID"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
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


async def query_to_apply_rows(client: httpx.AsyncClient) -> list[dict]:
    rows: list[dict] = []
    cursor = None
    while True:
        body: dict = {
            "page_size": 100,
            "filter": {"property": "Status", "select": {"equals": "To Apply"}},
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


async def archive_page(client: httpx.AsyncClient, page_id: str) -> None:
    r = await client.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        json={"archived": True},
    )
    r.raise_for_status()


async def fetch_jd_text(url: str, client: httpx.AsyncClient) -> str:
    try:
        r = await client.get(url, follow_redirects=True, timeout=15.0)
        if r.status_code != 200:
            return ""
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text)
        return text[:5000]
    except Exception as e:
        logger.warning("fetch_failed", url=url[:80], error=str(e))
        return ""


async def evaluate_row(
    row: dict,
    web: httpx.AsyncClient,
    oai: AsyncOpenAI,
    sem: asyncio.Semaphore,
) -> dict | None:
    async with sem:
        props = row.get("properties", {})
        role = extract_text(props.get("Role", {}), "title")
        company = extract_company(props.get("Company", {}))
        jd_url = extract_url(props.get("JD Link", {}))

        if not jd_url:
            return None

        desc = await fetch_jd_text(jd_url, web)
        if not desc:
            return None

        decision = await should_exclude(role, desc, oai)
        if not decision.exclude:
            return None

        return {
            "id": row["id"],
            "role": role,
            "company": company,
            "reason": decision.reason,
        }


async def main(apply: bool = False) -> None:
    oai = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with (
        httpx.AsyncClient(headers=HEADERS, timeout=30.0) as notion,
        httpx.AsyncClient(timeout=20.0) as web,
    ):
        print("Querying Notion 'To Apply' rows...")
        rows = await query_to_apply_rows(notion)
        print(f"  Fetched {len(rows)} rows\n")

        if not rows:
            print("Nothing to evaluate.\n")
            return

        print(f"Evaluating {len(rows)} rows via LLM (concurrency=5)...")
        sem = asyncio.Semaphore(2)
        results = await asyncio.gather(
            *[evaluate_row(row, web, oai, sem) for row in rows]
        )
        to_archive = [r for r in results if r]

        print()
        if not to_archive:
            print("Nothing to archive. All rows pass the LLM filter.\n")
            return

        print(f"Would archive {len(to_archive)} of {len(rows)} rows. Sample:\n")
        for item in to_archive[:30]:
            print(
                f"  [{item['company'][:15]:15s}] "
                f"{item['role'][:48]:48s} | {item['reason'][:60]}"
            )
        if len(to_archive) > 30:
            print(f"  ... and {len(to_archive) - 30} more\n")

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
                await archive_page(notion, item["id"])
                archived += 1
                await asyncio.sleep(0.35)
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
        description="Archive Notion 'To Apply' rows that an LLM judges should be excluded."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually archive rows. Default is dry-run.",
    )
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
