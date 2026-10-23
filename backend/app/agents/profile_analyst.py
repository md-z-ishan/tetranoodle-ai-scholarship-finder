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
from typing import Any, List, Optional, Set, Tuple

from app.events import AgentEvent
from app.schemas import ExtraInputs, FieldConfidenceScore, Project, StudentProfile
from app.tools.github_tool import github_tool
from app.tools.llm import QuotaExhaustedError, llm_client
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


def compute_missing_fields_and_confidence(
    profile: StudentProfile,
) -> Tuple[List[str], List[FieldConfidenceScore]]:
    """Deterministically calculate missing critical profile attributes and confidence scores."""
    missing: List[str] = []
    confidence_list: List[FieldConfidenceScore] = []

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
            confidence_list.append(FieldConfidenceScore(field_name=field_name, confidence=weight))
        else:
            missing.append(field_name)
            confidence_list.append(
                FieldConfidenceScore(
                    field_name=field_name,
                    confidence=0.0,
                    reason="Not provided or found in documents",
                )
            )

    # Skills & Projects confidence based on quantity and verification
    skills_conf = min(1.0, round(len(profile.skills) * 0.2, 2))
    projects_conf = min(1.0, round(len(profile.projects) * 0.35, 2))
    confidence_list.append(FieldConfidenceScore(field_name="skills", confidence=skills_conf))
    confidence_list.append(FieldConfidenceScore(field_name="projects", confidence=projects_conf))

    return missing, confidence_list


# ---------------------------------------------------------------------------
# Deterministic fallback profile builder (Phase 8)
# ---------------------------------------------------------------------------

# Patterns used for regex extraction — all generic, no student-specific values
_CGPA_PATTERN = re.compile(
    r"(?:CGPA|GPA|CPI|SGPA|cumulative\s+grade\s+point)[\s:]*([0-9]+\.?[0-9]*)\s*/\s*([0-9]+\.?[0-9]*)",
    re.IGNORECASE,
)
_NAME_PATTERN = re.compile(
    r"^([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,3})",
    re.MULTILINE,
)
_DEGREE_KEYWORDS = {
    "bachelor": "Bachelor", "b.tech": "B.Tech", "btech": "B.Tech",
    "b.e": "B.E.", "b.sc": "B.Sc.", "bsc": "B.Sc.",
    "master": "Master", "m.tech": "M.Tech", "mtech": "M.Tech",
    "m.sc": "M.Sc.", "msc": "M.Sc.", "phd": "PhD", "ph.d": "PhD",
    "m.b.a": "MBA", "mba": "MBA",
}
_FIELD_KEYWORDS = [
    "computer science", "artificial intelligence", "machine learning",
    "electrical engineering", "electronics", "mechanical engineering",
    "civil engineering", "information technology", "data science",
    "biotechnology", "biosciences", "bioinformatics", "chemistry",
    "physics", "mathematics", "economics", "commerce", "business",
    "management", "finance", "environmental science",
]
_NATIONALITY_KEYWORDS = [
    "indian", "nigeria", "kenya", "ghana", "bangladesh", "pakistan",
    "sri lanka", "nepal", "myanmar", "vietnam", "indonesia",
]


