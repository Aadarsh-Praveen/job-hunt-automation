"""Main fetcher: pull jobs across all ATS sources, filter, dedupe, write to Notion.

Pipeline:
    Phase 1 — Fetch from all ATS sources (Greenhouse / Lever / Ashby / Workable /
              Recruitee / Personio / Workday)
    Phase 2 — Role filter
    Phase 3 — Dedupe early (saves LLM credits)
    Phase 4 — LLM resolve empty locations (new jobs only)
    Phase 5 — Location filter
    Phase 6 — Write to Notion
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re

from src.common.llm_location import resolve_many
from src.common.logger import get_logger
from src.config import load_locations, load_role_keywords, load_slugs
from src.fetchers.ashby import AshbyFetcher
from src.fetchers.base import JobPosting
from src.fetchers.greenhouse import GreenhouseFetcher
from src.fetchers.lever import LeverFetcher
from src.fetchers.personio import PersonioFetcher
from src.fetchers.recruitee import RecruiteeFetcher
from src.fetchers.workable import WorkableFetcher
from src.fetchers.workday import WorkdayFetcher
from src.tracker.dedupe import load_existing_hashes
from src.tracker.schemas import ApplicationRow
from src.tracker.writers import write_application

logger = get_logger("fetch_jobs")


def matches_role(job: JobPosting, kw: dict) -> bool:
    title = job.role.lower()
    include = [t.lower() for t in kw.get("include_titles", [])]
    include += [k.lower() for k in kw.get("include_keywords_any", [])]
    if not any(term in title for term in include):
        return False
    exclude = [t.lower() for t in kw.get("exclude_titles", [])]
    if any(term in title for term in exclude):
        return False
    return True


def matches_location(location: str, loc_cfg: dict) -> bool:
    """Four-tier location filter — see locations.json for tier definitions."""
    if not location:
        return True
    loc = location.lower()

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
    return False


def job_to_application_row(job: JobPosting) -> ApplicationRow:
    return ApplicationRow(
        role=job.role,
        company=job.company,
        jd_link=job.jd_link,
        ats_source=job.ats_source,
        date_posted=job.date_posted,
        date_discovered=job.date_discovered,
        location=job.location,
        department=job.department,
        status="To Apply",
        dedupe_hash=job.dedupe_hash,
    )


def _load_safely(ats_name: str) -> list:
    """Load slugs but tolerate missing/empty files."""
    try:
        return load_slugs(ats_name) or []
    except Exception as e:
        logger.warning("slugs_load_failed", ats=ats_name, error=str(e))
        return []


async def main(dry_run: bool = False) -> None:
    role_kw = load_role_keywords()
    loc_cfg = load_locations()

    # ---------- Phase 1: fetch ----------
    fetchers = [
        GreenhouseFetcher(_load_safely("greenhouse")),
        LeverFetcher(_load_safely("lever")),
        AshbyFetcher(_load_safely("ashby")),
        WorkableFetcher(_load_safely("workable")),
        RecruiteeFetcher(_load_safely("recruitee")),
        PersonioFetcher(_load_safely("personio")),
        WorkdayFetcher(_load_safely("workday")),
    ]

    logger.info(
        "fetch_started",
        **{f"{f.ats_name}_slugs": len(f.slugs) for f in fetchers},
    )

    results = await asyncio.gather(*[f.fetch_all() for f in fetchers])
    all_jobs: list[JobPosting] = [j for sub in results for j in sub]

    # ---------- Phase 2: role filter ----------
    role_matching = [j for j in all_jobs if matches_role(j, role_kw)]

    # ---------- Phase 3: dedupe (early, saves LLM credits) ----------
    existing_hashes = await load_existing_hashes()
    new_jobs = [j for j in role_matching if j.dedupe_hash not in existing_hashes]

    logger.info(
        "dedupe_complete",
        already_in_notion=len(existing_hashes),
        new_role_matching=len(new_jobs),
    )

    # ---------- Phase 4: LLM resolve empty locations (skip in dry-run) ----------
    llm_resolved_count = 0
    if not dry_run and os.environ.get("OPENAI_API_KEY"):
        empty_loc = [j for j in new_jobs if not j.location]
        if empty_loc:
            logger.info("llm_resolving_empty_locations", count=len(empty_loc))
            resolved = await resolve_many(
                [(j.dedupe_hash, j.jd_link) for j in empty_loc]
            )
            for j in empty_loc:
                if j.dedupe_hash in resolved:
                    j.location = resolved[j.dedupe_hash]
                    llm_resolved_count += 1

    # ---------- Phase 5: location filter ----------
    matching = [j for j in new_jobs if matches_location(j.location, loc_cfg)]

    if dry_run:
        print(
            f"\n[DRY RUN]"
            f"\n  Raw fetched:           {len(all_jobs)}"
            f"\n  After role filter:     {len(role_matching)}"
            f"\n  New (after dedupe):    {len(new_jobs)}"
            f"\n  After loc filter:      {len(matching)}"
            f"\n  (LLM resolution skipped in dry-run)\n"
        )
        return

    # ---------- Phase 6: write ----------
    if not matching:
        print("\nNo new jobs to write. Notion is up to date.\n")
        return

    written = failed = 0
    for job in matching:
        try:
            await write_application(job_to_application_row(job))
            written += 1
        except Exception as e:
            logger.warning(
                "write_failed", role=job.role, company=job.company, error=str(e),
            )
            failed += 1

    print("\n" + "=" * 72)
    print("  ✓ Fetch + write complete")
    print("=" * 72)
    print(f"  Raw fetched:           {len(all_jobs)}")
    print(f"  After role filter:     {len(role_matching)}")
    print(f"  New (after dedupe):    {len(new_jobs)}")
    print(f"  LLM-resolved empties:  {llm_resolved_count}")
    print(f"  After loc filter:      {len(matching)}")
    print(f"  New written:           {written}")
    if failed:
        print(f"  Failed writes:         {failed}")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
