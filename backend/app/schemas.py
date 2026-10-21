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
