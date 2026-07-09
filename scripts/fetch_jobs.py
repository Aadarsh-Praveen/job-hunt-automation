"""Main fetcher: pull jobs across all ATS sources, filter, dedupe, write to Notion.

Pipeline:
    Phase 1 — Fetch from all ATS sources (Greenhouse / Lever / Ashby / Workable /
              Recruitee / Personio / Workday)
    Phase 2 — Role filter (title + description keyword excludes)
    Phase 3 — Dedupe early (saves LLM credits)
    Phase 4 — LLM resolve empty locations (new jobs only)
    Phase 5 — Location filter
    Phase 6 — LLM exclusion filter (years/credentials/senior-level signals)
    Phase 7 — Write to Notion
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re

from dotenv import load_dotenv
from openai import AsyncOpenAI

from src.common.llm_filter import batch_filter as llm_exclude_filter
from src.common.llm_location import resolve_many
from src.common.logger import get_logger
from src.config import (
    get_settings,
    load_exclude_keywords,
    load_locations,
    load_role_keywords,
    load_slugs,
)
from src.fetchers.apify_universal import ApifyUniversalFetcher
from src.fetchers.ashby import AshbyFetcher
from src.fetchers.base import JobPosting
from src.fetchers.greenhouse import GreenhouseFetcher
from src.fetchers.jobspy_google import JobSpyGoogleFetcher
from src.fetchers.lever import LeverFetcher
from src.fetchers.personio import PersonioFetcher
from src.fetchers.recruitee import RecruiteeFetcher
from src.fetchers.workable import WorkableFetcher
from src.fetchers.workday import WorkdayFetcher
from src.tracker.dedupe import load_existing_hashes
from src.tracker.schemas import ApplicationRow
from src.tracker.writers import write_application

load_dotenv()
logger = get_logger("fetch_jobs")


def matches_role(
    job: JobPosting,
    kw: dict,
    exclude_kw: dict | None = None,
) -> bool:
    """Title-only pre-filter. Description-based excludes (years of experience,
    PhD requirements, senior-level signals) are handled by the LLM filter in
    Phase 6 — more reliable than substring matching."""
    title = job.role.lower()

    include = [t.lower() for t in kw.get("include_titles", [])]
    include += [k.lower() for k in kw.get("include_keywords_any", [])]
    if not any(term in title for term in include):
        return False

    exclude = [t.lower() for t in kw.get("exclude_titles", [])]
    if any(term in title for term in exclude):
        return False

    if exclude_kw:
        seniority = [t.lower() for t in exclude_kw.get("seniority_excludes", [])]
        if any(term in title for term in seniority):
            return False

    return True


AMBIGUOUS_LOCATIONS = {"remote", "anywhere"}


def matches_location(location: str, loc_cfg: dict) -> bool | None:
    """Four-tier location filter — see locations.json for tier definitions.

    Returns True (include), False (exclude), or None (ambiguous — caller
    should defer to the LLM arbiter). Ambiguous: bare "Remote"/"Anywhere"
    or an empty location string — not enough signal to decide from the
    string alone, but not automatically excludable either.
    """
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


def job_to_application_row(job: JobPosting, notes: str = "") -> ApplicationRow:
    return ApplicationRow(
        role=job.role,
        company=job.company,
        jd_link=job.jd_link,
        ats_source=job.ats_source,
        date_posted=job.date_posted,
        date_discovered=job.date_discovered,
        location=job.location,
        department=job.department,
        # 1900 not 2000 — Notion's rich_text limit is measured in UTF-16
        # code units, not Python's len(); a small margin avoids 400s from
        # wide Unicode chars in scraped JD text (confirmed live in the
        # backfill script, see scripts/backfill_description.py).
        description=(job.description or "")[:1900],
        status="To Apply",
        dedupe_hash=job.dedupe_hash,
        notes=notes,
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
    exclude_kw = load_exclude_keywords()
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

    # Apify universal — separate branch, doesn't fit BaseFetcher's
    # per-slug fetch_one shape (one bulk call across curated presets).
    settings = get_settings()
    if os.getenv("SKIP_APIFY") != "1" and settings.apify_api_key and settings.apify_universal_actor_id:
        apify_jobs = await ApifyUniversalFetcher().fetch_all()
        all_jobs.extend(apify_jobs)
        logger.info("apify_added", count=len(apify_jobs))
    else:
        logger.info("apify_skipped", reason="disabled_or_missing_config")

    # JobSpy Google Jobs — best-effort, separate branch (bulk call per
    # keyword, doesn't fit BaseFetcher). Google changes its job-search
    # layout periodically and breaks the underlying scraper; this must
    # never take the pipeline down with it.
    if os.getenv("SKIP_JOBSPY") != "1":
        try:
            jobspy_jobs = await JobSpyGoogleFetcher().fetch_all()
            all_jobs.extend(jobspy_jobs)
            logger.info("jobspy_added", count=len(jobspy_jobs))
        except Exception as e:  # noqa: BLE001
            logger.warning("jobspy_failed", error=str(e))
    else:
        logger.info("jobspy_skipped")

    # ---------- Phase 2: role filter ----------
    role_matching = [j for j in all_jobs if matches_role(j, role_kw, exclude_kw)]

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
    loc_verdicts = [(j, matches_location(j.location, loc_cfg)) for j in new_jobs]
    matching = [j for j, v in loc_verdicts if v is True]
    ambiguous_jobs = [j for j, v in loc_verdicts if v is None]

    # Ambiguous locations (bare "Remote"/"Anywhere"/empty) can't be
    # resolved from the string alone — defer to an LLM reading the JD
    # body. UNCLEAR is treated as "include but flag", not "exclude" —
    # the user reviews the Notion queue manually, so a real US-only job
    # that just says "Remote" shouldn't get silently dropped.
    arbiter_unclear_hashes: set[str] = set()
    arbiter_excluded_count = 0
    if ambiguous_jobs and os.environ.get("OPENAI_API_KEY"):
        from src.common.llm_location_arbiter import arbitrate

        oai_client = AsyncOpenAI()
        sem = asyncio.Semaphore(10)

        async def _arbitrate_one(job: JobPosting):
            async with sem:
                decision = await arbitrate(job.location, job.description, oai_client)
                return job, decision

        arbiter_results = await asyncio.gather(
            *[_arbitrate_one(j) for j in ambiguous_jobs]
        )
        for job, decision in arbiter_results:
            if decision.decision == "NON_US":
                arbiter_excluded_count += 1
                continue
            matching.append(job)
            if decision.decision == "UNCLEAR":
                arbiter_unclear_hashes.add(job.dedupe_hash)
        logger.info(
            "phase_5_arbiter_complete",
            ambiguous=len(ambiguous_jobs),
            kept=len(ambiguous_jobs) - arbiter_excluded_count,
            unclear_flagged=len(arbiter_unclear_hashes),
            excluded_non_us=arbiter_excluded_count,
        )
    elif ambiguous_jobs:
        logger.info(
            "phase_5_arbiter_skipped",
            reason="no_openai_key",
            ambiguous=len(ambiguous_jobs),
        )

    # ---------- Phase 6: LLM exclusion filter ----------
    llm_excluded_count = 0
    if matching and os.environ.get("OPENAI_API_KEY"):
        oai_client = AsyncOpenAI()
        before = len(matching)
        matching = await llm_exclude_filter(matching, oai_client)
        llm_excluded_count = before - len(matching)
        logger.info(
            "phase_6_llm_complete",
            kept=len(matching),
            excluded=llm_excluded_count,
        )

    if dry_run:
        print(
            f"\n[DRY RUN]"
            f"\n  Raw fetched:           {len(all_jobs)}"
            f"\n  After role filter:     {len(role_matching)}"
            f"\n  New (after dedupe):    {len(new_jobs)}"
            f"\n  Ambiguous locations:   {len(ambiguous_jobs)}"
            f"  (kept {len(ambiguous_jobs) - arbiter_excluded_count}, "
            f"of which {len(arbiter_unclear_hashes)} UNCLEAR; "
            f"excluded {arbiter_excluded_count} NON_US)"
            f"\n  After loc filter:      {len(matching) + llm_excluded_count}"
            f"\n  After LLM filter:      {len(matching)}"
            f"\n  (LLM location resolution skipped in dry-run)\n"
        )
        return

    # ---------- Phase 7: write ----------
    if not matching:
        print("\nNo new jobs to write. Notion is up to date.\n")
        return

    written = failed = 0
    for job in matching:
        notes = "location_verified: UNCLEAR" if job.dedupe_hash in arbiter_unclear_hashes else ""
        try:
            await write_application(job_to_application_row(job, notes=notes))
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
    print(f"  Ambiguous locations:   {len(ambiguous_jobs)} "
          f"(kept {len(ambiguous_jobs) - arbiter_excluded_count}, "
          f"{len(arbiter_unclear_hashes)} UNCLEAR, "
          f"excluded {arbiter_excluded_count} NON_US)")
    print(f"  After loc filter:      {len(matching) + llm_excluded_count}")
    print(f"  LLM-excluded:          {llm_excluded_count}")
    print(f"  New written:           {written}")
    if failed:
        print(f"  Failed writes:         {failed}")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
