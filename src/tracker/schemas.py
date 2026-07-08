"""Notion database schemas + Pydantic models for type-safe writes.

Two parts:
1. Pydantic models — typed internal representation of rows in each DB
2. Notion property schemas — dicts in Notion API format, used by seed_notion.py
   to create the databases
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# ============================================================================
# Status enums (used by Pydantic models + must match Notion select options)
# ============================================================================

ApplicationStatus = Literal[
    "To Apply",
    "Applied",
    "Screened",
    "OA Sent",
    "Interviewing",
    "Final Round",
    "Offer",
    "Rejected",
    "Ghosted",
    "Withdrawn",
]

ContactStatus = Literal[
    "Queued",
    "Sent",
    "Opened",
    "Replied",
    "Bounced",
    "Closed",
]

CompanyTier = Literal["T1", "T2", "Startup", "YC"]

CompanyStatus = Literal[
    "Researching",
    "Targeted",
    "Applied",
    "Passed",
]


# ============================================================================
# Pydantic models — internal representation of rows
# ============================================================================


class ApplicationRow(BaseModel):
    """A row in the Applications database."""

    role: str
    company: str
    jd_link: str
    ats_source: str
    date_posted: datetime | None = None
    date_discovered: datetime
    date_applied: datetime | None = None
    resume_version_url: str = ""
    resume_latex_url: str = ""
    diff_url: str = ""
    self_ats_score: float | None = None
    status: ApplicationStatus = "To Apply"
    location: str = ""
    department: str = ""
    notes: str = ""
    dedupe_hash: str


class ContactRow(BaseModel):
    """A row in the Contacts database."""

    name: str
    title: str = ""
    company: str = ""
    email: str = ""
    linkedin_url: str = ""
    source: str = ""
    first_contact_date: datetime | None = None
    last_contact_date: datetime | None = None
    sequence_step: int = 0
    status: ContactStatus = "Queued"
    notes: str = ""


class CompanyRow(BaseModel):
    """A row in the Companies database."""

    name: str
    ats_slug: str = ""
    ats_type: str = ""
    tier: CompanyTier = "T2"
    status: CompanyStatus = "Researching"
    notes: str = ""


# ============================================================================
# Notion API property schemas — used by seed_notion.py to create databases
# ============================================================================

APPLICATIONS_SCHEMA: dict = {
    "Role": {"title": {}},
    "Company": {"rich_text": {}},
    "JD Link": {"url": {}},
    "ATS Source": {
        "select": {
            "options": [
                {"name": "greenhouse", "color": "green"},
                {"name": "lever", "color": "blue"},
                {"name": "ashby", "color": "purple"},
                {"name": "workable", "color": "yellow"},
                {"name": "recruitee", "color": "orange"},
                {"name": "personio", "color": "pink"},
                {"name": "workday", "color": "red"},
                {"name": "apify_universal", "color": "gray"},
                {"name": "apify_faang", "color": "brown"},
                {"name": "jobspy_google", "color": "teal"},
                {"name": "indeed", "color": "default"},
                {"name": "linkedin", "color": "default"},
                {"name": "manual", "color": "default"},
            ]
        }
    },
    "Date Posted": {"date": {}},
    "Date Discovered": {"date": {}},
    "Date Applied": {"date": {}},
    "Resume Version": {"url": {}},
    "Resume LaTeX": {"url": {}},
    "Diff Link": {"url": {}},
    "Self ATS Score": {"number": {"format": "number"}},
    "Status": {
        "select": {
            "options": [
                {"name": "To Apply", "color": "gray"},
                {"name": "Applied", "color": "blue"},
                {"name": "Screened", "color": "purple"},
                {"name": "OA Sent", "color": "orange"},
                {"name": "Interviewing", "color": "yellow"},
                {"name": "Final Round", "color": "pink"},
                {"name": "Offer", "color": "green"},
                {"name": "Rejected", "color": "red"},
                {"name": "Ghosted", "color": "brown"},
                {"name": "Withdrawn", "color": "default"},
            ]
        }
    },
    "Location": {"rich_text": {}},
    "Department": {"rich_text": {}},
    "Notes": {"rich_text": {}},
    "Dedupe Hash": {"rich_text": {}},
}


CONTACTS_SCHEMA: dict = {
    "Name": {"title": {}},
    "Title": {"rich_text": {}},
    "Company": {"rich_text": {}},
    "Email": {"email": {}},
    "LinkedIn URL": {"url": {}},
    "Source": {
        "select": {
            "options": [
                {"name": "Apollo", "color": "green"},
                {"name": "Hunter", "color": "blue"},
                {"name": "ContactOut", "color": "purple"},
                {"name": "Manual", "color": "default"},
                {"name": "Pattern-inferred", "color": "yellow"},
            ]
        }
    },
    "First Contact Date": {"date": {}},
    "Last Contact Date": {"date": {}},
    "Sequence Step": {"number": {"format": "number"}},
    "Status": {
        "select": {
            "options": [
                {"name": "Queued", "color": "gray"},
                {"name": "Sent", "color": "blue"},
                {"name": "Opened", "color": "purple"},
                {"name": "Replied", "color": "green"},
                {"name": "Bounced", "color": "red"},
                {"name": "Closed", "color": "default"},
            ]
        }
    },
    "Notes": {"rich_text": {}},
}


COMPANIES_SCHEMA: dict = {
    "Name": {"title": {}},
    "ATS Slug": {"rich_text": {}},
    "ATS Type": {
        "select": {
            "options": [
                {"name": "greenhouse", "color": "green"},
                {"name": "lever", "color": "blue"},
                {"name": "ashby", "color": "purple"},
                {"name": "workable", "color": "yellow"},
                {"name": "workday", "color": "red"},
                {"name": "custom", "color": "default"},
            ]
        }
    },
    "Tier": {
        "select": {
            "options": [
                {"name": "T1", "color": "green"},
                {"name": "T2", "color": "blue"},
                {"name": "Startup", "color": "purple"},
                {"name": "YC", "color": "orange"},
            ]
        }
    },
    "Status": {
        "select": {
            "options": [
                {"name": "Researching", "color": "gray"},
                {"name": "Targeted", "color": "blue"},
                {"name": "Applied", "color": "green"},
                {"name": "Passed", "color": "red"},
            ]
        }
    },
    "Notes": {"rich_text": {}},
}