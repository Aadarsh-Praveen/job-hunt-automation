"""Hunter.io API client.

Free tier: 25 searches/month. Each domain_search or email_finder = 1 credit.
email_verifier is separate (50/month free).

Docs: https://hunter.io/api-documentation/v2

Security note: We use the X-API-Key header instead of the api_key query param
so the key doesn't appear in logs / URLs.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx
from dotenv import load_dotenv

from src.common.logger import get_logger

logger = get_logger("contacts.hunter")
load_dotenv()

HUNTER_BASE = "https://api.hunter.io/v2"
HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")


def _headers() -> dict:
    return {"X-Api-Key": HUNTER_API_KEY} if HUNTER_API_KEY else {}


def _check_key() -> bool:
    if not HUNTER_API_KEY:
        logger.warning("hunter_no_api_key")
        return False
    return True


async def domain_search(
    domain: str,
    limit: int = 10,
    department: Optional[str] = None,
    seniority: Optional[str] = None,
) -> list[dict]:
    if not _check_key():
        return []
    params: dict = {"domain": domain, "limit": min(limit, 100)}
    if department:
        params["department"] = department
    if seniority:
        params["seniority"] = seniority
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=_headers()) as client:
            r = await client.get(f"{HUNTER_BASE}/domain-search", params=params)
            r.raise_for_status()
            data = r.json()
        emails = data.get("data", {}).get("emails", []) or []
        logger.info("hunter_domain_search", domain=domain, found=len(emails))
        return emails
    except Exception as e:
        logger.warning("hunter_domain_search_failed", domain=domain, error=str(e))
        return []


async def email_finder(domain: str, first_name: str, last_name: str) -> Optional[dict]:
    if not _check_key():
        return None
    params = {"domain": domain, "first_name": first_name, "last_name": last_name}
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=_headers()) as client:
            r = await client.get(f"{HUNTER_BASE}/email-finder", params=params)
            r.raise_for_status()
            data = r.json()
        result = data.get("data", {}) or {}
        if not result.get("email"):
            return None
        logger.info("hunter_email_finder", domain=domain, score=result.get("score"))
        return result
    except Exception as e:
        logger.warning("hunter_email_finder_failed", error=str(e))
        return None


async def email_verifier(email: str) -> Optional[dict]:
    if not _check_key():
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=_headers()) as client:
            r = await client.get(f"{HUNTER_BASE}/email-verifier", params={"email": email})
            r.raise_for_status()
            return r.json().get("data", {})
    except Exception as e:
        logger.warning("hunter_verify_failed", email=email, error=str(e))
        return None


async def account_info() -> dict:
    if not _check_key():
        return {}
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=_headers()) as client:
            r = await client.get(f"{HUNTER_BASE}/account")
            r.raise_for_status()
            return r.json().get("data", {})
    except Exception as e:
        logger.warning("hunter_account_failed", error=str(e))
        return {}
