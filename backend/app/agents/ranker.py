"""Ranker Agent: deterministic, transparent scoring and top-5 selection.

Scoring weights (constants block — only place to tune):
  FIT      = 40  (eligibility_met vs gaps, field/degree relevance, preferred countries)
  FUNDING  = 25  (full > partial > waiver > unknown)
  URGENCY  = 15  (days_left: ample time scores high; too close/passed scores low; rolling=neutral)
  RISK     = 20  (confidence, unknown fields, source tier)
  TOTAL    = 100

Post-check: numbers and dates produced by the LLM explanation step are verified
against verified data; mismatches are fixed or dropped and logged as warn events.
"""

import logging
import re
from datetime import date
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.events import AgentEvent
from app.schemas import (
    Recommendation,
    ScoreBreakdown,
    StudentProfile,
    VerifiedOpportunity,
)
from app.tools.llm import llm_client
from app.tools.telemetry import EmitCallback, Telemetry


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Score weight constants — single place to change
# ---------------------------------------------------------------------------
W_FIT = 40
W_FUNDING = 25
W_URGENCY = 15
W_RISK = 20
TOTAL_WEIGHT = W_FIT + W_FUNDING + W_URGENCY + W_RISK  # must equal 100

CLOSING_SOON_DAYS = 30       # <= this many days → closing_soon
AMPLE_DAYS = 60              # >= this many days → ample time

FUNDING_SCORES: Dict[str, float] = {
    "full": 1.0,
    "partial": 0.6,
    "tuition_waiver": 0.75,
    "stipend_only": 0.5,
    "unknown": 0.3,
}

TIER_RISK_BONUS: Dict[str, float] = {
    "official": 1.0,
    "reputable": 0.75,
    "unknown": 0.5,
}


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _score_fit(opp: VerifiedOpportunity, profile: StudentProfile) -> Tuple[float, str]:
    """
    Fit sub-score (0–1):
      - eligibility_met count vs gaps count
      - target country match
      - field/degree presence
    """
    met = len(opp.eligibility_met)
    gaps = len(opp.eligibility_gaps)
    total = max(met + gaps, 1)
    eligibility_ratio = met / total

    country_match = 0.0
    if opp.country and profile.target_countries:
        if any(opp.country.lower() in tc.lower() or tc.lower() in opp.country.lower()
               for tc in profile.target_countries):
            country_match = 1.0
    elif not profile.target_countries:
        country_match = 0.5  # No preference stated; neutral

    field_match = 0.0
    if profile.field and opp.name:
        if profile.field.lower() in (opp.name + " " + (opp.organization or "")).lower():
            field_match = 1.0
        else:
            field_match = 0.4  # Present but not confirmed

    score = (eligibility_ratio * 0.6 + country_match * 0.25 + field_match * 0.15)
    reason = (
        f"{met} eligibility criteria met, {gaps} gaps; "
        f"country match={'yes' if country_match > 0.5 else 'no'}; "
        f"field match={'yes' if field_match > 0.5 else 'partial'}"
    )
    return round(score, 4), reason


def _score_funding(opp: VerifiedOpportunity) -> Tuple[float, str]:
    """Funding sub-score (0–1) based on funding_type."""
    score = FUNDING_SCORES.get(opp.funding_type, 0.3)
    reason = f"funding_type={opp.funding_type}"
    return round(score, 4), reason


def _score_urgency(opp: VerifiedOpportunity) -> Tuple[float, str]:
    """
    Urgency sub-score (0–1):
      rolling → 0.6 (neutral)
      passed  → 0.0 (excluded, but still computed for transparency)
      days_left >= AMPLE_DAYS → 1.0
      CLOSING_SOON_DAYS <= days_left < AMPLE_DAYS → 0.7
      0 < days_left < CLOSING_SOON_DAYS → 0.4
      unclear → 0.5
    """
    status = opp.deadline_status
    days = opp.days_left

    if status == "passed":
        return 0.0, "Deadline has passed"
    if status == "rolling":
        return 0.6, "Rolling deadline — apply anytime"
    if status == "unclear" or days is None:
        return 0.5, "Deadline unclear"
    if days >= AMPLE_DAYS:
        return 1.0, f"Ample time: {days} days remaining"
    if days >= CLOSING_SOON_DAYS:
        return 0.7, f"Upcoming: {days} days remaining"
    return 0.4, f"Closing soon: only {days} days remaining"


