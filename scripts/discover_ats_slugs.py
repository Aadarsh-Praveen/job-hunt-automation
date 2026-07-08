"""Discover which ATS a company uses by testing slug variants against
Greenhouse / Lever / Ashby / Workable / Recruitee / Personio.

Workday isn't auto-discoverable (requires tenant + wd_N + site_id) — add those
manually to data/companies/workday.json after finding them via DevTools.

Usage (original probe mode — unchanged, still the default with no flags):
    # Edit data/companies_to_check.txt — one company per line
    uv run python scripts/discover_ats_slugs.py

Usage (new — Google-search discovery via SerpAPI, only runs when --dry-run
or --apply is passed; requires SERPAPI_API_KEY):
    uv run python scripts/discover_ats_slugs.py --dry-run
    uv run python scripts/discover_ats_slugs.py --apply
    uv run python scripts/discover_ats_slugs.py --apply --role-keywords "AI Engineer" "Data Scientist"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

import httpx

from src.common.logger import get_logger
from src.config import get_settings

logger = get_logger("discover_ats_slugs")

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_FILE = DATA_DIR / "companies_to_check.txt"
COMPANIES_DIR = DATA_DIR / "companies"

ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "workable": "https://apply.workable.com/api/v3/accounts/{slug}/jobs",
    "recruitee": "https://{slug}.recruitee.com/api/offers/",
    "personio": "https://{slug}.jobs.personio.com/xml",
}

# ---------------------------------------------------------------------------
# New: Google-search-based discovery via SerpAPI (--dry-run / --apply only)
# ---------------------------------------------------------------------------

DEFAULT_ROLE_KEYWORDS = [
    "AI Engineer",
    "ML Engineer",
    "Data Scientist",
    "Data Analyst",
    "Gen AI Engineer",
    "AI Developer",
]

SEARCH_DOMAINS = {
    "greenhouse": "boards.greenhouse.io",
    "lever": "jobs.lever.co",
    "ashby": "jobs.ashbyhq.com",
    "workable": "apply.workable.com",
    "recruitee": "jobs.recruitee.com",
    "personio": "jobs.personio.com",
}

# Permissive on purpose — Google sometimes shows a canonicalized URL (e.g.
# job-boards.greenhouse.io) even when the site: query targeted the older
# domain, so the greenhouse pattern accepts both known forms.
#
# recruitee/personio: subdomain-form ONLY ({company}.recruitee.com /
# {company}.jobs.personio.com). Their generic jobs.{X}.com/{path} domain
# does NOT carry company identity in practice — live queries showed
# jobs.recruitee.com/o/{job-title} ("o" = literal "offer", not a slug) and
# jobs.recruitee.com/open-positions (a generic listing page). Confirmed via
# real SerpAPI results before shipping this, not assumed from the docs.
SLUG_PATTERNS = {
    "greenhouse": re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)"),
    "lever": re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)"),
    "workable": re.compile(r"apply\.workable\.com/([a-zA-Z0-9_-]+)"),
    "recruitee": re.compile(r"([a-zA-Z0-9_-]+)\.recruitee\.com"),
    "personio": re.compile(r"([a-zA-Z0-9_-]+)\.jobs\.personio\.com"),
}

# Path segments that match the regexes above but aren't real company slugs.
SLUG_DENYLIST = {"embed", "api", "jobs", "job-boards", "boards", "www", "o", "open-positions"}


def extract_slug(ats: str, url: str) -> str | None:
    """Return the slug in its original casing — some ATS APIs (confirmed:
    Lever) are case-sensitive, so normalizing case here would silently
    produce a slug that 404s forever."""
    match = SLUG_PATTERNS[ats].search(url)
    if not match:
        return None
    slug = next((g for g in match.groups() if g), None)
    if not slug or slug.lower() in SLUG_DENYLIST:
        return None
    return slug


def build_queries(ats: str, role_keywords: list[str]) -> list[str]:
    domain = SEARCH_DOMAINS[ats]
    return [f'site:{domain} "{kw}"' for kw in role_keywords]


def run_serpapi_search(client, query: str) -> list[str]:
    """Return organic-result links for one query. Empty list on any API error."""
    try:
        results = client.search(params={"engine": "google", "q": query, "num": 20})
    except Exception as e:  # noqa: BLE001
        logger.warning("serpapi_query_failed", query=query, error=str(e))
        return []

    if results.get("error"):
        logger.warning("serpapi_query_error", query=query, error=results["error"])
        return []

    return [
        r["link"] for r in results.get("organic_results", []) if r.get("link")
    ]


def discover_slugs(role_keywords: list[str]) -> dict[str, set[str]]:
    """Run all ATS x keyword queries and return newly-seen slugs per ATS.

    Returns {} if SERPAPI_API_KEY isn't configured — callers should treat
    that as "skip silently", not an error.
    """
    settings = get_settings()
    if not settings.serpapi_api_key:
        logger.info(
            "serpapi_not_configured",
            detail="SERPAPI_API_KEY not set, skipping discovery",
        )
        return {}

    import serpapi

    client = serpapi.Client(api_key=settings.serpapi_api_key)

    # Keyed by lowercase for case-insensitive dedup within a run, valued
    # with the first-seen original casing (the casing that actually
    # resolves against the ATS API).
    found: dict[str, dict[str, str]] = {ats: {} for ats in ENDPOINTS}
    query_count = 0
    for ats in ENDPOINTS:
        for query in build_queries(ats, role_keywords):
            query_count += 1
            for link in run_serpapi_search(client, query):
                slug = extract_slug(ats, link)
                if slug and slug.lower() not in found[ats]:
                    found[ats][slug.lower()] = slug
        logger.info("discovery_ats_complete", ats=ats, found=len(found[ats]))

    logger.info("discovery_complete", queries=query_count)
    return {ats: set(slugs.values()) for ats, slugs in found.items()}


def load_existing_slugs_raw(ats: str) -> list[str]:
    """Original casing, exactly as stored in the file."""
    path = COMPANIES_DIR / f"{ats}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [s for s in data if isinstance(s, str)]


def load_existing_slugs(ats: str) -> set[str]:
    """Lowercased — for case-insensitive "is this new" comparisons only."""
    return {s.lower() for s in load_existing_slugs_raw(ats)}


def write_slugs(ats: str, new_slugs: set[str]) -> None:
    """Append newly discovered slugs, preserving each slug's own casing
    (some ATS APIs are case-sensitive) and each existing entry's casing."""
    existing_raw = load_existing_slugs_raw(ats)
    existing_lower = {s.lower() for s in existing_raw}
    to_add = [s for s in new_slugs if s.lower() not in existing_lower]
    merged = sorted(existing_raw + to_add, key=str.lower)
    path = COMPANIES_DIR / f"{ats}.json"
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")


