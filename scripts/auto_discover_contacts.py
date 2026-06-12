"""Daily auto-contact discovery — picks one company with applications but no
contacts yet, runs Hunter domain_search, writes contacts to Notion.

Designed for GitHub Actions daily cron. 1 Hunter credit per run = 25/month max.

Usage:
    uv run python scripts/auto_discover_contacts.py            # dry-run
    uv run python scripts/auto_discover_contacts.py --apply    # write to Notion
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from src.common.logger import get_logger
from src.contacts.hunter import account_info, domain_search

logger = get_logger("auto_discover_contacts")
load_dotenv()

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
APPS_DB_ID = os.environ["NOTION_APPLICATIONS_DB_ID"]
CONTACTS_DB_ID = os.environ["NOTION_CONTACTS_DB_ID"]
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# Optional: override company → domain when the default {slug}.com doesn't work
DOMAIN_OVERRIDES_FILE = Path(__file__).parent.parent / "data" / "company_domains.json"


def load_domain_overrides() -> dict[str, str]:
    if DOMAIN_OVERRIDES_FILE.exists():
        return json.loads(DOMAIN_OVERRIDES_FILE.read_text())
    return {}


def guess_domain(company: str, overrides: dict[str, str]) -> str:
    """Best-effort: overrides first, then {company}.com, else {company}.ai."""
    company_lower = company.lower().strip()
    if company_lower in overrides:
        return overrides[company_lower]
    # Strip common suffixes
    clean = company_lower.replace(" ", "").replace("-", "").replace("_", "")
    return f"{clean}.com"


async def get_applied_companies(client: httpx.AsyncClient) -> list[str]:
    """List companies that have at least one row in Applications, ordered by date_discovered desc."""
    rows: list[dict] = []
    cursor = None
    while True:
        body = {
            "page_size": 100,
            "sorts": [{"property": "Date Discovered", "direction": "descending"}],
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

    companies: list[str] = []
    seen: set[str] = set()
    for row in rows:
        props = row.get("properties", {})
        co_prop = props.get("Company", {})
        text = "".join(it.get("plain_text", "") for it in co_prop.get("rich_text", []))
        if not text:
            sel = co_prop.get("select")
            text = sel.get("name", "") if sel else ""
        text = text.strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            companies.append(text)
    return companies


async def get_companies_with_contacts(client: httpx.AsyncClient) -> set[str]:
    """Set of company names (lowercased) that already have at least one contact."""
    seen: set[str] = set()
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = await client.post(
            f"https://api.notion.com/v1/databases/{CONTACTS_DB_ID}/query",
            json=body,
        )
        r.raise_for_status()
        data = r.json()
        for row in data["results"]:
            notes = row.get("properties", {}).get("Notes", {}).get("rich_text", [])
            notes_text = "".join(n.get("plain_text", "") for n in notes).lower()
            # Notes contains "Domain: stripe.com" from find_contacts writer
            if "domain:" in notes_text:
                domain = notes_text.split("domain:")[1].strip().split()[0]
                # Extract the company part (before .com/.ai/etc)
                co = domain.split(".")[0]
                seen.add(co)
        if not data.get("has_more"):
            break
        cursor = data["next_cursor"]
    return seen


async def write_contact(client: httpx.AsyncClient, contact: dict, domain: str) -> None:
    first = contact.get("first_name") or ""
    last = contact.get("last_name") or ""
    name = f"{first} {last}".strip() or contact.get("value", "Unknown")
    position = contact.get("position") or ""

    properties = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Email": {"email": contact["email"]},
        "Title": {"rich_text": [{"text": {"content": position}}]},
        "Source": {"select": {"name": "Hunter"}},
        "Status": {"select": {"name": "Queued"}},
        "Sequence Step": {"number": 0},
        "Notes": {"rich_text": [{"text": {"content": f"Domain: {domain}"}}]},
    }
    if contact.get("linkedin"):
        properties["LinkedIn URL"] = {"url": contact["linkedin"]}

    r = await client.post(
        "https://api.notion.com/v1/pages",
        json={"parent": {"database_id": CONTACTS_DB_ID}, "properties": properties},
    )
    r.raise_for_status()


async def main(apply: bool = False) -> None:
    # Check credits first — bail if we're tapped out
    info = await account_info()
    searches = info.get("requests", {}).get("searches", {})
    used = searches.get("used", 0)
    available = searches.get("available", 25)
    remaining = available - used
    print(f"Hunter credits remaining: {remaining}/{available}\n")

    if remaining < 1:
        print("⚠  Out of Hunter credits this month. Skipping.\n")
        return

    overrides = load_domain_overrides()

    async with httpx.AsyncClient(headers=NOTION_HEADERS, timeout=30.0) as notion:
        applied_companies = await get_applied_companies(notion)
        already_covered = await get_companies_with_contacts(notion)

        # Pick the most recent company that hasn't been searched yet
        target = None
        for co in applied_companies:
            slug = co.lower().replace(" ", "").replace("-", "").replace("_", "")
            if slug not in already_covered:
                target = co
                break

        if not target:
            print("✓ All companies in Applications DB already have contacts. Nothing to do.\n")
            return

        domain = guess_domain(target, overrides)
        print(f"Target: {target}  →  domain: {domain}")

        contacts = await domain_search(
            domain,
            limit=5,
            seniority="senior",  # bias toward managers / hiring managers
        )

        if not contacts:
            print(f"  No contacts found at {domain}.")
            print(f"  Add an override to data/company_domains.json if domain is wrong:")
            print(f'  {{"{target.lower()}": "actual-domain.com"}}\n')
            return

        print(f"  Found {len(contacts)} contacts:\n")
        for c in contacts:
            name = f"{c.get('first_name','')} {c.get('last_name','')}".strip()
            print(f"    {name:25s} | {c.get('position','')[:35]:35s} | {c['email']}")

        if not apply:
            print(f"\n[DRY RUN] Re-run with --apply to write to Notion.\n")
            return

        written = 0
        for c in contacts:
            if not c.get("email"):
                continue
            try:
                await write_contact(notion, c, domain)
                written += 1
                await asyncio.sleep(0.35)
            except Exception as e:
                logger.warning("write_failed", email=c.get("email"), error=str(e))

        print(f"\n✓ Wrote {written} contacts for {target} to Notion.\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    asyncio.run(main(apply=p.parse_args().apply))
