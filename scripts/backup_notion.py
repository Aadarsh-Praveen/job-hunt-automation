"""Export all Notion databases (Applications, Contacts, Companies) to CSV.

Designed to be run from GitHub Actions weekly. CSVs written to data/backups/{YYYY-MM-DD}/
and uploaded as a workflow artifact (retained 90 days).

Usage:
    uv run python scripts/backup_notion.py
"""

from __future__ import annotations

import asyncio
import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from src.common.logger import get_logger

logger = get_logger("backup_notion")
load_dotenv()

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

DBS = {
    "applications": os.environ.get("NOTION_APPLICATIONS_DB_ID"),
    "contacts": os.environ.get("NOTION_CONTACTS_DB_ID"),
    "companies": os.environ.get("NOTION_COMPANIES_DB_ID"),
}


def extract_value(prop: dict) -> str:
    """Convert a Notion property to a plain string for CSV."""
    t = prop.get("type")
    if t == "title":
        return "".join(it.get("plain_text", "") for it in prop.get("title", []))
    if t == "rich_text":
        return "".join(it.get("plain_text", "") for it in prop.get("rich_text", []))
    if t == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    if t == "multi_select":
        return ", ".join(s.get("name", "") for s in prop.get("multi_select", []))
    if t == "email":
        return prop.get("email") or ""
    if t == "url":
        return prop.get("url") or ""
    if t == "number":
        n = prop.get("number")
        return str(n) if n is not None else ""
    if t == "date":
        d = prop.get("date")
        return d.get("start", "") if d else ""
    if t == "relation":
        return ", ".join(r.get("id", "") for r in prop.get("relation", []))
    if t == "checkbox":
        return "true" if prop.get("checkbox") else "false"
    if t == "phone_number":
        return prop.get("phone_number") or ""
    return ""


async def query_all(client: httpx.AsyncClient, db_id: str) -> list[dict]:
    rows: list[dict] = []
    cursor = None
    while True:
        body: dict[str, Any] = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = await client.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            json=body,
        )
        r.raise_for_status()
        data = r.json()
        rows.extend(data["results"])
        if not data.get("has_more"):
            break
        cursor = data["next_cursor"]
    return rows


async def backup_one(
    client: httpx.AsyncClient, name: str, db_id: str, out_dir: Path
) -> int:
    rows = await query_all(client, db_id)
    if not rows:
        logger.warning("backup_empty", db=name)
        return 0

    # Build CSV from union of property names
    all_props: set[str] = set()
    for row in rows:
        all_props.update(row.get("properties", {}).keys())
    columns = ["page_id", "created_time", "last_edited_time", "archived"] + sorted(all_props)

    out_file = out_dir / f"{name}.csv"
    with out_file.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for row in rows:
            r = {
                "page_id": row.get("id", ""),
                "created_time": row.get("created_time", ""),
                "last_edited_time": row.get("last_edited_time", ""),
                "archived": "true" if row.get("archived") else "false",
            }
            for prop_name, prop_val in row.get("properties", {}).items():
                r[prop_name] = extract_value(prop_val)
            w.writerow(r)

    logger.info("backup_complete", db=name, rows=len(rows), file=str(out_file))
    return len(rows)


async def main() -> None:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = Path(__file__).parent.parent / "data" / "backups" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nBacking up Notion → {out_dir}\n")

    async with httpx.AsyncClient(headers=NOTION_HEADERS, timeout=60.0) as client:
        for name, db_id in DBS.items():
            if not db_id:
                print(f"  ⚠  Skipping {name} — env var not set")
                continue
            count = await backup_one(client, name, db_id, out_dir)
            print(f"  ✓  {name}: {count} rows")

    print(f"\n✓ Backup complete: {out_dir}\n")


if __name__ == "__main__":
    asyncio.run(main())
