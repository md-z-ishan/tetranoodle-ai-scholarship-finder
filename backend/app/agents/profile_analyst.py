"""Profile Analyst Agent for student document parsing and profile synthesis.

Responsibilities:
1. Extract text from resume and marksheet PDFs (pdf_tool); warn if scanned.
2. Enrich with public developer profile from GitHub (github_tool).
3. LLM structured extraction (generate_json with StudentProfile) using strict prompt:
   - Extract ONLY facts explicitly stated in sources.
   - Never infer CGPA, target countries, budget, or achievements.
4. Merge form inputs from ExtraInputs (form values override guesses with audit in source_notes).
5. Grounding check: validates every skill, project, and achievement against source texts;
   prunes hallucinations with logged warning events.
6. Deterministic missing_info computation and field_confidence scoring.
7. Emits granular AgentEvents for live UI telemetry streaming.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from app.events import AgentEvent
from app.schemas import ExtraInputs, Project, StudentProfile
from app.tools.github_tool import github_tool
from app.tools.llm import llm_client
from app.tools.pdf_tool import pdf_tool
from app.tools.telemetry import EmitCallback, Telemetry


logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """You are a meticulous, zero-hallucination Profile Analyst for an executive scholarship matching system.
Your job is to read raw student documents (Resume, Academic Marksheet/Transcript) and GitHub developer profiles to extract a strictly grounded StudentProfile.