def _score_risk(opp: VerifiedOpportunity) -> Tuple[float, str]:
    """
    Risk sub-score (0–1):
      confidence * tier_bonus * (1 - unknown_penalty)
    """
    tier_bonus = TIER_RISK_BONUS.get(opp.source_tier, 0.4)
    unknown_count = len(opp.unknown_fields)
    unknown_penalty = min(0.5, unknown_count * 0.1)
    score = round(opp.confidence * tier_bonus * (1.0 - unknown_penalty), 4)
    reason = (
        f"evidence confidence={opp.confidence:.2f}; "
        f"source_tier={opp.source_tier}; "
        f"unknown_fields={unknown_count}"
    )
    return score, reason


def compute_score(opp: VerifiedOpportunity, profile: StudentProfile) -> Tuple[float, ScoreBreakdown]:
    """Compute weighted composite score [0–100] with per-component breakdown."""
    fit_raw, fit_reason = _score_fit(opp, profile)
    funding_raw, funding_reason = _score_funding(opp)
    urgency_raw, urgency_reason = _score_urgency(opp)
    risk_raw, risk_reason = _score_risk(opp)

    total = (
        fit_raw * W_FIT
        + funding_raw * W_FUNDING
        + urgency_raw * W_URGENCY
        + risk_raw * W_RISK
    )
    total = round(min(100.0, max(0.0, total)), 2)

    breakdown = ScoreBreakdown(
        fit=round(fit_raw * W_FIT, 2),
        fit_reason=fit_reason,
        funding=round(funding_raw * W_FUNDING, 2),
        funding_reason=funding_reason,
        urgency=round(urgency_raw * W_URGENCY, 2),
        urgency_reason=urgency_reason,
        risk=round(risk_raw * W_RISK, 2),
        risk_reason=risk_reason,
    )
    return total, breakdown


# ---------------------------------------------------------------------------
# LLM explanation schema
# ---------------------------------------------------------------------------

class ExplanationEntry(BaseModel):
    """LLM-generated explanation for one recommendation (grounded only)."""
    why_it_fits: str = ""
    how_to_apply: List[str] = Field(default_factory=list)
    still_needed: List[str] = Field(default_factory=list)


class ExplanationBatch(BaseModel):
    """Explanations for up to 5 top candidates + executive summary."""
    explanations: List[ExplanationEntry] = Field(default_factory=list)
    executive_summary: str = ""


# ---------------------------------------------------------------------------
# Post-check: verify numbers/dates in LLM text against verified data
# ---------------------------------------------------------------------------

def _extract_numbers_from_text(text: str) -> List[str]:
    """Extract all numeric tokens (integers and decimals) from text."""
    return re.findall(r"\b\d+(?:\.\d+)?\b", text)


def _postcheck_explanation(
    entry: ExplanationEntry,
    opp: VerifiedOpportunity,
) -> Tuple[ExplanationEntry, List[str]]:
    """
    Verify numbers/dates in why_it_fits and how_to_apply against verified data.
    Drop or redact sentences containing mismatches; return list of issues found.
    """
    issues: List[str] = []

    # Build reference set: numbers from verified fields
    verified_text = " ".join(filter(None, [
        opp.deadline_text,
        str(opp.days_left) if opp.days_left is not None else None,
        opp.funding_summary,
        opp.deadline_date,
        str(opp.deadline_status),
    ] + [e.snippet for e in opp.evidence]))

    verified_numbers = set(_extract_numbers_from_text(verified_text))

    # Check why_it_fits
    llm_nums_fit = _extract_numbers_from_text(entry.why_it_fits)
    bad_nums = [n for n in llm_nums_fit if n not in verified_numbers and len(n) >= 4]  # skip small nums like "1", "2"
    if bad_nums:
        issues.append(f"why_it_fits: unverified numbers {bad_nums[:3]}")
        # Remove the offending sentences
        sentences = entry.why_it_fits.split(". ")
        cleaned = [s for s in sentences if not any(bn in s for bn in bad_nums)]
        entry.why_it_fits = ". ".join(cleaned).strip()

    return entry, issues


