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
# GitHub Enrichment Models
# -----------------------------------------------------------------------------

class GithubRepo(BaseModel):
    """Public repository details fetched from GitHub REST API."""
    name: str
    description: Optional[str] = None
    language: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    stars: int = 0
    last_pushed: Optional[str] = None
    readme_excerpt: Optional[str] = None


class GithubProfile(BaseModel):
    """Aggregated public GitHub developer profile and activity."""
    username: str
    name: Optional[str] = None
    bio: Optional[str] = None
    public_repos: int = 0
    followers: int = 0
    top_languages: List[str] = Field(default_factory=list)
    repos: List[GithubRepo] = Field(default_factory=list)
    error: Optional[str] = None



# -----------------------------------------------------------------------------
# (b) Student Profile Model (Enriched, Traceable, Honest about Gaps)
# -----------------------------------------------------------------------------

class FieldConfidenceScore(BaseModel):
    """Grounded evidence confidence score for a student profile attribute."""
    field_name: str
    confidence: float
    reason: Optional[str] = None


class StudentProfile(BaseModel):
    """Normalized profile extracted from resume, transcript, and GitHub."""
    name: Optional[str] = None
    degree: Optional[str] = None
    target_degree_level: Optional[str] = None
    field: Optional[str] = None
    nationality: Optional[str] = None
    cgpa: Optional[float] = None
    cgpa_scale: Optional[float] = None
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
    field_confidence: List[FieldConfidenceScore] = Field(default_factory=list)



class ExtraInputs(BaseModel):
    """User-provided form fields overriding or augmenting parsed documents."""
    github_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    target_countries: List[str] = Field(default_factory=list)
    budget_note: Optional[str] = None
    degree_level: Optional[str] = None
    target_degree_level: Optional[str] = None
    nationality: Optional[str] = None
    intended_field: Optional[str] = None
    other_links: List[str] = Field(default_factory=list)



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


# -----------------------------------------------------------------------------
# (d) Verified Opportunity Model (Audited by Verifier) — Phase 4
# -----------------------------------------------------------------------------

SourceTier = Literal["official", "reputable", "unknown"]
VerificationStatus = Literal["verified", "partial", "unverified", "rejected"]
DeadlineStatus = Literal["open", "closing_soon", "passed", "unclear", "rolling"]
FundingTypeVerified = Literal["full", "partial", "tuition_waiver", "stipend_only", "unknown"]


class VerifiedOpportunity(Opportunity):
    """Deeply verified opportunity with official page evidence, audits, and confidence.

    Confidence formula (documented here for reproducibility):
      raw = (fields_evidenced / 4) * source_tier_factor * (1.0 if link_alive else 0.3)
      source_tier_factor: official=1.0, reputable=0.75, unknown=0.5
      Confidence clamped to [0.0, 1.0].
    """
    # Verification results
    eligibility_met: List[str] = Field(default_factory=list)
    eligibility_gaps: List[str] = Field(default_factory=list)
    unknown_fields: List[str] = Field(default_factory=list)
    needs_from_student: List[str] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)

    # Source quality
    source_tier: SourceTier = "unknown"
    official_domain: Optional[bool] = None
    link_alive: Optional[bool] = None
    fetched_via: Optional[str] = None        # "httpx" | "tavily_extract" | "bs4" | None
    fetched_at: Optional[str] = None

    # Deadline analysis
    deadline_date: Optional[str] = None      # ISO date string or null
    deadline_text: Optional[str] = None      # Raw text from page
    days_left: Optional[int] = None
    deadline_status: DeadlineStatus = "unclear"

    # Funding details
    funding_type: FundingTypeVerified = "unknown"
    degree_levels: List[str] = Field(default_factory=list)
    nationality_notes: Optional[str] = None
    apply_steps: List[str] = Field(default_factory=list)

    # Per-field evidence dict: field_name -> Evidence
    field_evidence: Dict[str, Any] = Field(default_factory=dict)

    # Computed confidence and status
    confidence: float = 0.0
    status: VerificationStatus = "unverified"
    rejection_reason: Optional[str] = None


# -----------------------------------------------------------------------------
# (e) Final Swarm Output Models (Ranker, Writer, Critic & Run Lifecycle)
# -----------------------------------------------------------------------------

FundingType = Literal["full_ride", "tuition_waiver", "stipend_only", "partial", "loan_assistance", "unknown"]
UrgencyLevel = Literal["urgent", "upcoming", "relaxed", "expired", "unknown"]


class ScoreBreakdown(BaseModel):
    """Transparent per-component score with short rationale."""
    fit: float = 0.0
    fit_reason: str = ""
    funding: float = 0.0
    funding_reason: str = ""
    urgency: float = 0.0
    urgency_reason: str = ""
    risk: float = 0.0
    risk_reason: str = ""


class Recommendation(BaseModel):
    """Ranked top recommendation with transparent justification and student action items."""
    rank: int
    opportunity: VerifiedOpportunity
    score: float = 0.0                        # 0–100 composite score
    score_breakdown: Optional[ScoreBreakdown] = None
    why_it_fits: str = ""
    urgency_tag: UrgencyLevel = "unknown"
    how_to_apply: List[str] = Field(default_factory=list)
    still_needed: List[str] = Field(default_factory=list)


class ApplicationDraft(BaseModel):
    """Personalized statement of purpose / application draft backed strictly by profile facts."""
    opportunity_name: Optional[str] = None
    text: Optional[str] = None
    based_on_profile_fields: List[str] = Field(default_factory=list)
    critic_notes: List[str] = Field(default_factory=list)
    revision_count: int = 0
    label: str = "Draft only - not submitted"


class RunResult(BaseModel):
    """Consolidated end-to-end swarm execution payload returned to client & saved in history."""
    run_id: str
    profile: Optional[StudentProfile] = None
    candidates: List[Opportunity] = Field(default_factory=list)
    verified: List[VerifiedOpportunity] = Field(default_factory=list)
    recommendations: List[Recommendation] = Field(default_factory=list)
    draft: Optional[ApplicationDraft] = None
    executive_summary: Optional[str] = None
    what_we_could_not_verify: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    timings: Dict[str, float] = Field(default_factory=dict)
    run_health: Dict[str, Any] = Field(default_factory=dict)
