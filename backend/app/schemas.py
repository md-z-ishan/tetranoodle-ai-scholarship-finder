"""Pydantic v2 data models for student profiles, opportunities, and application drafts.

Strict Rules:
- No made-up defaults.
- Every opportunity-related field is Optional, defaulting to None or empty list.
- Facts not present on an official page are explicitly stored as null in unknown_fields.
- Zero hardcoding to any student, country, degree level, or discipline.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


# -----------------------------------------------------------------------------
# (a) Foundational Evidence & Project Models
# -----------------------------------------------------------------------------

class Evidence(BaseModel):
    """Verifiable proof snippet extracted directly from a live web page."""
    url: str
    fetched_at: str
    snippet: str


class Project(BaseModel):
    """Student technical project extracted from resume or public GitHub."""
    name: str
    description: Optional[str] = None
    tech: List[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# (b) Student Profile Model (Enriched, Traceable, Honest about Gaps)
# -----------------------------------------------------------------------------

class StudentProfile(BaseModel):
    """Normalized profile extracted from resume, transcript, and GitHub."""
    name: Optional[str] = None
    degree: Optional[str] = None
    field: Optional[str] = None
    cgpa: Optional[float] = None
    graduation_year: Optional[int] = None
    skills: List[str] = Field(default_factory=list)
    projects: List[Project] = Field(default_factory=list)
    achievements: List[str] = Field(default_factory=list)
    target_countries: List[str] = Field(default_factory=list)
    budget_note: Optional[str] = None
    language_tests: List[str] = Field(default_factory=list)
    github_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    source_notes: List[str] = Field(default_factory=list)
    missing_info: List[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# (c) Candidate Opportunity Model (Discovered by Scout)
# -----------------------------------------------------------------------------

class Opportunity(BaseModel):
    """Raw scholarship or degree opportunity identified from live web search."""
    name: Optional[str] = None
    organization: Optional[str] = None
    country: Optional[str] = None
    url: Optional[str] = None
    funding_summary: Optional[str] = None
    deadline: Optional[str] = None
    source_snippet: Optional[str] = None


