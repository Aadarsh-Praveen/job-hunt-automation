"""Debug: fetch one JD page and print what the LLM sees."""
import asyncio
import httpx
from src.common.llm_location import HTTP_HEADERS, fetch_jd_text

# Replace with one of the "SKIP" jobs' jd_link from your Notion
URL = "https://jobs.ashbyhq.com/openai/PASTE-A-JOB-ID-HERE"

async def main():
    async with httpx.AsyncClient(headers=HTTP_HEADERS, follow_redirects=True) as c:
        text = await fetch_jd_text(URL, c)
        print(f"Length: {len(text)} chars\n")
        print(text[:1000] if text else "(empty)")

asyncio.run(main())
