"""Central configuration: env vars, file-backed config (slug lists, role keywords)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


class Settings(BaseSettings):
    """All environment variables, validated and typed."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Notion
    notion_api_key: str = ""
    notion_parent_page_id: str = ""
    notion_applications_db_id: str = ""
    notion_contacts_db_id: str = ""
    notion_companies_db_id: str = ""

    # Apify
    apify_api_key: str = ""
    apify_universal_actor_id: str = ""

    # Contact discovery
    apollo_api_key: str = ""
    hunter_api_key: str = ""
    contactout_api_key: str = ""
    neverbounce_api_key: str = ""

    # Outreach
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    from_email: str = ""
    firecrawl_api_key: str = ""

    # Drive
    google_drive_folder_id: str = ""
    google_drive_backups_folder_id: str = ""

    # Optional Anthropic
    anthropic_api_key: str = ""

    # Misc
    alert_email: str = ""
    timezone: str = "America/New_York"
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton settings instance."""
    return Settings()


def load_slugs(ats: str) -> list:
    """Load company slugs for one ATS.

    Prefers data/companies/{ats}.json (supports string slugs AND dict slugs
    for Workday). Falls back to {ats}_slugs.txt for backward compat.
    """
    json_path = DATA_DIR / "companies" / f"{ats}.json"
    if json_path.exists():
        return json.loads(json_path.read_text(encoding="utf-8"))

    txt_path = DATA_DIR / "companies" / f"{ats}_slugs.txt"
    if not txt_path.exists():
        return []

    return [
        line.strip()
        for line in txt_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


@lru_cache(maxsize=1)
def load_role_keywords() -> dict:
    """Load role title patterns + include/exclude keywords."""
    return json.loads((DATA_DIR / "role_keywords.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_locations() -> dict:
    """Load preferred/excluded locations."""
    return json.loads((DATA_DIR / "locations.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_exclude_keywords() -> dict:
    """Load seniority/experience/credential exclude keywords."""
    return json.loads((DATA_DIR / "exclude_keywords.json").read_text(encoding="utf-8"))