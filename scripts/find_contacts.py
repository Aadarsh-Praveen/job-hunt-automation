"""Find contacts at a company via Hunter.io. Writes to Notion Contacts DB.

Usage:
    # Show your remaining Hunter credits (no cost)
    uv run python scripts/find_contacts.py --account

    # Find engineering managers at stripe.com — dry-run by default
    uv run python scripts/find_contacts.py --domain stripe.com --department engineering --limit 10

    # Apply (write to Notion Contacts DB)
    uv run python scripts/find_contacts.py --domain stripe.com --department engineering --apply

    # Specific person lookup
    uv run python scripts/find_contacts.py --domain stripe.com --first Jane --last Smith --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx
from dotenv import load_dotenv

from src.common.logger import get_logger
from src.contacts.hunter import account_info, domain_search, email_finder

logger = get_logger("find_contacts")
load_dotenv()

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
CONTACTS_DB_ID = os.environ["NOTION_CONTACTS_DB_ID"]
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


async def existing_emails_in_notion(client: httpx.AsyncClient) -> set[str]:
    """Dedupe set: every email already in the Contacts DB."""
    emails: set[str] = set()
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
            email = row.get("properties", {}).get("Email", {}).get("email")
            if email:
                emails.add(email.lower())
        if not data.get("has_more"):
            break
        cursor = data["next_cursor"]
    return emails


async def write_contact(
    client: httpx.AsyncClient, contact: dict, domain: str
) -> None:
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


async def main(args) -> None:
    # --account: show credits and exit
    if args.account:
        info = await account_info()
        if not info:
            print("Failed to fetch account info. Check HUNTER_API_KEY in .env.")
            return
        reqs = info.get("requests", {}) or {}
        searches = reqs.get("searches", {})
        verif = reqs.get("verifications", {})
        print(f"\nHunter Account: {info.get('email', 'unknown')}")
        print(f"Plan: {info.get('plan_name', 'Free')}")
        print(f"Searches used:       {searches.get('used', '?')} / {searches.get('available', '?')}")
        print(f"Verifications used:  {verif.get('used', '?')} / {verif.get('available', '?')}\n")
        return

    if not args.domain:
        print("Need --domain (e.g., stripe.com) or --account.\n")
        return

    # --first --last: single-person lookup
    if args.first and args.last:
        result = await email_finder(args.domain, args.first, args.last)
        if not result or not result.get("email"):
            print(f"No email found for {args.first} {args.last} at {args.domain}.\n")
            return
        contacts = [result]
        print(f"Found: {result.get('email')} (score: {result.get('score')})\n")
    else:
        # Bulk domain search
        contacts = await domain_search(
            args.domain,
            limit=args.limit,
            department=args.department,
            seniority=args.seniority,
        )
        if not contacts:
            print(f"No contacts returned for {args.domain}.\n")
            return

    async with httpx.AsyncClient(headers=NOTION_HEADERS, timeout=30.0) as notion:
        existing = await existing_emails_in_notion(notion)
        new_contacts = [
            c for c in contacts
            if c.get("email") and c["email"].lower() not in existing
        ]

        print(f"Found:        {len(contacts)}")
        print(f"New:          {len(new_contacts)}")
        print(f"Cached:       {len(contacts) - len(new_contacts)}\n")

        for c in new_contacts[:25]:
            name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip() or "?"
            pos = c.get("position", "") or ""
            print(f"  {name[:25]:25s} | {pos[:35]:35s} | {c['email']}")
        if len(new_contacts) > 25:
            print(f"  ... and {len(new_contacts) - 25} more")

        if not args.apply:
            print(f"\n[DRY RUN] Re-run with --apply to write {len(new_contacts)} to Notion.\n")
            return

        if not new_contacts:
            print("\nNothing new to write.\n")
            return

        print(f"\nWriting {len(new_contacts)} contacts to Notion...")
        written = 0
        for c in new_contacts:
            try:
                await write_contact(notion, c, args.domain)
                written += 1
                await asyncio.sleep(0.35)
            except Exception as e:
                logger.warning("write_failed", email=c.get("email"), error=str(e))

        print(f"\n✓ Wrote {written} new contacts to Notion.\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--domain", help="Company domain, e.g. stripe.com")
    p.add_argument("--department", help="engineering, executive, finance, hr, marketing, sales")
    p.add_argument("--seniority", help="junior, senior, executive")
    p.add_argument("--limit", type=int, default=10, help="Max contacts (default 10)")
    p.add_argument("--first", help="First name (for specific-person lookup)")
    p.add_argument("--last", help="Last name (for specific-person lookup)")
    p.add_argument("--account", action="store_true", help="Show remaining credits + exit")
    p.add_argument("--apply", action="store_true", help="Write to Notion (default: dry-run)")
    asyncio.run(main(p.parse_args()))
