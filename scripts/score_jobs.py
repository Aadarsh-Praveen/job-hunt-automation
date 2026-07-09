"""Score Notion 'To Apply' rows against the base resume via LLM.

Mirrors scripts/cleanup_locations.py / scripts/cleanup_with_llm.py — raw
httpx, os.environ, 0.35s sleep between writes.

Requires six columns on the Applications DB (added manually in Notion —
see project instructions): Fit Score, Skill Match, Years Fit, Domain
Match (all Number), Fit Reasoning (Rich text), Scored At (Date). If
they're missing, this logs a warning and exits 0 rather than crashing
the cron.

Usage:
    uv run python scripts/score_jobs.py            # dry-run preview
    uv run python scripts/score_jobs.py --apply    # actually write scores
    uv run python scripts/score_jobs.py --apply --limit 10   # small test batch
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import time
from datetime import UTC, datetime

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

from src.common.jd_fit_scorer import FitScore, score_job
from src.common.logger import get_logger
from src.common.resume_loader import load_resume_plaintext

logger = get_logger("score_jobs")
load_dotenv()

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
APPS_DB_ID = os.environ["NOTION_APPLICATIONS_DB_ID"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

REQUIRED_COLUMNS = {
    "Fit Score": "number",
    "Skill Match": "number",
    "Years Fit": "number",
    "Domain Match": "number",
    "Fit Reasoning": "rich_text",
    "Scored At": "date",
}


def extract_text(prop: dict, kind: str) -> str:
    items = prop.get(kind, [])
    return "".join(it.get("plain_text", "") for it in items)


def extract_url(prop: dict) -> str:
    return prop.get("url") or ""


def extract_company(prop: dict) -> str:
    if rt := prop.get("rich_text"):
        return "".join(it.get("plain_text", "") for it in rt)
    if sel := prop.get("select"):
        return sel.get("name", "")
    if prop.get("relation"):
        return "(relation)"
    return ""


async def db_has_required_columns(client: httpx.AsyncClient) -> bool:
    """Pre-flight check: does the DB schema have all six scoring columns?

    Checked via GET /databases/{id} rather than letting a filtered query
    fail on a nonexistent property — gives a clear, specific message
    about exactly which columns are missing.
    """
    r = await client.get(f"https://api.notion.com/v1/databases/{APPS_DB_ID}")
    r.raise_for_status()
    properties = r.json().get("properties", {})

    missing = [name for name in REQUIRED_COLUMNS if name not in properties]
    if missing:
        logger.warning(
            "notion_columns_missing",
            missing=missing,
            detail="add these columns to Notion first, skipping",
        )
        return False
    return True


async def query_unscored_rows(client: httpx.AsyncClient) -> list[dict]:
    rows: list[dict] = []
    cursor = None
    body_filter = {
        "and": [
            {"property": "Status", "select": {"equals": "To Apply"}},
            {"property": "Fit Score", "number": {"is_empty": True}},
        ]
    }
    while True:
        body: dict = {"page_size": 100, "filter": body_filter}
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
    return rows


async def fetch_jd_text(url: str, client: httpx.AsyncClient) -> str:
    try:
        r = await client.get(url, follow_redirects=True, timeout=15.0)
        if r.status_code != 200:
            return ""
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text)
        return text[:5000]
    except Exception as e:  # noqa: BLE001
        logger.warning("jd_fetch_failed", url=url[:80], error=str(e))
        return ""


async def score_row(
    row: dict,
    web: httpx.AsyncClient,
    oai: AsyncOpenAI,
    resume_snippet: str,
    sem: asyncio.Semaphore,
) -> dict | None:
    async with sem:
        props = row.get("properties", {})
        role = extract_text(props.get("Role", {}), "title")
        company = extract_company(props.get("Company", {}))

        # Notion-stored description first (instant, no HTTP) — only rows
        # written before the Description column existed fall through to
        # a live URL fetch, which fails for bot-protected sites/SPAs.
        description = extract_text(props.get("Description", {}), "rich_text")
        source = "notion" if description else ""

        if not description:
            jd_url = extract_url(props.get("JD Link", {}))
            if jd_url:
                description = await fetch_jd_text(jd_url, web)
                source = "url_fetch" if description else ""

        if not description:
            return None

        score = await score_job(role, description, resume_snippet, oai)
        if score is None:
            return None

        return {
            "id": row["id"],
            "role": role,
            "company": company,
            "score": score,
            "source": source,
        }


def score_properties(score: FitScore) -> dict:
    return {
        "Fit Score": {"number": score.fit_score},
        "Skill Match": {"number": score.skill_match},
        "Years Fit": {"number": score.years_fit},
        "Domain Match": {"number": score.domain_match},
        "Fit Reasoning": {
            "rich_text": [{"type": "text", "text": {"content": score.reasoning[:2000]}}]
        },
        "Scored At": {"date": {"start": datetime.now(UTC).isoformat()}},
    }


async def write_score(client: httpx.AsyncClient, page_id: str, score: FitScore) -> None:
    r = await client.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        json={"properties": score_properties(score)},
    )
    r.raise_for_status()


def print_histogram(results: list[dict]) -> None:
    buckets = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
    for r in results:
        s = r["score"].fit_score
        if s <= 20:
            buckets["0-20"] += 1
        elif s <= 40:
            buckets["21-40"] += 1
        elif s <= 60:
            buckets["41-60"] += 1
        elif s <= 80:
            buckets["61-80"] += 1
        else:
            buckets["81-100"] += 1

    print("Score histogram:")
    for label, count in buckets.items():
        print(f"  {label:>6}: {'#' * count} ({count})")


async def main(apply: bool = False, limit: int | None = None) -> None:
    resume_snippet = load_resume_plaintext()
    logger.info("resume_loaded", chars=len(resume_snippet))

    async with (
        httpx.AsyncClient(headers=HEADERS, timeout=30.0) as notion,
        httpx.AsyncClient(timeout=20.0) as web,
    ):
        if not await db_has_required_columns(notion):
            print(
                "\nRequired Notion columns are missing "
                f"({', '.join(REQUIRED_COLUMNS)}). "
                "Add these columns to Notion first, skipping.\n"
            )
            return

        print("Querying unscored 'To Apply' rows...")
        rows = await query_unscored_rows(notion)
        if limit:
            rows = rows[:limit]
        print(f"  Found {len(rows)} rows to score\n")

        if not rows:
            print("Nothing to score.\n")
            return

        oai = AsyncOpenAI(api_key=OPENAI_API_KEY)

        start = time.monotonic()
        # Each call embeds the full ~1500-token resume snippet plus JD —
        # far heavier than other LLM calls in this codebase. Concurrency
        # 5 (as originally speced) empirically blew through gpt-4o's
        # 30k TPM limit almost immediately (verified: a live dry-run at
        # concurrency 5 against 341 rows failed 227/341, 100% of which
        # were rate_limit_exceeded, not missing-JD skips). 2 matches the
        # same real (if under-labeled) concurrency already used for
        # gpt-4o calls in cleanup_with_llm.py.
        sem = asyncio.Semaphore(2)
        scored = 0
        failed = 0
        results: list[dict] = []

        async def _one(row: dict) -> None:
            nonlocal scored, failed
            result = await score_row(row, web, oai, resume_snippet, sem)
            if result is None:
                failed += 1
            else:
                scored += 1
                results.append(result)
            if (scored + failed) % 10 == 0:
                logger.info(
                    "scoring_progress",
                    done=scored + failed,
                    total=len(rows),
                    scored=scored,
                    failed=failed,
                )

        await asyncio.gather(*[_one(row) for row in rows])

        duration_s = time.monotonic() - start
        avg_score = (
            sum(r["score"].fit_score for r in results) / len(results) if results else 0
        )

        from_notion = sum(1 for r in results if r["source"] == "notion")
        from_url = sum(1 for r in results if r["source"] == "url_fetch")

        logger.info(
            "scoring_complete",
            total=len(rows),
            scored=scored,
            failed=failed,
            from_notion=from_notion,
            from_url_fetch=from_url,
            duration_s=round(duration_s, 1),
            avg_score=round(avg_score, 1),
        )

        results.sort(key=lambda r: r["score"].fit_score, reverse=True)

        if not apply:
            print(
                f"Description source: {from_notion} from Notion, "
                f"{from_url} from live URL fetch\n"
            )
            print(f"Top {min(20, len(results))} by fit score:\n")
            for r in results[:20]:
                s = r["score"]
                print(
                    f"  {s.fit_score:>3}  [{r['company'][:15]:15s}] {r['role'][:45]:45s} "
                    f"(skill={s.skill_match} years={s.years_fit} domain={s.domain_match})"
                )
            print()
            print_histogram(results)
            est_cost = len(rows) * 0.003
            print(
                f"\n[DRY RUN] Scored {scored}/{len(rows)} rows in {duration_s:.1f}s "
                f"({failed} failed/skipped, e.g. missing JD or fetch failure).\n"
                f"Estimated cost at full volume: ~${est_cost:.2f} for this batch.\n"
                f"Re-run with --apply to write these scores to Notion.\n"
            )
            return

        print(f"Writing {scored} scores to Notion...")
        written = write_failed = 0
        for r in results:
            try:
                await write_score(notion, r["id"], r["score"])
                written += 1
                await asyncio.sleep(0.35)  # Stay under Notion's ~3 req/sec limit
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "score_write_failed", page_id=r["id"], role=r["role"], error=str(e)
                )
                write_failed += 1

        print("\n" + "=" * 72)
        print("  ✓ Scoring complete")
        print("=" * 72)
        print(f"  Total rows scanned:  {len(rows)}")
        print(f"  Scored:              {scored}  ({from_notion} from Notion, {from_url} from URL fetch)")
        print(f"  Written to Notion:   {written}")
        print(f"  Failed to score:     {failed}  (missing JD / fetch / LLM error)")
        if write_failed:
            print(f"  Failed to write:     {write_failed}  (see warnings above)")
        print(f"  Avg fit score:       {avg_score:.1f}")
        print(f"  Duration:            {duration_s:.1f}s")
        print("=" * 72 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Score Notion 'To Apply' rows against the base resume via LLM."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write scores to Notion. Default is dry-run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N unscored rows (for testing).",
    )
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply, limit=args.limit))