async def run_discovery_mode(role_keywords: list[str], apply: bool) -> None:
    discovered = await asyncio.to_thread(discover_slugs, role_keywords)
    if not discovered:
        return

    mode_label = "APPLY" if apply else "DRY RUN"
    print(f"\n=== Slug discovery ({mode_label}) ===\n")

    total_new = 0
    for ats in ENDPOINTS:
        existing = load_existing_slugs(ats)
        new_slugs = sorted(
            (s for s in discovered.get(ats, set()) if s.lower() not in existing),
            key=str.lower,
        )
        if not new_slugs:
            continue
        total_new += len(new_slugs)
        print(f"  {ats:<11} {len(new_slugs)} new: {', '.join(new_slugs)}")
        if apply:
            write_slugs(ats, discovered[ats])

    logger.info("discovery_summary", total_new=total_new, applied=apply)

    if total_new == 0:
        print("  No new slugs found.\n")
    elif apply:
        print(f"\n  Wrote {total_new} new slug(s) to data/companies/*.json\n")
    else:
        print(f"\n  {total_new} new slug(s) found. Re-run with --apply to write them.\n")


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
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role-keywords",
        nargs="+",
        default=DEFAULT_ROLE_KEYWORDS,
        help="Role keywords to search for (Google-search discovery mode only).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write newly discovered slugs to data/companies/*.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run Google-search discovery and print results without writing files.",
    )
    args = parser.parse_args()

    if args.apply or args.dry_run:
        # New Google-search discovery mode.
        asyncio.run(run_discovery_mode(args.role_keywords, apply=args.apply))
    else:
        # Original companies_to_check.txt probe mode — unchanged.
        asyncio.run(main())
