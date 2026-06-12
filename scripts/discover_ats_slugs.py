"""Discover which ATS a company uses by testing slug variants against
Greenhouse / Lever / Ashby / Workable / Recruitee / Personio.

Workday isn't auto-discoverable (requires tenant + wd_N + site_id) — add those
manually to data/companies/workday.json after finding them via DevTools.

Usage:
    # Edit data/companies_to_check.txt — one company per line
    uv run python scripts/discover_ats_slugs.py
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_FILE = DATA_DIR / "companies_to_check.txt"

ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "workable": "https://apply.workable.com/api/v3/accounts/{slug}/jobs",
    "recruitee": "https://{slug}.recruitee.com/api/offers/",
    "personio": "https://{slug}.jobs.personio.com/xml",
}


def slug_variants(name: str) -> list[str]:
    name = re.sub(r"\b(inc|llc|ltd|co|corp|labs|inc\.|llc\.|co\.|corp\.)\b", "", name, flags=re.I).strip()
    name = name.replace("&", " and ")
    clean = re.sub(r"[^\w\s-]", " ", name).strip()
    clean = re.sub(r"\s+", " ", clean)
    lower = clean.lower()

    variants: list[str] = [
        re.sub(r"[\s_-]+", "", lower),
        re.sub(r"\s+", "-", lower),
        re.sub(r"\s+", "_", lower),
    ]
    no_and = re.sub(r"\band\b", "", lower).strip()
    no_and = re.sub(r"\s+", "", no_and)
    if no_and:
        variants.append(no_and)
    first = lower.split()[0] if lower.split() else ""
    if first:
        variants.append(first)

    seen: set[str] = set()
    return [v for v in variants if v and not (v in seen or seen.add(v))]


async def check_endpoint(client: httpx.AsyncClient, ats: str, slug: str) -> int:
    """Return job count if valid, -1 if not."""
    url = ENDPOINTS[ats].format(slug=slug)
    try:
        params = {"includeCompensation": "true"} if ats == "ashby" else None
        if ats == "workable":
            params = {"limit": 1}
        r = await client.get(url, params=params, timeout=8.0)
        if r.status_code != 200:
            return -1

        if ats == "personio":
            # XML response — just check it parses and has <position> elements
            from xml.etree import ElementTree as ET
            try:
                root = ET.fromstring(r.text)
                return len(root.findall("position"))
            except ET.ParseError:
                return -1

        data = r.json()
        if ats == "greenhouse":
            return len(data.get("jobs", []))
        if ats == "lever":
            return len(data) if isinstance(data, list) else -1
        if ats == "ashby":
            jobs = data.get("jobs", [])
            return len(jobs) if isinstance(jobs, list) and jobs else -1
        if ats == "workable":
            results = data.get("results", [])
            return data.get("total", len(results))
        if ats == "recruitee":
            return len(data.get("offers", []))
    except Exception:
        pass
    return -1


async def discover_one(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, company: str
) -> dict:
    async with sem:
        for slug in slug_variants(company):
            tasks = [check_endpoint(client, ats, slug) for ats in ENDPOINTS]
            counts = await asyncio.gather(*tasks)
            for ats, count in zip(ENDPOINTS.keys(), counts):
                if count >= 0:
                    return {
                        "company": company, "ats": ats, "slug": slug, "jobs": count,
                    }
    return {"company": company, "ats": None, "slug": None, "jobs": 0}


async def main() -> None:
    if not INPUT_FILE.exists():
        INPUT_FILE.write_text(
            "# One company name per line. Lines starting with # are ignored.\n"
            "Hugging Face\n"
            "Weights and Biases\n"
        )
        print(f"Created template at {INPUT_FILE}. Edit and re-run.\n")
        return

    companies = [
        line.strip() for line in INPUT_FILE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not companies:
        print("No companies in companies_to_check.txt\n")
        return

    print(f"Checking {len(companies)} companies across {len(ENDPOINTS)} ATSes...\n")
    sem = asyncio.Semaphore(8)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(
            *[discover_one(client, sem, c) for c in companies]
        )

    found = [r for r in results if r["ats"]]
    missing = [r for r in results if not r["ats"]]

    if found:
        print(f"=== Found ({len(found)}) ===\n")
        print(f"  {'ATS':<11} {'Slug':<28} {'Company':<28} Jobs")
        print(f"  {'-'*11} {'-'*28} {'-'*28} ----")
        for r in sorted(found, key=lambda x: (x["ats"], x["slug"])):
            print(f"  {r['ats']:<11} {r['slug']:<28} {r['company']:<28} {r['jobs']}")

    if missing:
        print(f"\n=== Not found ({len(missing)}) ===\n")
        for r in missing:
            print(f"  {r['company']}")
        print(
            "\n  Likely on Workday, Oracle HCM, or custom portal.\n"
            "  For Workday: find tenant + wd_N + site_id via DevTools,\n"
            "  add to data/companies/workday.json.\n"
        )

    by_ats: dict[str, list[str]] = {}
    for r in found:
        by_ats.setdefault(r["ats"], []).append(r["slug"])

    if by_ats:
        print("\n=== Slug snippets to merge ===")
        for ats, slugs in sorted(by_ats.items()):
            print(f"\n# Add to data/companies/{ats}.json:")
            print(json.dumps(sorted(set(slugs)), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
