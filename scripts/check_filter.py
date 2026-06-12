"""Diagnostic: show which jobs the location filter cuts vs keeps."""
import asyncio
from src.config import load_locations, load_role_keywords, load_slugs
from src.fetchers.ashby import AshbyFetcher
from src.fetchers.greenhouse import GreenhouseFetcher
from src.fetchers.lever import LeverFetcher
from scripts.fetch_jobs import matches_role, matches_location


async def main():
    role_kw = load_role_keywords()
    loc_cfg = load_locations()

    gh = GreenhouseFetcher(load_slugs("greenhouse"))
    lv = LeverFetcher(load_slugs("lever"))
    ab = AshbyFetcher(load_slugs("ashby"))
    gh_jobs, lv_jobs, ab_jobs = await asyncio.gather(
        gh.fetch_all(), lv.fetch_all(), ab.fetch_all()
    )
    all_jobs = gh_jobs + lv_jobs + ab_jobs
    role_match = [j for j in all_jobs if matches_role(j, role_kw)]
    cut = [j for j in role_match if not matches_location(j.location, loc_cfg)]
    kept = [j for j in role_match if matches_location(j.location, loc_cfg)]

    print(f"\n=== CUT ({len(cut)} jobs) — sample 15 ===")
    for j in cut[:15]:
        print(f"  [{j.company:15s}] {j.role[:50]:50s} | {j.location}")

    print(f"\n=== KEPT ({len(kept)} jobs) — sample 10 ===")
    for j in kept[:10]:
        print(f"  [{j.company:15s}] {j.role[:50]:50s} | {j.location}")


asyncio.run(main())
