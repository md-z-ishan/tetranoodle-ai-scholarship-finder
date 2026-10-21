"""Shared state definition for LangGraph orchestrator.

Defines the typed graph state handed between swarm agents:
Orchestrator -> Profile Analyst -> Scout -> Verifier -> Ranker -> Writer -> Critic.
Includes execution checkpoints, timings, retries log, and user-facing warnings.
"""

from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict
from app.schemas import (
    ApplicationDraft,
    Opportunity,
    Recommendation,
    StudentProfile,
    VerifiedOpportunity,
)


class SwarmState(TypedDict, total=False):
    """LangGraph shared state flowing through the 7-agent pipeline."""

    # Run Identifiers & Telemetry
    run_id: str

    # Raw Inputs provided by Student / UI
    resume_text: Optional[str]
    marksheet_text: Optional[str]
    extra_fields: Dict[str, Any]

    # Agent Artifacts
    profile: Optional[StudentProfile]
    candidates: List[Opportunity]
    verified: List[VerifiedOpportunity]
    recommendations: List[Recommendation]
    draft: Optional[ApplicationDraft]
    executive_summary: Optional[str]

    # Critic & Feedback Loops
    critic_feedback: Optional[str]
    revision_count: int

    # Observability, Resilience & Health Logging
    warnings: List[str]
    retries_log: List[Dict[str, Any]]
    timings: Dict[str, float]
    run_health: Dict[str, Any]
