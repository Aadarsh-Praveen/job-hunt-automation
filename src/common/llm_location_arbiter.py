"""LLM arbiter for jobs where the Location field is ambiguous
('Remote', 'Anywhere', empty). Reads the JD body to determine whether
the role is US-only or open to non-US workers.

UNCLEAR is treated as "include, but flag" by callers — a job that says
just "Remote" with no evidence either way in the JD shouldn't be
silently dropped, since the user reviews the Notion queue manually.
"""

from __future__ import annotations

import json

from openai import AsyncOpenAI
from pydantic import BaseModel

from src.common.logger import get_logger

logger = get_logger("llm_location_arbiter")

ARBITER_PROMPT = """You determine whether a remote job is US-only or open to non-US applicants.

The Location field said only: {location}
Full description:
{description}

Decide based on the description text:
- If the JD says US-only, US-based, must be in the US, authorized to work in the US, or similar -> US_ONLY
- If the JD says worldwide, global, any country, EMEA/APAC/EU, or names a specific non-US country -> NON_US
- If unclear or no evidence either way -> UNCLEAR

Respond with ONLY valid JSON:
{{"decision": "US_ONLY" | "NON_US" | "UNCLEAR", "evidence": "<exact verbatim quote or empty>"}}"""


class ArbiterDecision(BaseModel):
    decision: str  # "US_ONLY" | "NON_US" | "UNCLEAR"
    evidence: str = ""


async def arbitrate(
    location: str,
    description: str,
    client: AsyncOpenAI,
    model: str = "gpt-4o-mini",
) -> ArbiterDecision:
    if not description:
        return ArbiterDecision(decision="UNCLEAR", evidence="")

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": ARBITER_PROMPT.format(
                        location=location or "(empty)",
                        description=(description or "")[:3000],
                    ),
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=120,
        )
        data = json.loads(resp.choices[0].message.content)
        return ArbiterDecision(
            decision=str(data.get("decision", "UNCLEAR"))[:20],
            evidence=str(data.get("evidence", ""))[:300],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("arbiter_error", error=str(e))
        return ArbiterDecision(decision="UNCLEAR", evidence="")
