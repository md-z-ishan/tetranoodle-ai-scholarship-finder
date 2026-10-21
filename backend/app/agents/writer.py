"""Writer Agent: crafts highly tailored, evidence-grounded application drafts.

Strict Rules:
- Header label: "Draft only - not submitted".
- Uses ONLY facts present in StudentProfile and VerifiedOpportunity evidence.
- Zero invented project names, grades, award names, professors, or universities.
- Structure:
  1. Motivation & Proposed Area of Study
  2. Academic Foundation & Performance
  3. Technical Projects & Competencies (from profile only)
  4. Alignment with Target Programme (from verified evidence snippets)
  5. Future Professional Goals & Closing
"""

import logging
from typing import List, Optional

from pydantic import BaseModel, Field

from app.events import AgentEvent
from app.schemas import ApplicationDraft, Recommendation, StudentProfile, VerifiedOpportunity
from app.tools.llm import llm_client
from app.tools.telemetry import EmitCallback, Telemetry


logger = logging.getLogger(__name__)

HEADER_LABEL = "Draft only - not submitted"


class GeneratedDraftResponse(BaseModel):
    """Structured LLM draft response."""
    text: str = Field(description="Full statement of purpose draft (600-800 words)")
    based_on_profile_fields: List[str] = Field(
        default_factory=list,
        description="List of specific profile fields utilized (e.g. ['degree', 'cgpa', 'projects[0]', 'skills'])",
    )


