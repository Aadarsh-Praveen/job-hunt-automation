"""LLM-based JD fit scorer. Compares a job's title + description
against the candidate's base resume and returns a structured score."""

from __future__ import annotations

import json

from openai import AsyncOpenAI
from pydantic import BaseModel

from src.common.logger import get_logger

logger = get_logger("jd_fit_scorer")

SCORER_PROMPT = """You are scoring how well a candidate's resume matches a job description.

CANDIDATE PROFILE (base resume):
{resume_snippet}

JOB TITLE: {title}

JOB DESCRIPTION (excerpt):
{description}

Score four dimensions (integers 0-100):

1. skill_match: How many of the job's REQUIRED skills does the candidate
   have based on their resume? 100 = all required skills present. 50 =
   half present. 0 = none present. Consider concrete tools/languages
   (Python, PyTorch, AWS, etc.), not soft skills.

2. years_fit: Does the candidate's ~3 years of experience match the
   role's seniority? 100 = perfect match (asks for 2-4 years or "mid-level").
   70 = stretch but reachable (asks for 5 years). 40 = big stretch
   (asks for 7 years). 0 = wildly mismatched (asks for 15+ years OR
   requires <1 year i.e. new grad only).

3. domain_match: Is the role aligned with AI/ML/Data engineering, science,
   analysis, GenAI, or applied AI? 100 = directly in those domains.
   60 = adjacent (data infrastructure, MLOps). 30 = tangential (general
   backend SWE at an AI company). 0 = unrelated (marketing, sales,
   hardware, pure devops).

4. fit_score: Overall composite. Weight skill_match 40%, years_fit 30%,
   domain_match 30%. But override to a lower number if any dimension is
   near-zero (a 0 in domain_match caps overall at 30).

reasoning: One or two sentences explaining the score. Reference specific
skills or experience gaps. Be concise.

Respond with ONLY valid JSON:
{{"skill_match": <int>, "years_fit": <int>, "domain_match": <int>,
  "fit_score": <int>, "reasoning": "<string>"}}"""


class FitScore(BaseModel):
    skill_match: int
    years_fit: int
    domain_match: int
    fit_score: int
    reasoning: str


async def score_job(
    title: str,
    description: str,
    resume_snippet: str,
    client: AsyncOpenAI,
    model: str = "gpt-4o",
) -> FitScore | None:
    """Score a single job. Returns None on failure (don't crash caller)."""
    if not description:
        return None

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": SCORER_PROMPT.format(
                        resume_snippet=resume_snippet[:6000],
                        title=title or "(missing)",
                        description=description[:3500],
                    ),
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=250,
        )
        data = json.loads(resp.choices[0].message.content)
        return FitScore(
            skill_match=max(0, min(100, int(data.get("skill_match", 0)))),
            years_fit=max(0, min(100, int(data.get("years_fit", 0)))),
            domain_match=max(0, min(100, int(data.get("domain_match", 0)))),
            fit_score=max(0, min(100, int(data.get("fit_score", 0)))),
            reasoning=str(data.get("reasoning", ""))[:500],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("scorer_failed", title=(title or "")[:60], error=str(e))
        return None
