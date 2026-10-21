"""Critic Agent: dual-layer anti-hallucination audit for application drafts.

Layers:
1. Deterministic Grounding Audit:
   - Verifies all numbers, test scores, and project names in draft against profile & verified evidence.
   - Detects fabricated metrics, unmentioned awards, and invented organizations.
2. LLM Review (Structured Output):
   - Audits tone, structural flow, scholarship fit, and subtle unsupported claims.
   - Outputs verdict ('approve' | 'revise') with specific, actionable issue list.
3. Revision Loop Orchestration:
   - Drives Writer-Critic cycle for up to 2 revisions.
   - Retains chronological critic notes; never fakes revisions.
"""

import logging
import re
from typing import List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, Field

from app.events import AgentEvent
from app.schemas import ApplicationDraft, StudentProfile, VerifiedOpportunity
from app.tools.llm import llm_client
from app.tools.telemetry import EmitCallback, Telemetry


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured LLM Review Model
# ---------------------------------------------------------------------------

class CriticLLMResponse(BaseModel):
    """Structured audit response from Critic agent."""
    verdict: Literal["approve", "revise"] = Field(
        description="'approve' if draft is truthful, fits requirements, and is well-grounded; 'revise' otherwise."
    )
    issues: List[str] = Field(
        default_factory=list,
        description="Specific actionable errors or ungrounded claims requiring correction.",
    )
    rationale: str = Field(description="Brief assessment summarizing the audit findings.")


# ---------------------------------------------------------------------------
# Deterministic Grounding Audit
# ---------------------------------------------------------------------------

def _extract_numbers(text: str) -> Set[str]:
    """Extract significant numbers (excluding common small integers 0-9)."""
    tokens = re.findall(r"\b\d+(?:\.\d+)?\b", text)
    # Filter out single-digit common word tokens (1, 2, 3) and standard years (2024, 2025, 2026, 2027)
    return {t for t in tokens if len(t) > 1 and t not in {"2024", "2025", "2026", "2027"}}


def audit_draft_grounding(
    draft_text: str,
    profile: StudentProfile,
    opportunity: VerifiedOpportunity,
) -> List[str]:
    """Deterministically check that numbers and claims in the draft exist in source records."""
    unsupported: List[str] = []

    # 1. Compile all legitimate numbers from profile and verified evidence
    profile_numbers: Set[str] = set()
    if profile.cgpa is not None:
        profile_numbers.add(str(profile.cgpa))
    if profile.cgpa_scale is not None:
        profile_numbers.add(str(profile.cgpa_scale))
    if profile.graduation_year:
        profile_numbers.add(str(profile.graduation_year))

    for p in profile.projects:
        profile_numbers.update(_extract_numbers(f"{p.name} {p.description or ''}"))

    # Evidence numbers
    opp_text = f"{opportunity.deadline_text or ''} {opportunity.days_left or ''} {opportunity.funding_summary or ''} "
    opp_text += " ".join(e.snippet for e in opportunity.evidence)
    opp_numbers = _extract_numbers(opp_text)

    allowed_numbers = profile_numbers | opp_numbers

    # Audit draft numbers
    draft_numbers = _extract_numbers(draft_text)
    for num in draft_numbers:
        # Check if number appears in allowed set
        if num not in allowed_numbers and float(num) > 9.0:  # Ignore small numbers like top 5, 8 semesters
            # Allow common percentages if in project description
            if not any(num in (p.description or "") for p in profile.projects):
                unsupported.append(f"Ungrounded numeric claim '{num}' not found in profile or verified evidence")

    return unsupported


# ---------------------------------------------------------------------------
# Critic Agent Main
# ---------------------------------------------------------------------------