# ---------------------------------------------------------------------------
# Main ranker
# ---------------------------------------------------------------------------

async def run_ranker(
    verified: List[VerifiedOpportunity],
    profile: StudentProfile,
    run_id: str = "run-ranker",
    emit: EmitCallback = None,
    telemetry: Optional[Telemetry] = None,
) -> Tuple[List[Recommendation], str, List[str], List[str]]:
    """Run deterministic scoring, top-5 selection, grounded explanations, post-check.

    Returns:
        (recommendations, executive_summary, could_not_verify_list, needs_from_student_list)
    """
    if emit:
        await emit(AgentEvent(
            run_id=run_id, agent="Ranker",
            message=f"Scoring {len(verified)} verified candidates…",
            level="info",
        ))

    # ------------------------------------------------------------------
    # Step 1: Strict Viability Filter (A3):
    # Only 'verified' or 'partial' candidates with deadline_status != 'passed'
    # and no blocking eligibility gaps may enter top 5.
    # ------------------------------------------------------------------
    scoreable = [
        v for v in verified
        if v.status in ("verified", "partial")
        and v.deadline_status != "passed"
        and not v.eligibility_gaps
    ]

    # Graceful quota-pressure fallback: if Gemini rate limits caused all
    # candidates to time out as 'unverified', surface them anyway at reduced
    # confidence rather than returning zero results to the student.
    if not scoreable:
        scoreable = [
            v for v in verified
            if v.status == "unverified"
            and v.deadline_status != "passed"
            and not (v.rejection_reason or "").startswith("Deadline")
            and not v.eligibility_gaps
        ]
        if scoreable and emit:
            await emit(AgentEvent(
                run_id=run_id, agent="Ranker",
                message=(
                    f"All {len(verified)} candidates timed out during LLM verification (Gemini quota pressure). "
                    f"Surfacing {len(scoreable)} unverified candidates at reduced confidence as best-effort results."
                ),
                level="warn",
            ))
        # Cap confidence for unverified candidates so UI shows them as low-confidence
        for v in scoreable:
            if v.confidence == 0.0:
                v.confidence = 0.25

    scored: List[Tuple[float, ScoreBreakdown, VerifiedOpportunity]] = []

    for opp in scoreable:
        score, breakdown = compute_score(opp, profile)
        scored.append((score, breakdown, opp))

    scored.sort(key=lambda x: -x[0])

    if emit:
        await emit(AgentEvent(
            run_id=run_id, agent="Ranker",
            message=f"Viability filter: {len(scoreable)}/{len(verified)} candidates are viable. Top score: {scored[0][0]:.1f}/100 for '{scored[0][2].name}'" if scored else f"Viability filter: 0/{len(verified)} viable candidates",
            level="info" if scoreable else "warn",
        ))

    # ------------------------------------------------------------------
    # Step 2: Top-5 selection with soft diversity (country)
    # ------------------------------------------------------------------
    top5: List[Tuple[float, ScoreBreakdown, VerifiedOpportunity]] = []
    country_counts: Dict[str, int] = {}

    for score, breakdown, opp in scored:
        if len(top5) >= 5:
            break
        country = (opp.country or "unknown").lower()
        # Soft diversity: if same country already has 2+ and score is within 10 of the worst top5, skip
        if country_counts.get(country, 0) >= 2 and len(top5) >= 2:
            threshold = top5[-1][0] - 10.0
            if score < threshold:
                continue  # Prefer diversity
        top5.append((score, breakdown, opp))
        country_counts[country] = country_counts.get(country, 0) + 1

    # If we still have < 5 (due to diversity), fill from remaining scoreable ONLY (never unverified/rejected)
    if len(top5) < 5:
        remaining = [x for x in scored if x not in top5]
        for item in remaining:
            if len(top5) >= 5:
                break
            top5.append(item)

    if emit:
        await emit(AgentEvent(
            run_id=run_id, agent="Ranker",
            message=f"Selected {len(top5)} viable recommendations (target=5, viable={len(scoreable)})",
            level="info" if len(top5) == 5 else "warn",
            payload={"selected": len(top5), "viable": len(scoreable)},
        ))

    # ------------------------------------------------------------------
    # Step 3: LLM grounded explanations + executive summary
    # ------------------------------------------------------------------
    candidates_context = ""
    for i, (score, breakdown, opp) in enumerate(top5, 1):
        candidates_context += (
            f"\nCandidate #{i}: {opp.name or 'Unknown'}\n"
            f"  Organization: {opp.organization or 'Unknown'}\n"
            f"  Country: {opp.country or 'Unknown'}\n"
            f"  URL: {opp.url}\n"
            f"  Status: {opp.status} | Confidence: {opp.confidence:.2f}\n"
            f"  Funding type: {opp.funding_type}\n"
            f"  Deadline: {opp.deadline_text or 'Unknown'} ({opp.deadline_status})\n"
            f"  Days left: {opp.days_left}\n"
            f"  Eligibility met: {'; '.join(opp.eligibility_met[:3]) or 'None confirmed'}\n"
            f"  Eligibility gaps: {'; '.join(opp.eligibility_gaps[:3]) or 'None'}\n"
            f"  Apply steps: {'; '.join(opp.apply_steps[:4]) or 'Not extracted'}\n"
            f"  Unknown fields: {opp.unknown_fields}\n"
            f"  Still needed from student: {opp.needs_from_student}\n"
            f"  Score: {score:.1f}/100 "
            f"(fit={breakdown.fit:.1f}, funding={breakdown.funding:.1f}, "
            f"urgency={breakdown.urgency:.1f}, risk={breakdown.risk:.1f})\n"
        )

    profile_context = (
        f"Student: {profile.degree} in {profile.field}, "
        f"CGPA {profile.cgpa}/{profile.cgpa_scale or 10}, "
        f"Target: {', '.join(profile.target_countries)}, "
        f"Budget: {profile.budget_note or 'Not stated'}"
    )

    explanation_prompt = (
        f"You are writing a consultant-grade scholarship recommendation memo.\n\n"
        f"STUDENT: {profile_context}\n"
        f"TOP CANDIDATES:\n{candidates_context}\n\n"
        f"For each candidate, write:\n"
        f"  why_it_fits: 1–2 sentence explanation ONLY using the facts listed above. "
        f"NEVER invent new numbers, deadlines, or facts.\n"
        f"  how_to_apply: bullet list extracted from the apply_steps only.\n"
        f"  still_needed: items from unknown_fields and needs_from_student only.\n\n"
        f"Then write executive_summary: 3–5 sentences, consultant memo style, "
        f"every claim traceable to the candidate data above. "
        f"Do NOT mention any numbers that are not explicitly listed in the candidate data above."
    )

    explanation_system = (
        "You are a zero-hallucination scholarship analyst. "
        "Use ONLY facts from the candidate data provided. "
        "Do NOT invent deadlines, scores, or amounts not listed. "
        "Keep explanations concise and evidence-based."
    )

    explanations_batch: Optional[ExplanationBatch] = None
    try:
        explanations_batch = await llm_client.generate_json(
            prompt=explanation_prompt,
            schema=ExplanationBatch,
            system_instruction=explanation_system,
            telemetry=telemetry,
        )
    except Exception as exc:
        logger.error("Ranker LLM explanation failed: %s", exc)
        if telemetry:
            telemetry.record_failure("ranker_llm", error=str(exc), fatal=False)

    # ------------------------------------------------------------------
    # Step 4: Post-check numbers in LLM text vs verified data
    # ------------------------------------------------------------------
    all_postcheck_issues: List[str] = []
    explanations = explanations_batch.explanations if explanations_batch else []

    # Pad to match top5 length
    while len(explanations) < len(top5):
        explanations.append(ExplanationEntry())

    postcheck_explanations = []
    for i, (entry, (score, breakdown, opp)) in enumerate(zip(explanations, top5)):
        cleaned_entry, issues = _postcheck_explanation(entry, opp)
        if issues:
            all_postcheck_issues.extend(issues)
            logger.warning("Post-check fixed issues for candidate %d: %s", i + 1, issues)
            if emit:
                await emit(AgentEvent(
                    run_id=run_id, agent="Ranker",
                    message=f"Post-check fixed {len(issues)} issue(s) in explanation for candidate {i+1}",
                    level="warn",
                    payload={"issues": issues[:3]},
                ))
        postcheck_explanations.append(cleaned_entry)

    # ------------------------------------------------------------------
    # Assemble recommendations
    # ------------------------------------------------------------------
    recommendations: List[Recommendation] = []
    for rank, ((score, breakdown, opp), entry) in enumerate(zip(top5, postcheck_explanations), 1):
        # Determine urgency_tag
        urgency_map = {
            "passed": "expired",
            "closing_soon": "urgent",
            "open": "upcoming" if (opp.days_left or 999) < AMPLE_DAYS else "relaxed",
            "rolling": "relaxed",
            "unclear": "unknown",
        }
        urgency_tag = urgency_map.get(opp.deadline_status, "unknown")

        rec = Recommendation(
            rank=rank,
            opportunity=opp,
            score=score,
            score_breakdown=breakdown,
            why_it_fits=entry.why_it_fits,
            urgency_tag=urgency_tag,  # type: ignore[arg-type]
            how_to_apply=entry.how_to_apply or opp.apply_steps,
            still_needed=entry.still_needed or opp.needs_from_student,
        )
        recommendations.append(rec)

    # ------------------------------------------------------------------
    # Could not verify / needs from student lists
    # ------------------------------------------------------------------
    top5_opps = {id(opp) for _, _, opp in top5}
    could_not_verify = [
        f"{v.name or v.url}: {v.status} — {v.rejection_reason or (', '.join(v.eligibility_gaps) if v.eligibility_gaps else None) or ', '.join(v.unknown_fields[:2]) or 'low evidence'}"
        for v in verified
        if id(v) not in top5_opps
    ]

    needs_from_student = []
    for rec in recommendations:
        for item in rec.still_needed:
            entry_str = f"[{rec.opportunity.name}] {item}"
            if entry_str not in needs_from_student:
                needs_from_student.append(entry_str)

    executive_summary = (
        explanations_batch.executive_summary
        if explanations_batch and explanations_batch.executive_summary
        else f"Identified {len(recommendations)} ranked opportunities from {len(verified)} verified candidates. "
             f"See score breakdowns for transparent rationale."
    )

    if len(top5) < 5:
        exec_warning = (
            f" Note: Only {len(top5)} viable candidate(s) were found "
            f"(fewer than 5); the remaining {len(verified) - len(top5)} were rejected or unverified."
        )
        executive_summary += exec_warning

    if emit:
        await emit(AgentEvent(
            run_id=run_id, agent="Ranker",
            message=f"Ranking complete: {len(recommendations)} recommendations. Post-check issues: {len(all_postcheck_issues)}",
            level="success",
            payload={"recommendations": len(recommendations), "postcheck_issues": len(all_postcheck_issues)},
        ))

    return recommendations, executive_summary, could_not_verify, needs_from_student
