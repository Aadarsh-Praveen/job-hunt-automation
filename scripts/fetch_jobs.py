"""Main fetcher: pull jobs, filter by role, dedupe against Notion, write new ones.

Usage:
    uv run python scripts/fetch_jobs.py             # full run, writes to Notion
    uv run python scripts/fetch_jobs.py --dry-run   # show counts, no writes
"""

from __future__ import annotations

import argparse
import asyncio

from src.common.logger import get_logger
from src.config import load_role_keywords, load_slugs
from src.fetchers.ashby import AshbyFetcher
from src.fetchers.base import JobPosting
from src.fetchers.greenhouse import GreenhouseFetcher
from src.fetchers.lever import LeverFetcher
from src.tracker.dedupe import load_existing_hashes
from src.tracker.schemas import ApplicationRow
from src.tracker.writers import write_application

logger = get_logger("fetch_jobs")


def matches_role(job: JobPosting, kw: dict) -> bool:
    """Title-based role filter — same logic as test_fetch.py."""
    title = job.role.lower()

    include_terms = [t.lower() for t in kw.get("include_titles", [])]
    include_terms += [k.lower() for k in kw.get("include_keywords_any", [])]
    if not any(term in title for term in include_terms):
        return False

    exclude_terms = [t.lower() for t in kw.get("exclude_titles", [])]
    if any(term in title for term in exclude_terms):
        return False

    return True


def job_to_application_row(job: JobPosting) -> ApplicationRow:
    """Convert a fetched JobPosting into an ApplicationRow ready for Notion."""
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


async def main(dry_run: bool = False) -> None:
    role_kw = load_role_keywords()

    # ---------- Phase 1: fetch all jobs ----------
    gh = GreenhouseFetcher(load_slugs("greenhouse"))
    lv = LeverFetcher(load_slugs("lever"))
    ab = AshbyFetcher(load_slugs("ashby"))

    logger.info(
        "fetch_started",
        greenhouse_slugs=len(gh.slugs),
        lever_slugs=len(lv.slugs),
        ashby_slugs=len(ab.slugs),
    )

    gh_jobs, lv_jobs, ab_jobs = await asyncio.gather(
        gh.fetch_all(), lv.fetch_all(), ab.fetch_all()
    )
    all_jobs = gh_jobs + lv_jobs + ab_jobs

    # ---------- Phase 2: role filter ----------
    matching = [j for j in all_jobs if matches_role(j, role_kw)]
    logger.info(
        "filter_complete",
        raw=len(all_jobs),
        matching=len(matching),
    )

    if dry_run:
        print(
            f"\n[DRY RUN] Raw: {len(all_jobs)} | Matching: {len(matching)} "
            f"| Would dedupe + write to Notion.\n"
        )
        return

    # ---------- Phase 3: dedupe against Notion ----------
    existing_hashes = await load_existing_hashes()
    new_jobs = [j for j in matching if j.dedupe_hash not in existing_hashes]
    logger.info(
        "dedupe_complete",
        already_in_notion=len(existing_hashes),
        new_to_write=len(new_jobs),
    )

    # ---------- Phase 4: write to Notion ----------
    if not new_jobs:
        print("\nNo new jobs to write. Notion is up to date.\n")
        return

    written = 0
    failed = 0
    for job in new_jobs:
        try:
            row = job_to_application_row(job)
            await write_application(row)
            written += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "write_failed",
                role=job.role,
                company=job.company,
                error=str(e),
                error_type=type(e).__name__,
            )
            failed += 1

    # ---------- Summary ----------
    print("\n" + "=" * 72)
    print("  ✓ Fetch + write complete")
    print("=" * 72)
    print(f"  Raw fetched:        {len(all_jobs)}")
    print(f"  Role-matching:      {len(matching)}")
    print(f"  Already in Notion:  {len(matching) - len(new_jobs)}")
    print(f"  New written:        {written}")
    if failed:
        print(f"  Failed writes:      {failed}  (see warnings above)")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show counts without writing to Notion",
    )
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))