async def run_writer(
    profile: StudentProfile,
    opportunity: VerifiedOpportunity,
    critic_issues: Optional[List[str]] = None,
    revision_count: int = 0,
    run_id: str = "run-writer",
    emit: EmitCallback = None,
    telemetry: Optional[Telemetry] = None,
) -> ApplicationDraft:
    """Draft or revise a tailored statement of purpose using strictly grounded facts."""
    opp_name = opportunity.name or "Target Programme"
    action_label = f"Revising draft (round {revision_count})" if revision_count > 0 else "Drafting Statement of Purpose"

    if emit:
        await emit(AgentEvent(
            run_id=run_id,
            agent="Writer",
            message=f"{action_label} for '{opp_name}'…",
            level="info",
            payload={"revision": revision_count, "opportunity": opp_name},
        ))

    # Compile Grounded Profile Facts
    projects_text = ""
    for p in profile.projects:
        tech = f" ({', '.join(p.tech)})" if p.tech else ""
        desc = f": {p.description}" if p.description else ""
        projects_text += f"- {p.name}{tech}{desc}\n"

    profile_context = (
        f"STUDENT PROFILE FACTS (USE ONLY THESE, INVENT NOTHING ELSE):\n"
        f"- Full Name: {profile.name or 'Applicant'}\n"
        f"- Degree: {profile.degree or 'Not specified'}\n"
        f"- Target Degree Level: {profile.target_degree_level or 'Master'}\n"
        f"- Field of Study: {profile.field or 'Not specified'}\n"
        f"- CGPA: {profile.cgpa or 'N/A'}/{profile.cgpa_scale or 10.0}\n"
        f"- Skills: {', '.join(profile.skills) or 'Not specified'}\n"
        f"- Projects:\n{projects_text or '  None listed'}\n"
        f"- Achievements: {', '.join(profile.achievements) or 'None listed'}\n"
        f"- Career/Budget Note: {profile.budget_note or 'Modest background seeking merit funding'}\n"
    )

    # Compile Grounded Opportunity Facts
    evidence_snippets = "\n".join(f"  * {e.snippet}" for e in opportunity.evidence[:5])
    opp_context = (
        f"TARGET PROGRAMME / SCHOLARSHIP FACTS (FROM VERIFIED OFFICIAL PAGE):\n"
        f"- Name: {opportunity.name}\n"
        f"- Organisation: {opportunity.organization or 'University'}\n"
        f"- Country: {opportunity.country or 'International'}\n"
        f"- Funding Type: {opportunity.funding_type}\n"
        f"- Verified Evidence Snippets:\n{evidence_snippets or '  General programme page'}\n"
    )

    revision_instructions = ""
    if critic_issues:
        issues_list = "\n".join(f"  ! {iss}" for iss in critic_issues)
        revision_instructions = (
            f"\n\nCRITICAL AUDIT ISSUES TO RESOLVE FROM PREVIOUS ROUND:\n{issues_list}\n"
            f"You MUST fix or remove any claims flagged above. Do NOT repeat ungrounded statements."
        )

    prompt = (
        f"{profile_context}\n"
        f"{opp_context}"
        f"{revision_instructions}\n\n"
        f"TASK:\n"
        f"Write a 600 to 800 word academic Statement of Purpose tailored to '{opp_name}'.\n\n"
        f"STRUCTURE:\n"
        f"1. Clear Motivation & Proposed Study at {opportunity.organization or 'the institution'}\n"
        f"2. Academic Foundation ({profile.degree}, CGPA {profile.cgpa})\n"
        f"3. Concrete Engineering Projects (mention ONLY the student's actual projects: {[p.name for p in profile.projects]})\n"
        f"4. Specific Alignment with Programme (quote or refer strictly to verified page evidence)\n"
        f"5. Long-term Vision & Contribution\n\n"
        f"STRICT RULES:\n"
        f"- Header must start with: [{HEADER_LABEL}]\n"
        f"- Never fabricate professors, lab names, unmentioned research, or unpublished papers.\n"
        f"- Keep the tone professional, scholarly, and authentic."
    )

    system_instruction = (
        "You are an ethical academic scholarship and admissions consultant. "
        "You adhere to strict zero-hallucination standards. "
        "Every single project, technology, skill, and academic qualification must come strictly from the provided student profile facts."
    )

    draft_result: GeneratedDraftResponse
    try:
        draft_result = await llm_client.generate_json(
            prompt=prompt,
            schema=GeneratedDraftResponse,
            system_instruction=system_instruction,
            telemetry=telemetry,
        )
    except Exception as exc:
        logger.error("Writer generation failed: %s", exc)
        if telemetry:
            telemetry.record_failure("writer_agent", error=str(exc), fatal=False)
        # Fallback template
        fallback_text = (
            f"[{HEADER_LABEL}]\n\n"
            f"Statement of Purpose for {opp_name}\n"
            f"Applicant: {profile.name or 'Applicant'}\n\n"
            f"I am writing to express my earnest application for the {opp_name} at {opportunity.organization or 'your university'}. "
            f"Having completed my studies in {profile.degree or 'Engineering'} with a CGPA of {profile.cgpa}/{profile.cgpa_scale or 10.0}, "
            f"I have established a solid academic foundation in {profile.field or 'my field'}.\n\n"
            f"During my academic career, I developed hands-on technical competencies through projects including "
            f"{', '.join(p.name for p in profile.projects[:2]) or 'applied engineering coursework'}. "
            f"My technical focus in {', '.join(profile.skills[:5]) or 'core methodologies'} directly aligns with the rigorous standards of this programme.\n\n"
            f"I look forward to contributing meaningfully to the academic community."
        )
        return ApplicationDraft(
            opportunity_name=opp_name,
            text=fallback_text,
            based_on_profile_fields=["degree", "cgpa", "skills", "projects"],
            revision_count=revision_count,
            label=HEADER_LABEL,
        )

    # Ensure header label is present
    full_text = draft_result.text.strip()
    if not full_text.startswith(f"[{HEADER_LABEL}]"):
        full_text = f"[{HEADER_LABEL}]\n\n{full_text}"

    if emit:
        word_count = len(full_text.split())
        await emit(AgentEvent(
            run_id=run_id,
            agent="Writer",
            message=f"Draft completed ({word_count} words). Backed by profile fields: {', '.join(draft_result.based_on_profile_fields[:4])}",
            level="success",
            payload={"word_count": word_count, "revision_count": revision_count},
        ))

    return ApplicationDraft(
        opportunity_name=opp_name,
        text=full_text,
        based_on_profile_fields=draft_result.based_on_profile_fields,
        critic_notes=[],
        revision_count=revision_count,
        label=HEADER_LABEL,
    )