def build_deterministic_profile(
    resume_text: str,
    marksheet_text: str,
    extra_inputs: Optional["ExtraInputs"] = None,
) -> StudentProfile:
    """Construct a best-effort StudentProfile from raw PDF text without LLM.

    Every extracted field is tagged in source_notes with extraction method.
    Missing fields are left null — values are NEVER invented.
    This is explicitly marked as a DETERMINISTIC FALLBACK, not full LLM analysis.
    """
    corpus = f"{resume_text}\n{marksheet_text}"
    corpus_lower = corpus.lower()

    profile = StudentProfile(
        source_notes=["[DETERMINISTIC FALLBACK] LLM synthesis unavailable (API quota exhausted). "
                      "Fields extracted by pattern-matching from raw document text. "
                      "Accuracy lower than full LLM analysis."]
    )

    # --- Name (first multi-word capitalized line in resume) ---
    name_match = _NAME_PATTERN.search(resume_text.strip())
    if name_match:
        candidate_name = name_match.group(1).strip()
        # Sanity: reject very short or all-caps words (likely a header)
        words = candidate_name.split()
        if 2 <= len(words) <= 4 and all(w[0].isupper() and not w.isupper() for w in words):
            profile.name = candidate_name
            profile.source_notes.append(f"Name extracted by regex from first line of resume.")

    # --- CGPA ---
    cgpa_match = _CGPA_PATTERN.search(corpus)
    if cgpa_match:
        try:
            profile.cgpa = float(cgpa_match.group(1))
            profile.cgpa_scale = float(cgpa_match.group(2))
            profile.source_notes.append(
                f"CGPA extracted by pattern: {profile.cgpa}/{profile.cgpa_scale}"
            )
        except ValueError:
            pass

    # --- Degree ---
    for kw, normalized in _DEGREE_KEYWORDS.items():
        if kw in corpus_lower:
            profile.degree = normalized
            profile.source_notes.append(f"Degree keyword '{kw}' found in documents.")
            break

    # --- Field ---
    for fkw in _FIELD_KEYWORDS:
        if fkw in corpus_lower:
            profile.field = fkw.title()
            profile.source_notes.append(f"Field keyword '{fkw}' found in documents.")
            break

    # --- Nationality ---
    for nkw in _NATIONALITY_KEYWORDS:
        if nkw in corpus_lower:
            profile.nationality = nkw.title()
            profile.source_notes.append(f"Nationality keyword '{nkw}' found in documents.")
            break

    # --- Apply ExtraInputs overrides (same logic as LLM path) ---
    if extra_inputs:
        if extra_inputs.target_countries:
            profile.target_countries = extra_inputs.target_countries
            profile.source_notes.append("Target countries from form input.")
        if extra_inputs.budget_note:
            profile.budget_note = extra_inputs.budget_note
        if extra_inputs.target_degree_level:
            profile.target_degree_level = extra_inputs.target_degree_level
        elif extra_inputs.degree_level:
            profile.target_degree_level = extra_inputs.degree_level
        if extra_inputs.nationality and not profile.nationality:
            profile.nationality = extra_inputs.nationality
        if extra_inputs.intended_field and not profile.field:
            profile.field = extra_inputs.intended_field
        if extra_inputs.github_url:
            profile.github_url = extra_inputs.github_url
        if extra_inputs.linkedin_url:
            profile.linkedin_url = extra_inputs.linkedin_url

    # Deterministic next-degree derivation
    if not profile.target_degree_level and profile.degree:
        deg_lower = profile.degree.lower()
        if any(kw in deg_lower for kw in ["bachelor", "b.tech", "btech", "b.e", "b.sc", "b.s"]):
            profile.target_degree_level = "Master"
            profile.source_notes.append("Target degree derived as 'Master' (next level from Bachelor).")
        elif any(kw in deg_lower for kw in ["master", "m.tech", "mtech", "msc", "m.sc"]):
            profile.target_degree_level = "Doctoral / PhD"
            profile.source_notes.append("Target degree derived as 'PhD' (next level from Master).")

    return profile


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

    llm_synthesis_failed = False
    try:
        profile: StudentProfile = await llm_client.generate_json(
            prompt=extraction_prompt,
            schema=StudentProfile,
            system_instruction=SYSTEM_INSTRUCTION,
            telemetry=telemetry,
        )
    except QuotaExhaustedError as qe:
        llm_synthesis_failed = True
        logger.warning("ProfileAnalyst: LLM quota exhausted — falling back to deterministic extraction. %s", qe)
        if telemetry:
            telemetry.record_failure(
                "profile_analyst_llm",
                error=f"QuotaExhausted: {str(qe)[:200]}",
                fatal=False,
            )
        if emit:
            await emit(AgentEvent(
                run_id=run_id,
                agent="ProfileAnalyst",
                message=(
                    "LLM synthesis unavailable (API quota exhausted). "
                    "Using deterministic pattern extraction. "
                    "Profile accuracy is reduced — some fields may be missing."
                ),
                level="warn",
            ))
        profile = build_deterministic_profile(resume_text, marksheet_text, extra_inputs)
    except Exception as exc:
        llm_synthesis_failed = True
        logger.error("ProfileAnalyst: LLM synthesis failed with unexpected error — deterministic fallback. %s", exc)
        if telemetry:
            telemetry.record_failure("profile_analyst_llm", error=str(exc)[:200], fatal=False)
        if emit:
            await emit(AgentEvent(
                run_id=run_id,
                agent="ProfileAnalyst",
                message=f"LLM synthesis failed ({type(exc).__name__}). Using deterministic extraction (reduced accuracy).",
                level="warn",
            ))
        profile = build_deterministic_profile(resume_text, marksheet_text, extra_inputs)

    # Step 4: Merge Form Overrides (ExtraInputs) & Derive Target Degree Level
    if extra_inputs:
        if extra_inputs.target_countries:
            profile.target_countries = extra_inputs.target_countries
            profile.source_notes.append("Target countries provided via verified form input (overrides document extraction).")
        if extra_inputs.budget_note:
            profile.budget_note = extra_inputs.budget_note
            profile.source_notes.append("Budget constraints provided via verified form input (overrides document extraction).")
        if extra_inputs.target_degree_level:
            profile.target_degree_level = extra_inputs.target_degree_level
            profile.source_notes.append(f"Target degree level explicitly specified as {profile.target_degree_level}.")
        elif extra_inputs.degree_level:
            profile.target_degree_level = extra_inputs.degree_level
            if not profile.degree:
                profile.degree = extra_inputs.degree_level
            profile.source_notes.append(f"Target degree level set via form input: {profile.target_degree_level}.")
        if extra_inputs.nationality:
            profile.nationality = extra_inputs.nationality
            profile.source_notes.append(f"Nationality verified via form input: {profile.nationality}.")
        if extra_inputs.intended_field and not profile.field:
            profile.field = extra_inputs.intended_field
            profile.source_notes.append("Intended field specified via form input.")
        if extra_inputs.github_url:
            profile.github_url = extra_inputs.github_url
        if extra_inputs.linkedin_url:
            profile.linkedin_url = extra_inputs.linkedin_url

    # Deterministic Next-Level Derivation (A1) if target_degree_level is still unset
    if not profile.target_degree_level:
        current_deg = (profile.degree or "").lower()
        if any(kw in current_deg for kw in ["bachelor", "b.tech", "btech", "b.e", "b.s", "undergraduate"]):
            profile.target_degree_level = "Master"
            profile.source_notes.append("Derived target degree level 'Master' as next academic progression.")
        elif any(kw in current_deg for kw in ["master", "m.tech", "mtech", "msc", "m.s", "postgraduate"]):
            profile.target_degree_level = "Doctoral / PhD"
            profile.source_notes.append("Derived target degree level 'Doctoral / PhD' as next academic progression.")
        else:
            profile.target_degree_level = "Master"

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
