"""LLM-based job exclusion filter.

Uses gpt-4o (not mini) — mini hallucinates titles and over-rigidly classifies
Lead/Researcher/Tech Lead as managerial. gpt-4o follows the rules properly.

Cost: ~$0.005/call × 40-120 calls/day = ~$6-18/month. Cheap insurance.
"""

import asyncio
import json

import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel

logger = structlog.get_logger()

EXCLUSION_PROMPT = """You filter job postings for a candidate with this profile:
- 3 years of professional experience in AI/ML/Data
- Open to IC (Individual Contributor) roles up to Senior/Lead/Staff level
- Manager/Director/VP titles are too senior
- US-based positions only

==========  IC vs MANAGER — MEMORIZE THIS  ==========

These titles are ALL IC roles. INCLUDE them:
- "Senior X", "Lead X", "Staff X", "Principal X" (where X = Engineer/Scientist/Researcher/Analyst)
- "Tech Lead" (technical leadership, no direct reports)
- "Researcher", "Research Engineer", "Research Scientist", "Applied Scientist"
- "Member of Technical Staff", "Forward Deployed Engineer"

These titles are MANAGERS. EXCLUDE them:
- "X Manager", "Manager of X", "Manager, X" (any role)
- "Director", "VP", "Vice President", "Head of"
- "Engineering Manager", "Tech Lead Manager", "Data Science Manager"
- "Chief X", "Distinguished X", "Fellow"
- "Program Manager", "Product Manager" (not engineering roles)

KEY DISTINCTION:
- "Lead Data Scientist"     → INCLUDE (IC, technical lead)
- "Data Science Manager"    → EXCLUDE (Manager)
- "Tech Lead, ML"           → INCLUDE (IC technical lead)
- "Tech Lead Manager"       → EXCLUDE (people manager)
- "Senior Research Engineer"→ INCLUDE (IC researcher)
- "Engineering Manager"     → EXCLUDE (Manager)

==========  YEARS OF EXPERIENCE  ==========
"5+ years required"  → INCLUDE
"6+ years required"  → EXCLUDE
"7+ years required"  → EXCLUDE
"8+ years required"  → EXCLUDE
"10+ years required" → EXCLUDE
A "preferred" experience number is NEVER grounds for exclusion.

==========  OTHER EXCLUSION RULES  ==========
EXCLUDE only if ONE of these is CLEARLY true from the actual text:
1. Hard PhD requirement ("PhD required" — NOT "PhD preferred")
2. Internship / Co-op / requires current student enrollment
3. Location is explicitly a non-US country (the JD names a foreign city/country as the work location)
4. Pure hardware-only role with no software/ML/data component
5. Pure sales / BD / IT helpdesk role

==========  ANTI-HALLUCINATION RULE  ==========
You MUST be able to quote the EXACT verbatim phrase from the provided title or
description that justifies exclusion. Put that quote in the "evidence" field.

If you cannot find a verbatim quote, set exclude=false. Do NOT infer or assume.

DO NOT exclude "Lead X" just because of the word "Lead".
DO NOT exclude "Researcher" — researchers are IC roles.
DO NOT exclude based on "may imply" or "could indicate" — only on what's explicitly stated.

Title: {title}

Description (excerpt):
{description}

Respond with ONLY valid JSON in this exact shape:
{{
  "exclude": <true|false>,
  "reason": "<one short sentence>",
  "evidence": "<exact verbatim quote from title or description, or empty string if exclude=false>"
}}"""


class FilterDecision(BaseModel):
    exclude: bool
    reason: str
    evidence: str = ""


async def should_exclude(
    title: str,
    description: str,
    client: AsyncOpenAI,
    model: str = "gpt-4o",
) -> FilterDecision:
    desc_excerpt = (description or "")[:2500]
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": EXCLUSION_PROMPT.format(
                        title=title or "(missing)",
                        description=desc_excerpt or "(missing)",
                    ),
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=200,
        )
        data = json.loads(resp.choices[0].message.content)
        decision = FilterDecision(
            exclude=bool(data.get("exclude", False)),
            reason=str(data.get("reason", ""))[:200],
            evidence=str(data.get("evidence", ""))[:300],
        )

        # Post-hoc anti-hallucination check: if exclude=true but evidence isn't
        # actually in the input, flip to include.
        if decision.exclude and decision.evidence:
            haystack = (title + " " + desc_excerpt).lower()
            if decision.evidence.lower().strip() not in haystack:
                logger.warning(
                    "hallucinated_evidence",
                    title=title[:60],
                    evidence=decision.evidence[:80],
                    reason=decision.reason[:80],
                )
                decision.exclude = False
                decision.reason = f"hallucinated: {decision.reason}"

        return decision
    except Exception as e:
        logger.warning("llm_filter_error", title=(title or "")[:50], error=str(e))
        return FilterDecision(exclude=False, reason="llm_error_default_include")


async def batch_filter(jobs, client: AsyncOpenAI, concurrency: int = 5) -> list:
    if not jobs:
        return []

    sem = asyncio.Semaphore(concurrency)

    def _get(job, *attrs):
        for a in attrs:
            v = getattr(job, a, None) if not isinstance(job, dict) else job.get(a)
            if v:
                return v
        return ""

    async def check_one(job):
        async with sem:
            decision = await should_exclude(
                _get(job, "title", "role"),
                _get(job, "description"),
                client,
            )
            return job, decision

    results = await asyncio.gather(*[check_one(j) for j in jobs])
    kept, excluded = [], []
    for job, decision in results:
        (excluded if decision.exclude else kept).append((job, decision))

    logger.info(
        "llm_filter_complete",
        input_count=len(jobs),
        kept=len(kept),
        excluded=len(excluded),
    )
    for job, decision in excluded[:10]:
        logger.info(
            "llm_excluded",
            title=_get(job, "title", "role")[:80],
            reason=decision.reason,
        )

    return [job for job, _ in kept]
