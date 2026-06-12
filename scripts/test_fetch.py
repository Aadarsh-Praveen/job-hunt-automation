"""Sanity-check: fetch jobs from Greenhouse, Lever, Ashby + apply role filter.

Usage:
    uv run python scripts/test_fetch.py          # filtered (default)
    uv run python scripts/test_fetch.py --all    # show raw counts too
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from src.common.logger import get_logger
from src.config import load_role_keywords, load_slugs
from src.fetchers.ashby import AshbyFetcher
from src.fetchers.base import JobPosting
from src.fetchers.greenhouse import GreenhouseFetcher
from src.fetchers.lever import LeverFetcher

logger = get_logger("test_fetch")


def matches_role(job: JobPosting, kw: dict) -> bool:
    """Title-based role filter.

    Job passes if:
      - title contains any include_titles OR include_keywords_any term, AND
      - title contains none of the exclude_titles terms.
    """
    title = job.role.lower()

    include_terms = [t.lower() for t in kw.get("include_titles", [])]
    include_terms += [k.lower() for k in kw.get("include_keywords_any", [])]
    if not any(term in title for term in include_terms):
        return False

    exclude_terms = [t.lower() for t in kw.get("exclude_titles", [])]
    if any(term in title for term in exclude_terms):
        return False

    return True


async def main(show_all: bool = False) -> None:
    gh_slugs = load_slugs("greenhouse")
    lv_slugs = load_slugs("lever")
    ab_slugs = load_slugs("ashby")
    role_kw = load_role_keywords()

    logger.info(
        "starting_fetch",
        greenhouse_companies=len(gh_slugs),
        lever_companies=len(lv_slugs),
        ashby_companies=len(ab_slugs),
    )

    gh = GreenhouseFetcher(gh_slugs)
    lv = LeverFetcher(lv_slugs)
    ab = AshbyFetcher(ab_slugs)

    gh_jobs, lv_jobs, ab_jobs = await asyncio.gather(
        gh.fetch_all(), lv.fetch_all(), ab.fetch_all()
    )

    all_jobs = gh_jobs + lv_jobs + ab_jobs
    filtered = [j for j in all_jobs if matches_role(j, role_kw)]

    print("\n" + "=" * 72)
    print(f"  Raw fetch counts:")
    print(f"    Greenhouse: {len(gh_jobs):4d}  ({len(gh_slugs)} companies)")
    print(f"    Lever:      {len(lv_jobs):4d}  ({len(lv_slugs)} companies)")
    print(f"    Ashby:      {len(ab_jobs):4d}  ({len(ab_slugs)} companies)")
    print(f"    Total raw:  {len(all_jobs):4d}")
    print(f"")
    print(f"  After role filter (Data/AI/ML/GenAI):")
    print(f"    Matching:   {len(filtered):4d}  jobs")
    print("=" * 72 + "\n")

    # Top companies by matching jobs
    by_company = Counter(j.company for j in filtered)
    if by_company:
        print("Top companies by matching open roles:\n")
        for company, n in by_company.most_common(15):
            print(f"  {n:3d}  {company}")
        print()

    # By ATS source
    by_source = Counter(j.ats_source for j in filtered)
    print("Matching by ATS source:")
    for source, n in by_source.most_common():
        print(f"  {source:12s}  {n}")
    print()

    # Show 10 sample matched jobs
    print(f"Sample of 10 matched jobs:\n")
    for job in filtered[:10]:
        date_str = job.date_posted.strftime("%Y-%m-%d") if job.date_posted else "n/a"
        print(f"  [{job.ats_source:10s}] {job.role}")
        print(f"     {job.company} · {job.location or 'n/a'} · posted {date_str}")
        print(f"     {job.jd_link}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Show raw counts (default already shows them)")
    args = parser.parse_args()
    asyncio.run(main(show_all=args.all))
