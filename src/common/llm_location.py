"""LLM-based location extraction for jobs with empty location field.

Strategy:
  1. Fetch the public JD page via httpx
  2. Strip HTML to plain text (first ~2500 chars)
  3. Ask gpt-4o-mini to return location in a strict format
  4. Cost: ~$0.0004 per call → ~$1-2/month at our volume
"""

from __future__ import annotations

import asyncio
import os
import re

import httpx
from openai import AsyncOpenAI

from src.common.logger import get_logger

logger = get_logger("llm_location")

_client: AsyncOpenAI | None = None

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobHuntBot/1.0)",
    "Accept": "text/html,application/xhtml+xml",
}

SYSTEM_PROMPT = (
    "You extract the job location from a job description. "
    "Reply with EXACTLY one line, in ONE of these formats:\n"
    "  - 'City, State' for a US-located role (e.g. 'San Francisco, CA')\n"
    "  - 'City, Country' for a non-US role (e.g. 'London, UK')\n"
    "  - 'Remote (US)' if remote and US-restricted\n"
    "  - 'Remote (Worldwide)' if remote with no geo restriction\n"
    "  - 'Remote (Country)' if remote but restricted to a specific non-US country\n"
    "  - 'Unknown' if location is not stated anywhere\n"
    "No quotes, no explanation, no extra text. Just the location string."
)


def _get_client() -> AsyncOpenAI | None:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        _client = AsyncOpenAI(api_key=api_key)
    return _client


def _strip_html(html: str, max_chars: int = 2500) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


async def fetch_jd_text(jd_link: str, http_client: httpx.AsyncClient) -> str:
    try:
        r = await http_client.get(jd_link, timeout=15, follow_redirects=True)
        r.raise_for_status()
        return _strip_html(r.text)
    except Exception as e:
        logger.warning("jd_fetch_failed", jd_link=jd_link, error=str(e))
        return ""


async def llm_extract_location(jd_text: str) -> str:
    """Return cleaned location string, or '' on failure / 'Unknown'."""
    client = _get_client()
    if not client or not jd_text:
        return ""
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": jd_text},
            ],
            max_tokens=30,
            temperature=0,
        )
        out = resp.choices[0].message.content.strip().strip("'\"")
        return "" if out.lower() == "unknown" else out
    except Exception as e:
        logger.warning("llm_extract_failed", error=str(e))
        return ""


async def resolve_location(jd_link: str, http_client: httpx.AsyncClient) -> str:
    """One-shot helper: fetch JD, extract location via LLM."""
    text = await fetch_jd_text(jd_link, http_client)
    if not text:
        return ""
    return await llm_extract_location(text)


async def resolve_many(
    items: list[tuple[str, str]],
    concurrency: int = 10,
) -> dict[str, str]:
    """Resolve a batch in parallel. items = [(key, jd_link), ...]. Returns {key: location}."""
    if not _get_client():
        logger.warning("openai_key_missing_skipping_llm")
        return {}

    results: dict[str, str] = {}
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(headers=HTTP_HEADERS) as http_client:
        async def one(key: str, link: str) -> None:
            async with sem:
                loc = await resolve_location(link, http_client)
                if loc:
                    results[key] = loc

        await asyncio.gather(*[one(k, link) for k, link in items])

    return results
