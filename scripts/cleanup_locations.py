"""Cleanup: archive existing Notion Application rows whose location no longer passes the filter.

Mirrors scripts/fetch_jobs.py::matches_location's four-tier logic exactly.
Ambiguous rows (bare "Remote"/"Anywhere"/empty) get the same LLM arbiter
treatment — JD fetched live via its URL (Notion doesn't store the full
description), NON_US archived, UNCLEAR kept but flagged via the Notes
field so it's visible in the Notion view.

Usage:
    uv run python scripts/cleanup_locations.py            # dry-run, preview only
    uv run python scripts/cleanup_locations.py --apply    # actually archive
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

from src.common.llm_location import HTTP_HEADERS as JD_HTTP_HEADERS
from src.common.llm_location import fetch_jd_text
from src.common.llm_location_arbiter import arbitrate
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

AMBIGUOUS_LOCATIONS = {"remote", "anywhere"}


def location_passes(location: str, loc_cfg: dict) -> bool | None:
    """Mirrors scripts/fetch_jobs.py::matches_location — four-tier priority.

    Returns True (keep), False (archive), or None (ambiguous — caller
    should defer to the LLM arbiter)."""
    loc = (location or "").strip().lower()
    if not loc:
        return None

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
    if loc in AMBIGUOUS_LOCATIONS:
        return None
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


def extract_url(prop: dict) -> str:
    return prop.get("url") or ""


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


async def update_notes(client: httpx.AsyncClient, page_id: str, notes: str) -> None:
    r = await client.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        json={"properties": {"Notes": {"rich_text": [{"type": "text", "text": {"content": notes[:2000]}}]}}},
    )
    r.raise_for_status()


async def main(apply: bool = False) -> None:
    loc_cfg = load_locations()

    async with httpx.AsyncClient(headers=HEADERS, timeout=30.0) as client:
        print("Querying Notion Applications DB...")
        rows = await query_all_applications(client)
        print(f"  Fetched {len(rows)} rows total\n")

        to_archive: list[dict] = []
        ambiguous: list[dict] = []
        for row in rows:
            props = row.get("properties", {})
            location = extract_text(props.get("Location", {}), "rich_text")
            item = {
                "id": row["id"],
                "role": extract_text(props.get("Role", {}), "title"),
                "company": extract_company(props.get("Company", {})),
                "location": location,
                "jd_link": extract_url(props.get("JD Link", {})),
            }
            verdict = location_passes(location, loc_cfg)
            if verdict is False:
                to_archive.append(item)
            elif verdict is None:
                ambiguous.append(item)

        # Ambiguous rows (bare "Remote"/"Anywhere"/empty) — fetch the JD
        # and ask the LLM arbiter. NON_US -> archive. US_ONLY/UNCLEAR ->
        # keep; UNCLEAR also gets a Notes marker so it's visible in the
        # Notion view (same policy as scripts/fetch_jobs.py Phase 5).
        to_flag_unclear: list[dict] = []
        arbiter_kept = arbiter_excluded = 0
        if ambiguous and os.environ.get("OPENAI_API_KEY"):
            oai_client = AsyncOpenAI()
            sem = asyncio.Semaphore(10)

            async with httpx.AsyncClient(headers=JD_HTTP_HEADERS) as jd_client:

                async def _arbitrate_one(item: dict) -> tuple[dict, str]:
                    async with sem:
                        jd_text = await fetch_jd_text(item["jd_link"], jd_client) if item["jd_link"] else ""
                        decision = await arbitrate(item["location"], jd_text, oai_client)
                        return item, decision.decision

                results = await asyncio.gather(*[_arbitrate_one(i) for i in ambiguous])

            for item, decision in results:
                if decision == "NON_US":
                    to_archive.append(item)
                    arbiter_excluded += 1
                else:
                    arbiter_kept += 1
                    if decision == "UNCLEAR":
                        to_flag_unclear.append(item)
        elif ambiguous:
            print(
                f"  {len(ambiguous)} ambiguous rows found but OPENAI_API_KEY "
                f"is not set — skipping arbiter, leaving them as-is.\n"
            )

        print(
            f"Ambiguous rows: {len(ambiguous)}  "
            f"(arbiter kept {arbiter_kept}, of which {len(to_flag_unclear)} UNCLEAR; "
            f"excluded {arbiter_excluded} NON_US)\n"
        )

        if not to_archive and not to_flag_unclear:
            print("Nothing to archive or flag. Notion is clean already.\n")
            return

        if to_archive:
            print(f"Would archive {len(to_archive)} rows. Sample:\n")
            for item in to_archive[:25]:
                print(f"  [{item['company']:15s}] {item['role'][:48]:48s} | {item['location']}")
            if len(to_archive) > 25:
                print(f"  ... and {len(to_archive) - 25} more\n")

        if to_flag_unclear:
            print(f"\nWould flag {len(to_flag_unclear)} UNCLEAR rows (Notes updated, not archived):\n")
            for item in to_flag_unclear[:25]:
                print(f"  [{item['company']:15s}] {item['role'][:48]:48s} | {item['location']}")

        if not apply:
            print(
                f"\n[DRY RUN] Re-run with --apply to archive {len(to_archive)} rows "
                f"and flag {len(to_flag_unclear)} UNCLEAR rows.\n"
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

        print(f"Flagging {len(to_flag_unclear)} UNCLEAR rows...")
        flagged = flag_failed = 0
        for item in to_flag_unclear:
            try:
                await update_notes(client, item["id"], "location_verified: UNCLEAR")
                flagged += 1
                await asyncio.sleep(0.35)
            except Exception as e:
                logger.warning(
                    "flag_failed", page_id=item["id"], role=item["role"], error=str(e),
                )
                flag_failed += 1

        print("\n" + "=" * 72)
        print("  ✓ Cleanup complete")
        print("=" * 72)
        print(f"  Total rows scanned:  {len(rows)}")
        print(f"  Rows archived:       {archived}")
        print(f"  Rows flagged UNCLEAR:{flagged:>4}")
        if failed:
            print(f"  Archive failures:    {failed}  (see warnings above)")
        if flag_failed:
            print(f"  Flag failures:       {flag_failed}  (see warnings above)")
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