CRITICAL RULES:
1. Extract ONLY facts explicitly stated in the source texts.
2. NEVER guess, assume, or fabricate any detail (CGPA, target countries, budget, graduation year, or achievements).
3. If an attribute is not explicitly stated in the source text, leave it null or an empty list.
4. If there is a contradiction between documents (e.g. CGPA on resume differs from transcript), note the discrepancy in `source_notes`.
5. For CGPA, capture both `cgpa` and `cgpa_scale` (e.g. 8.2 out of 10.0).
6. List critical gaps explicitly in `missing_info`.
"""


def normalize_tokens(text: str) -> Set[str]:
    """Convert text into set of alphanumeric lowercase tokens for grounding comparison."""
    if not text:
        return set()
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", text.lower())
    return {token for token in cleaned.split() if len(token) > 2}


def is_grounded(item_text: str, source_corpus: str, source_tokens: Set[str], min_overlap: float = 0.5) -> bool:
    """Check whether an item is truthfully supported by the raw text corpus."""
    if not item_text or not item_text.strip():
        return False

    item_lower = item_text.strip().lower()
    corpus_lower = source_corpus.lower()

    # Exact substring match
    if item_lower in corpus_lower:
        return True

    # Token overlap match for longer phrases/descriptions
    item_tokens = normalize_tokens(item_text)
    if not item_tokens:
        return False

    overlap = len(item_tokens.intersection(source_tokens)) / len(item_tokens)
    return overlap >= min_overlap


def perform_grounding_audit(
    profile: StudentProfile,
    source_corpus: str,
) -> Tuple[StudentProfile, List[str]]:
    """Strictly verify every skill, project, and achievement against source texts.

    Prunes ungrounded hallucinations and returns audit logs.
    """
    removed_items: List[str] = []
    source_tokens = normalize_tokens(source_corpus)

    # 1. Audit Skills
    grounded_skills: List[str] = []
    for skill in profile.skills:
        if is_grounded(skill, source_corpus, source_tokens, min_overlap=0.6):
            grounded_skills.append(skill)
        else:
            removed_items.append(f"Skill: '{skill}'")

    # 2. Audit Projects
    grounded_projects: List[Project] = []
    for proj in profile.projects:
        # Check project name
        if is_grounded(proj.name, source_corpus, source_tokens, min_overlap=0.4):
            # Also audit project technologies
            valid_tech = [t for t in proj.tech if is_grounded(t, source_corpus, source_tokens, min_overlap=0.5)]
            proj.tech = valid_tech if valid_tech else proj.tech
            grounded_projects.append(proj)
        else:
            removed_items.append(f"Project: '{proj.name}'")

    # 3. Audit Achievements
    grounded_achievements: List[str] = []
    for ach in profile.achievements:
        if is_grounded(ach, source_corpus, source_tokens, min_overlap=0.5):
            grounded_achievements.append(ach)
        else:
            removed_items.append(f"Achievement: '{ach}'")

    profile.skills = grounded_skills
    profile.projects = grounded_projects
    profile.achievements = grounded_achievements

    return profile, removed_items


def compute_missing_fields_and_confidence(profile: StudentProfile) -> Tuple[List[str], Dict[str, float]]:
    """Deterministically calculate missing critical profile attributes and confidence scores."""
    missing: List[str] = []
    confidence: Dict[str, float] = {}

    # Critical fields for international scholarship matching
    checks = [
        ("degree", profile.degree, 1.0),
        ("field", profile.field, 1.0),
        ("cgpa", profile.cgpa, 1.0),
        ("target_countries", bool(profile.target_countries), 0.9),
        ("budget_note", profile.budget_note, 0.7),
        ("graduation_year", profile.graduation_year, 0.8),
    ]

    for field_name, value, weight in checks:
        if value:
            confidence[field_name] = weight
        else:
            missing.append(field_name)
            confidence[field_name] = 0.0

    # Skills & Projects confidence based on quantity and verification
    confidence["skills"] = min(1.0, round(len(profile.skills) * 0.2, 2))
    confidence["projects"] = min(1.0, round(len(profile.projects) * 0.35, 2))

    return missing, confidence


async def run_profile_analyst(
    resume_source: Optional[Any] = None,
    marksheet_source: Optional[Any] = None,
    extra_inputs: Optional[ExtraInputs] = None,
    run_id: str = "run-profile",
    emit: EmitCallback = None,
    telemetry: Optional[Telemetry] = None,
) -> StudentProfile:
    """Execute end-to-end Profile Analyst pipeline with multi-source synthesis & grounding."""
    if emit:
        await emit(AgentEvent(run_id=run_id, agent="ProfileAnalyst", message="Initializing student document analysis...", level="info"))

    # Step 1: PDF Extraction (Resume + Marksheet)
    resume_text = ""
    marksheet_text = ""

    if resume_source:
        if emit:
            await emit(AgentEvent(run_id=run_id, agent="ProfileAnalyst", message="Parsing student resume PDF...", level="info"))
        res = pdf_tool.extract_pdf_text(resume_source, telemetry=telemetry)
        resume_text = res.text
        if res.likely_scanned and emit:
            await emit(AgentEvent(
                run_id=run_id,
                agent="ProfileAnalyst",
                message="Resume appears to be a scanned document with limited machine-readable text.",
                level="warn",
            ))

    if marksheet_source:
        if emit:
            await emit(AgentEvent(run_id=run_id, agent="ProfileAnalyst", message="Parsing academic marksheet PDF...", level="info"))
        res = pdf_tool.extract_pdf_text(marksheet_source, telemetry=telemetry)
        marksheet_text = res.text
        if res.likely_scanned and emit:
            await emit(AgentEvent(
                run_id=run_id,
                agent="ProfileAnalyst",
                message="Marksheet appears to be scanned or image-only. Processing available characters.",
                level="warn",
            ))

    # Step 2: GitHub Developer Profile Enrichment
    github_text = ""
    github_url = extra_inputs.github_url if extra_inputs else None
    if github_url:
        if emit:
            await emit(AgentEvent(run_id=run_id, agent="ProfileAnalyst", message=f"Enriching profile from GitHub: {github_url}", level="info"))
        gh_profile = await github_tool.fetch_github_profile(github_url, telemetry=telemetry)
        if gh_profile.error:
            if emit:
                await emit(AgentEvent(
                    run_id=run_id,
                    agent="ProfileAnalyst",
                    message=f"GitHub enrichment notice: {gh_profile.error}",
                    level="warn",
                ))
        else:
            repo_summaries = []
            for r in gh_profile.repos[:4]:
                desc = f"- Repo {r.name} ({r.language or 'Unknown'}, {r.stars} stars): {r.description or ''}"
                if r.readme_excerpt:
                    desc += f"\n  README excerpt: {r.readme_excerpt[:300]}"
                repo_summaries.append(desc)

            github_text = (
                f"GitHub User: {gh_profile.username} ({gh_profile.name or ''})\n"
                f"Bio: {gh_profile.bio or ''}\n"
                f"Top Languages: {', '.join(gh_profile.top_languages)}\n"
                f"Repositories:\n" + "\n".join(repo_summaries)
            )
            if emit:
                await emit(AgentEvent(
                    run_id=run_id,
                    agent="ProfileAnalyst",
                    message=f"Enriched with {len(gh_profile.repos)} public repositories and languages: {', '.join(gh_profile.top_languages[:3])}",
                    level="success",
                ))

    # Step 3: LLM Structured Profile Extraction
    combined_corpus = (
        f"--- RESUME TEXT ---\n{resume_text}\n\n"
        f"--- MARKSHEET TEXT ---\n{marksheet_text}\n\n"
        f"--- GITHUB PROFILE ---\n{github_text}\n"
    )

    if emit:
        await emit(AgentEvent(run_id=run_id, agent="ProfileAnalyst", message="Synthesizing structured student profile via LLM...", level="info"))

    extraction_prompt = (
        "Extract the student's normalized academic and technical profile from the following sources.\n"
        "Remember: Output strictly grounded facts. Do not invent any unmentioned numbers, degrees, or countries.\n\n"
        f"{combined_corpus}"
    )

    profile: StudentProfile = await llm_client.generate_json(
        prompt=extraction_prompt,
        schema=StudentProfile,
        system_instruction=SYSTEM_INSTRUCTION,
        telemetry=telemetry,
    )

    # Step 4: Merge Form Overrides (ExtraInputs)
    if extra_inputs:
        if extra_inputs.target_countries:
            profile.target_countries = extra_inputs.target_countries
            profile.source_notes.append("Target countries provided via verified form input.")
        if extra_inputs.budget_note:
            profile.budget_note = extra_inputs.budget_note
            profile.source_notes.append("Budget constraints provided via verified form input.")
        if extra_inputs.degree_level and not profile.degree:
            profile.degree = extra_inputs.degree_level
            profile.source_notes.append("Intended degree level specified via form input.")
        if extra_inputs.intended_field and not profile.field:
            profile.field = extra_inputs.intended_field
        if extra_inputs.github_url:
            profile.github_url = extra_inputs.github_url
        if extra_inputs.linkedin_url:
            profile.linkedin_url = extra_inputs.linkedin_url

    # Step 5: Grounding Check (Anti-Hallucination Gate)
    profile, pruned_items = perform_grounding_audit(profile, combined_corpus)
    if pruned_items:
        logger.warning("Grounding check pruned unverified items: %s", pruned_items)
        if emit:
            await emit(AgentEvent(
                run_id=run_id,
                agent="ProfileAnalyst",
                message=f"Grounding verification pruned {len(pruned_items)} hallucinated/unsupported items: {', '.join(pruned_items[:3])}",
                level="warn",
            ))

    # Step 6: Deterministic Missing Info & Field Confidence
    missing_fields, conf_scores = compute_missing_fields_and_confidence(profile)
    profile.missing_info = missing_fields
    profile.field_confidence = conf_scores

    if emit:
        status_msg = (
            f"Profile ready: {profile.degree or 'Degree unknown'}, CGPA {profile.cgpa or 'N/A'}, "
            f"{len(profile.skills)} skills, {len(profile.projects)} projects. "
            f"Missing fields: {len(profile.missing_info)}"
        )
        await emit(AgentEvent(run_id=run_id, agent="ProfileAnalyst", message=status_msg, level="success"))

    return profile