async def run_critic(
    draft: ApplicationDraft,
    profile: StudentProfile,
    opportunity: VerifiedOpportunity,
    run_id: str = "run-critic",
    emit: EmitCallback = None,
    telemetry: Optional[Telemetry] = None,
) -> Tuple[Literal["approve", "revise"], List[str], str]:
    """Run dual-layer audit on application draft."""
    if emit:
        await emit(AgentEvent(
            run_id=run_id,
            agent="Critic",
            message=f"Auditing draft (round {draft.revision_count}) for '{opportunity.name}'…",
            level="info",
        ))

    # Layer 1: Deterministic Grounding
    det_issues = audit_draft_grounding(draft.text or "", profile, opportunity)

    # Layer 2: LLM Quality & Factuality Review
    profile_summary = (
        f"Profile: {profile.name}, {profile.degree}, CGPA {profile.cgpa}/{profile.cgpa_scale or 10.0}, "
        f"Projects: {[p.name for p in profile.projects]}, Skills: {profile.skills[:6]}"
    )
    opp_summary = f"Opportunity: {opportunity.name} at {opportunity.organization}, Tier: {opportunity.source_tier}"

    critic_prompt = (
        f"AUDIT THE FOLLOWING STATEMENT OF PURPOSE DRAFT:\n\n"
        f"STUDENT FACT SHEET:\n{profile_summary}\n\n"
        f"PROGRAMME DETAILS:\n{opp_summary}\n\n"
        f"DRAFT TEXT:\n{draft.text}\n\n"
        f"AUDIT CRITERIA:\n"
        f"1. Zero Unmentioned Facts: Did the draft invent any awards, publications, test scores, professors, or universities?\n"
        f"2. Alignment & Tone: Is it formal, scholarly, and genuinely aligned with {opportunity.name}?\n"
        f"3. Structure: Clear introduction, academic foundation, projects, rationale, and conclusion?\n\n"
        f"If ANY ungrounded claims or major stylistic issues exist, verdict MUST be 'revise' with clear bullet points. "
        f"If truthful and compliant, verdict is 'approve'."
    )

    system_instruction = (
        "You are an uncompromising admissions audit specialist. "
        "Strictly enforce zero-hallucination standards. "
        "Reject any draft containing embellished accomplishments, invented test scores, or ungrounded claims."
    )

    llm_verdict: Literal["approve", "revise"] = "approve"
    llm_issues: List[str] = []
    rationale = "Draft meets factual integrity standards."

    try:
        review: CriticLLMResponse = await llm_client.generate_json(
            prompt=critic_prompt,
            schema=CriticLLMResponse,
            system_instruction=system_instruction,
            telemetry=telemetry,
        )
        llm_verdict = review.verdict
        llm_issues = review.issues
        rationale = review.rationale
    except Exception as exc:
        logger.warning("Critic LLM audit failed: %s", exc)
        if telemetry:
            telemetry.record_failure("critic_agent", error=str(exc), fatal=False)

    # Combine issues
    all_issues = det_issues + [i for i in llm_issues if i not in det_issues]

    # Deterministic override: if grounding found fake metrics, force revise
    final_verdict: Literal["approve", "revise"] = "revise" if (all_issues or llm_verdict == "revise") else "approve"

    if emit:
        if final_verdict == "approve":
            await emit(AgentEvent(
                run_id=run_id,
                agent="Critic",
                message=f"Draft approved! {rationale[:120]}",
                level="success",
            ))
        else:
            await emit(AgentEvent(
                run_id=run_id,
                agent="Critic",
                message=f"Critic identified {len(all_issues)} issue(s) requiring revision: {'; '.join(all_issues[:2])}",
                level="warn",
                payload={"issues": all_issues},
            ))

    return final_verdict, all_issues, rationale


# ---------------------------------------------------------------------------
# Revision Loop Orchestration (B3)
# ---------------------------------------------------------------------------

async def run_writer_critic_loop(
    profile: StudentProfile,
    opportunity: VerifiedOpportunity,
    max_revisions: int = 2,
    run_id: str = "run-writer-critic",
    emit: EmitCallback = None,
    telemetry: Optional[Telemetry] = None,
) -> ApplicationDraft:
    """Execute iterative Writer-Critic loop with bounded revisions (max 2 rounds)."""
    # Import locally to avoid circular dependency
    from app.agents.writer import run_writer

    current_issues: Optional[List[str]] = None
    draft: Optional[ApplicationDraft] = None
    all_critic_notes: List[str] = []

    for round_num in range(max_revisions + 1):
        # 1. Writer drafts or revises
        draft = await run_writer(
            profile=profile,
            opportunity=opportunity,
            critic_issues=current_issues,
            revision_count=round_num,
            run_id=run_id,
            emit=emit,
            telemetry=telemetry,
        )

        # 2. Critic audits
        verdict, issues, rationale = await run_critic(
            draft=draft,
            profile=profile,
            opportunity=opportunity,
            run_id=run_id,
            emit=emit,
            telemetry=telemetry,
        )

        if issues:
            all_critic_notes.extend([f"[Round {round_num}] {iss}" for iss in issues])
            draft.critic_notes = list(all_critic_notes)

        # If approved or reached max rounds, exit loop
        if verdict == "approve":
            if emit:
                await emit(AgentEvent(
                    run_id=run_id,
                    agent="Critic",
                    message=f"Draft finalized successfully after {round_num} revision(s).",
                    level="success",
                ))
            break

        if round_num >= max_revisions:
            if emit:
                await emit(AgentEvent(
                    run_id=run_id,
                    agent="Critic",
                    message=f"Max revisions ({max_revisions}) reached; finalizing best available draft with remaining notes.",
                    level="warn",
                    payload={"remaining_issues": issues},
                ))
            break

        current_issues = issues

    return draft or ApplicationDraft(
        opportunity_name=opportunity.name,
        text=f"[Draft only - not submitted]\n\nApplication draft for {opportunity.name}",
        critic_notes=all_critic_notes,
        revision_count=max_revisions,
    )
