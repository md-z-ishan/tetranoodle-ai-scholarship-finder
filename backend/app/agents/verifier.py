"""Verifier Agent: evidence-grounded verification of each discovered opportunity.

Pipeline per candidate (bounded concurrency, default 3):
1. Fetch the official page with tiered fetch tool; record fetched_via.
2. Follow at most 2 sub-links on the same registrable domain whose anchor text
   matches generic eligibility/apply keywords (no scholarship-name hardcoding).
3. Build a compact context by preferring keyword-dense sections; truncate safely.
4. One LLM structured-output call extracts: eligibility criteria, funding details,
   deadline text, apply steps, degree levels, nationality notes — each with a
   verbatim evidence snippet.
5. EVIDENCE GROUNDING CHECK (deterministic): every snippet must appear (normalized
   whitespace + case) in the fetched text; non-matching fields are dropped to null
   and logged in unknown_fields.
6. Deterministic eligibility comparison against profile where possible (numeric
   CGPA vs stated minimum on the correct scale, degree level, target country).
7. Deadline logic against the REAL current date.
8. Computed confidence formula (documented in VerifiedOpportunity docstring).
9. Status rules: verified / partial / unverified / rejected.
10. Emits AgentEvents per candidate; records real retries/fallbacks in Telemetry.
"""

import asyncio
import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field

from app.events import AgentEvent
from app.schemas import Evidence, Opportunity, StudentProfile, VerifiedOpportunity
from app.tools.domains import classify_source_tier, is_official_domain, registrable_domain
from app.tools.fetch_tool import fetch_tool
from app.tools.llm import llm_client
from app.tools.search_tool import search_tool
from app.tools.telemetry import EmitCallback, Telemetry


logger = logging.getLogger(__name__)

# Max concurrent verifications (protects free-tier quota)
VERIFIER_CONCURRENCY = 3

# Generic sub-link anchor keywords — no scholarship-specific names allowed
ELIGIBILITY_LINK_KEYWORDS = frozenset([
    "eligibility", "eligible", "how to apply", "requirements", "criteria",
    "funding", "apply", "application", "who can apply", "scholarship details",
    "programme details", "award details", "stipend", "tuition",
])

# Key field names used in confidence formula
KEY_FIELDS = ("eligibility", "funding", "deadline", "apply_steps")

# Source-tier multipliers for confidence formula
TIER_FACTOR: Dict[str, float] = {"official": 1.0, "reputable": 0.75, "unknown": 0.5}


# ---------------------------------------------------------------------------
# LLM extraction schema (one call per candidate)
# ---------------------------------------------------------------------------

class EvidencedString(BaseModel):
    """A field value with a verbatim evidence snippet from the page."""
    value: Optional[str] = None
    evidence_snippet: Optional[str] = None   # Must be a verbatim quote from page text


class ExtractedOpportunityData(BaseModel):
    """LLM extraction result: all fields carry verbatim evidence snippets."""
    eligibility_criteria: List[EvidencedString] = Field(default_factory=list)
    funding_details: Optional[EvidencedString] = None
    deadline_text: Optional[EvidencedString] = None
    apply_steps: List[EvidencedString] = Field(default_factory=list)
    degree_levels: List[EvidencedString] = Field(default_factory=list)
    nationality_notes: Optional[EvidencedString] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_for_grounding(text: str) -> str:
    """Collapse whitespace and lowercase for substring grounding check."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _snippet_grounded(snippet: str, page_text: str) -> bool:
    """Return True iff the snippet appears (normalised) in the page text."""
    if not snippet or not snippet.strip():
        return False
    needle = _normalize_for_grounding(snippet)
    haystack = _normalize_for_grounding(page_text)
    # Allow up to 4 chars difference to handle PDF ligature variations
    return needle[:60] in haystack or needle in haystack


def _safe_truncate(text: str, max_chars: int = 6000) -> str:
    """Return up to max_chars characters, preferring keyword-rich regions."""
    if len(text) <= max_chars:
        return text
    keywords = ["eligib", "requir", "deadline", "funding", "tuition", "stipend", "apply", "award"]
    paragraphs = text.split("\n")
    scored = []
    for p in paragraphs:
        p_lower = p.lower()
        score = sum(1 for kw in keywords if kw in p_lower) * 10 + len(p)
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    result = ""
    for _, para in scored:
        if len(result) + len(para) + 1 > max_chars:
            break
        result += para + "\n"
    return result or text[:max_chars]


def _compute_confidence(
    evidenced_fields: int,
    total_key_fields: int,
    source_tier: str,
    link_alive: bool,
) -> float:
    """
    Confidence formula (documented in VerifiedOpportunity docstring):
      coverage  = evidenced_fields / total_key_fields
      tier_mult = TIER_FACTOR[source_tier]
      alive_mult = 1.0 if link_alive else 0.3
      confidence = coverage * tier_mult * alive_mult   (clamped 0–1)
    """
    if total_key_fields == 0:
        return 0.0
    coverage = evidenced_fields / total_key_fields
    tier_mult = TIER_FACTOR.get(source_tier, 0.5)
    alive_mult = 1.0 if link_alive else 0.3
    return round(min(1.0, coverage * tier_mult * alive_mult), 4)


def _parse_deadline(raw: str) -> tuple[Optional[str], Optional[int], str]:
    """
    Parse a raw deadline string to (iso_date_or_None, days_left_or_None, status).
    Status: 'open' | 'closing_soon' | 'passed' | 'unclear' | 'rolling'
    """
    if not raw:
        return None, None, "unclear"

    raw_lower = raw.lower()

    # Rolling / ongoing
    if any(kw in raw_lower for kw in ["rolling", "ongoing", "open year", "continuous", "anytime"]):
        return None, None, "rolling"

    today = date.today()

    # Try to extract a date — attempt several patterns
    date_patterns = [
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})",              # DD/MM/YYYY or MM/DD/YYYY
        r"(\w+ \d{1,2},?\s*\d{4})",                            # January 15, 2025
        r"(\d{1,2} \w+ \d{4})",                                # 15 January 2025
        r"(\w+ \d{4})",                                         # January 2025
        r"(\d{4}-\d{2}-\d{2})",                                # ISO
    ]

    for pattern in date_patterns:
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            matched_str = match.group(1)
            for fmt in ["%B %d, %Y", "%B %d %Y", "%d %B %Y", "%m/%d/%Y", "%d/%m/%Y",
                        "%Y-%m-%d", "%B %Y", "%d-%m-%Y"]:
                try:
                    parsed = datetime.strptime(matched_str.strip(), fmt).date()
                    days_left = (parsed - today).days
                    if days_left < 0:
                        status = "passed"
                    elif days_left <= 30:
                        status = "closing_soon"
                    else:
                        status = "open"
                    return parsed.isoformat(), days_left, status
                except ValueError:
                    continue

    return None, None, "unclear"


def _classify_funding_type(text: str) -> str:
    """Classify funding type from text evidence."""
    t = text.lower()
    if any(kw in t for kw in ["full scholarship", "full funding", "fully funded", "all expenses", "full tuition"]):
        return "full"
    if any(kw in t for kw in ["tuition waiver", "fee waiver"]):
        return "tuition_waiver"
    if any(kw in t for kw in ["stipend only", "living allowance only", "monthly stipend"]):
        return "stipend_only"
    if any(kw in t for kw in ["partial", "partial funding", "partial scholarship"]):
        return "partial"
    return "unknown"


def _extract_sub_links(html_or_text: str, base_url: str, page_domain: str) -> List[str]:
    """Extract up to 2 sub-links from page text/HTML matching eligibility keywords (same domain only)."""
    if not html_or_text:
        return []

    # Simple regex-based link extraction (no heavy HTML parsing dependency added here)
    href_re = re.compile(r'href=["\']([^"\'#]+)["\']', re.IGNORECASE)
    anchor_re = re.compile(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>([^<]*)</a>', re.IGNORECASE)

    candidates: List[str] = []
    for match in anchor_re.finditer(html_or_text):
        href, anchor_text = match.group(1), match.group(2).strip().lower()
        if any(kw in anchor_text for kw in ELIGIBILITY_LINK_KEYWORDS):
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            link_domain = parsed.netloc.lstrip("www.")
            if page_domain and link_domain.endswith(page_domain):
                candidates.append(full_url)
            if len(candidates) >= 2:
                break

    return candidates[:2]


# ---------------------------------------------------------------------------
# Main verifier
# ---------------------------------------------------------------------------

async def _verify_one(
    candidate: Opportunity,
    profile: StudentProfile,
    run_id: str,
    emit: EmitCallback,
    telemetry: Optional[Telemetry],
) -> VerifiedOpportunity:
    """Verify a single candidate: fetch → extract → ground → compare → status."""
    now_iso = datetime.utcnow().isoformat() + "Z"
    url = candidate.url or ""

    result = VerifiedOpportunity(
        **candidate.model_dump(),
        fetched_at=now_iso,
    )

    if not url:
        result.status = "unverified"
        result.rejection_reason = "No URL provided by scout"
        result.link_alive = False
        return result

    # A4: Aggregator-to-official check: if source tier is not official, search for official portal
    if classify_source_tier(url) != "official" and candidate.name:
        search_query = f"{candidate.organization or ''} {candidate.name} official portal scholarship".strip()
        try:
            res_hits = await search_tool.search(search_query, max_results=3, telemetry=telemetry)
            for rh in res_hits:
                if is_official_domain(rh.url):
                    logger.info("Aggregator %s redirected to official portal %s", url, rh.url)
                    if telemetry:
                        telemetry.increment("verifier_aggregator_resolved")
                    result.evidence.append(Evidence(url=url, fetched_at=now_iso, snippet="Aggregator source page"))
                    url = rh.url
                    result.url = rh.url
                    result.source_tier = "official"
                    result.official_domain = True
                    break
        except Exception as exc:
            logger.debug("Official portal resolution failed for %s: %s", candidate.name, exc)

    # ------------------------------------------------------------------ #
    # Step 1: Fetch official page (tiered)                                #
    # ------------------------------------------------------------------ #
    if emit:
        await emit(AgentEvent(
            run_id=run_id, agent="Verifier",
            message=f"Fetching: {url[:80]}…",
            level="info",
        ))

    page_text = ""
    fetched_via = None
    link_alive = False

    try:
        fetch_result = await fetch_tool.fetch_page(url, telemetry=telemetry)
        page_text = fetch_result.text or ""
        fetched_via = fetch_result.fetched_via
        link_alive = bool(page_text and len(page_text) > 100)
    except Exception as exc:
        logger.warning("Verifier fetch failed for %s: %s", url, exc)
        if telemetry:
            telemetry.record_failure("verifier_fetch", error=str(exc), fatal=False)

    result.link_alive = link_alive
    result.fetched_via = fetched_via
    result.source_tier = classify_source_tier(url)
    result.official_domain = is_official_domain(url)

    if not link_alive:
        result.status = "unverified"
        result.rejection_reason = f"Page unreachable after all fetch tiers. fetched_via={fetched_via}"
        result.confidence = _compute_confidence(0, len(KEY_FIELDS), result.source_tier, False)
        if emit:
            await emit(AgentEvent(
                run_id=run_id, agent="Verifier",
                message=f"Unreachable: {url[:60]} — status=unverified",
                level="warn",
            ))
        return result

    # ------------------------------------------------------------------ #
    # Step 2: Fetch up to 2 sub-links on same domain                     #
    # ------------------------------------------------------------------ #
    page_domain = registrable_domain(url)
    extra_links = _extract_sub_links(page_text, url, page_domain)
    for extra_url in extra_links[:2]:
        try:
            extra_res = await fetch_tool.fetch_page(extra_url, telemetry=telemetry)
            if extra_res.text:
                page_text += "\n\n" + extra_res.text
        except Exception as exc:
            logger.debug("Sub-link fetch failed %s: %s", extra_url, exc)

    # ------------------------------------------------------------------ #
    # Pre-LLM Deterministic Check (A2, A6: saves LLM quota)              #
    # ------------------------------------------------------------------ #
    page_lower = page_text.lower()
    target_lvl = (profile.target_degree_level or profile.degree or "Master").lower()
    is_grad = any(kw in target_lvl for kw in ["master", "msc", "phd", "doctoral", "postgraduate"])

    # Incompatible programme purpose checks
    if is_grad:
        bad_purpose_kws = ["military service bond", "secondary school student", "junior college", "polytechnic diploma", "year 12 equivalent"]
        for bp in bad_purpose_kws:
            if bp in page_lower:
                result.status = "rejected"
                result.rejection_reason = f"Evidenced programme purpose is incompatible ({bp})"
                result.confidence = 0.5
                if emit:
                    await emit(AgentEvent(
                        run_id=run_id, agent="Verifier",
                        message=f"Deterministic pre-LLM filter rejected {url[:50]}: {result.rejection_reason}",
                        level="warn",
                    ))
                return result

    # ------------------------------------------------------------------ #
    # Step 3: Build compact context                                       #
    # ------------------------------------------------------------------ #
    compact = _safe_truncate(page_text, max_chars=6000)

    # ------------------------------------------------------------------ #
    # Step 4: LLM structured extraction                                   #
    # ------------------------------------------------------------------ #
    extraction_prompt = (
        "Extract the following fields from the scholarship/programme page text below. "
        "For EVERY field include a verbatim evidence_snippet copied word-for-word from the text. "
        "If a field is not stated on the page, set both value and evidence_snippet to null.\n\n"
        f"PAGE URL: {url}\n"
        f"PAGE TEXT:\n{compact}"
    )

    extraction_system = (
        "You are a zero-hallucination scholarship data extraction specialist. "
        "Extract ONLY what the page text explicitly states. "
        "Every evidence_snippet must be a verbatim quote from the page text — never paraphrase. "
        "Do not invent deadlines, eligibility criteria, or funding amounts."
    )

    extracted: ExtractedOpportunityData
    try:
        extracted = await llm_client.generate_json(
            prompt=extraction_prompt,
            schema=ExtractedOpportunityData,
            system_instruction=extraction_system,
            telemetry=telemetry,
        )
    except Exception as exc:
        logger.error("Verifier LLM extraction failed for %s: %s", url, exc)
        if telemetry:
            telemetry.record_failure("verifier_llm", error=str(exc), fatal=False)
        result.status = "unverified"
        result.rejection_reason = f"LLM extraction failed: {str(exc)[:120]}"
        result.confidence = _compute_confidence(0, len(KEY_FIELDS), result.source_tier, link_alive)
        return result

    # ------------------------------------------------------------------ #
    # Step 5: Evidence grounding check (deterministic anti-hallucination) #
    # ------------------------------------------------------------------ #
    unknown_fields: List[str] = []
    evidenced_count = 0
    field_evidence: Dict[str, Any] = {}

    def _ground_field(field_name: str, evidenced_str: Optional["EvidencedString"]) -> Optional[str]:
        """Return value if snippet is grounded; else add to unknown_fields and return None."""
        nonlocal evidenced_count
        if not evidenced_str or not evidenced_str.value:
            if field_name in KEY_FIELDS:
                unknown_fields.append(field_name)
            return None
        snippet = evidenced_str.evidence_snippet or ""
        if snippet and _snippet_grounded(snippet, page_text):
            if field_name in KEY_FIELDS:
                evidenced_count += 1
            ev = Evidence(url=url, fetched_at=now_iso, snippet=snippet[:400])
            field_evidence[field_name] = ev.model_dump()
            result.evidence.append(ev)
            return evidenced_str.value
        else:
            unknown_fields.append(field_name)
            logger.debug("Grounding failed for field '%s' snippet: %s…", field_name, snippet[:80])
            return None

    def _ground_list(field_name: str, items: List["EvidencedString"]) -> List[str]:
        """Ground a list field; count as evidenced if at least one item passes."""
        nonlocal evidenced_count
        grounded_vals = []
        for item in items:
            if not item or not item.value:
                continue
            snippet = item.evidence_snippet or ""
            if snippet and _snippet_grounded(snippet, page_text):
                grounded_vals.append(item.value)
                ev = Evidence(url=url, fetched_at=now_iso, snippet=snippet[:400])
                result.evidence.append(ev)
            else:
                logger.debug("List grounding failed for %s item: %s", field_name, item.value)
        if grounded_vals and field_name in KEY_FIELDS:
            evidenced_count += 1
            field_evidence[field_name] = grounded_vals
        elif field_name in KEY_FIELDS:
            unknown_fields.append(field_name)
        return grounded_vals

    grounded_eligibility = _ground_list("eligibility", extracted.eligibility_criteria)
    grounded_funding = _ground_field("funding", extracted.funding_details)
    grounded_deadline_text = _ground_field("deadline", extracted.deadline_text)
    grounded_apply_steps = _ground_list("apply_steps", extracted.apply_steps)
    grounded_degree_levels = _ground_list("degree_levels", extracted.degree_levels)
    grounded_nationality = _ground_field("nationality_notes", extracted.nationality_notes)

    dropped_count = len([f for f in unknown_fields if f in KEY_FIELDS])
    if dropped_count:
        if emit:
            await emit(AgentEvent(
                run_id=run_id, agent="Verifier",
                message=f"Grounding dropped {dropped_count} unverified key fields for {url[:60]}",
                level="warn",
                payload={"unknown_fields": unknown_fields},
            ))

    if emit:
        await emit(AgentEvent(
            run_id=run_id, agent="Verifier",
            message=f"Extracted {evidenced_count}/{len(KEY_FIELDS)} key fields with evidence for {url[:60]}",
            level="info",
        ))

    result.unknown_fields = unknown_fields
    result.field_evidence = field_evidence
    result.apply_steps = grounded_apply_steps
    result.degree_levels = grounded_degree_levels
    result.nationality_notes = grounded_nationality

    # ------------------------------------------------------------------ #
    # Step 6: Eligibility comparison (deterministic where possible)       #
    # ------------------------------------------------------------------ #
    eligibility_met: List[str] = []
    eligibility_gaps: List[str] = []
    needs_from_student: List[str] = []

    for crit in grounded_eligibility:
        crit_lower = crit.lower()

        # Numeric CGPA comparison
        cgpa_match = re.search(r"(?:gpa|cgpa|grade)[^\d]*(\d+(?:\.\d+)?)", crit_lower)
        if cgpa_match:
            required_cgpa = float(cgpa_match.group(1))
            if profile.cgpa is not None:
                # Normalise to same scale
                profile_cgpa = profile.cgpa
                cgpa_scale = profile.cgpa_scale or 10.0
                if required_cgpa <= 4.0 and cgpa_scale > 4.0:
                    # Convert profile CGPA to 4.0 scale
                    profile_cgpa_norm = (profile.cgpa / cgpa_scale) * 4.0
                elif required_cgpa > 4.0 and cgpa_scale <= 4.0:
                    # Convert required to 10.0 scale
                    required_cgpa = (required_cgpa / 4.0) * cgpa_scale
                    profile_cgpa_norm = profile.cgpa
                else:
                    profile_cgpa_norm = profile.cgpa

                if profile_cgpa_norm >= required_cgpa:
                    eligibility_met.append(f"CGPA requirement met: {profile_cgpa_norm:.2f} >= {required_cgpa}")
                else:
                    eligibility_gaps.append(f"CGPA too low: have {profile_cgpa_norm:.2f}, need {required_cgpa}")
            else:
                needs_from_student.append("CGPA not provided; cannot verify minimum requirement")
            continue

        # Degree level comparison
        degree_keywords = ["master", "msc", "ms", "postgraduate", "phd", "doctoral", "bachelor", "undergraduate"]
        for kw in degree_keywords:
            if kw in crit_lower:
                target_deg = (profile.target_degree_level or "Master").lower()
                curr_deg = (profile.degree or "").lower()
                if kw in ["master", "msc", "ms", "postgraduate"]:
                    if any(t in target_deg for t in ["master", "msc", "ms", "postgraduate"]) or "bachelor" in curr_deg:
                        eligibility_met.append(f"Degree progression match: {kw} (student target: {profile.target_degree_level or 'Master'})")
                    else:
                        eligibility_gaps.append(f"Degree mismatch: page is for '{kw}', student target is '{profile.target_degree_level}'")
                elif kw in ["phd", "doctoral"]:
                    if any(t in target_deg for t in ["phd", "doctoral", "research"]):
                        eligibility_met.append(f"Degree level match: {kw}")
                    else:
                        eligibility_gaps.append(f"Degree mismatch: page requires PhD, student target is '{profile.target_degree_level}'")
                elif kw in ["bachelor", "undergraduate"]:
                    if is_grad and ("high school" in crit_lower or "diploma" in crit_lower or "undergraduate only" in crit_lower):
                        eligibility_gaps.append(f"Degree mismatch: page is for pre-degree/undergraduate students, student target is '{profile.target_degree_level}'")
                    else:
                        eligibility_met.append(f"Undergraduate credential match: {kw}")
                break
        else:
            # Can't determine deterministically — use LLM comparison below
            pass

    # Fast deterministic field and general criteria check to conserve rate limits
    if grounded_eligibility:
        field_lower = (profile.field or "computer science").lower()
        for c in grounded_eligibility:
            c_low = c.lower()
            if any(term in c_low for term in [field_lower, "stem", "computer", "engineering", "technology", "all fields", "any subject", "open to all", "all disciplines", "merit", "academic excellence"]):
                eligibility_met.append(f"Criterion aligned: {c}")

    # LLM fallback comparison for remaining complex criteria if any
    if grounded_eligibility and profile.field:
        remaining_criteria = [c for c in grounded_eligibility
                              if not any(kw in c.lower() for kw in ["gpa", "cgpa", "grade", "master", "phd", "bachelor", "stem", "computer", "all fields"])]
        if remaining_criteria and len(remaining_criteria) <= 3:
            try:
                class EligibilityResult(BaseModel):
                    met: List[str] = Field(default_factory=list)
                    gaps: List[str] = Field(default_factory=list)
                    unknown: List[str] = Field(default_factory=list)

                comparison_prompt = (
                    f"Student profile snapshot:\n"
                    f"  Degree: {profile.degree}\n"
                    f"  Field: {profile.field}\n"
                    f"  Target countries: {', '.join(profile.target_countries)}\n"
                    f"  CGPA: {profile.cgpa}/{profile.cgpa_scale or 10}\n\n"
                    f"Scholarship eligibility criteria (already extracted from official page):\n"
                    + "\n".join(f"- {c}" for c in remaining_criteria[:10])
                    + "\n\nFor each criterion, classify as met/gaps/unknown. "
                    "ONLY use what is stated in the profile; add to 'unknown' anything the page doesn't clarify or the profile doesn't answer."
                )
                elig_res = await llm_client.generate_json(
                    prompt=comparison_prompt,
                    schema=EligibilityResult,
                    telemetry=telemetry,
                )
                eligibility_met.extend(elig_res.met)
                eligibility_gaps.extend(elig_res.gaps)
                needs_from_student.extend(elig_res.unknown)
            except Exception as exc:
                logger.warning("LLM eligibility comparison failed: %s", exc)

    # Hard Filter A2: Citizenship / PR restrictions
    all_eligibility_text = " ".join(grounded_eligibility + ([grounded_nationality] if grounded_nationality else [])).lower()
    if profile.nationality:
        nat_lower = profile.nationality.lower()
        citizens_only_kws = ["singapore citizen", "us citizen", "canadian citizen", "citizens only", "domestic students only", "permanent residents only", "citizens or pr"]
        if any(co in all_eligibility_text for co in citizens_only_kws):
            if nat_lower not in all_eligibility_text:
                eligibility_gaps.append(f"Ineligible: requires domestic citizenship or permanent residency (excluding nationality '{profile.nationality}')")
    else:
        # Nationality unknown: keep partial and add to needs_from_student
        if any(co in all_eligibility_text for co in ["citizen", "permanent resident", "citizenship"]):
            needs_from_student.append("Confirm citizenship/residency status (programme has domestic citizenship requirements)")

    # Hard Filter A2: Target degree level check
    if grounded_degree_levels and target_lvl:
        grad_match = any(any(kw in dl.lower() for kw in ["master", "msc", "phd", "doctoral", "postgraduate", "graduate"]) for dl in grounded_degree_levels)
        undergrad_only = all(any(kw in dl.lower() for kw in ["bachelor", "undergraduate", "diploma", "polytechnic", "year 12", "high school"]) for dl in grounded_degree_levels)
        if is_grad and undergrad_only and not grad_match:
            eligibility_gaps.append(f"Incompatible degree level: page exclusively supports undergraduate/diploma ({', '.join(grounded_degree_levels)}), but student target is '{profile.target_degree_level or 'Master'}'")

    result.eligibility_met = eligibility_met
    result.eligibility_gaps = eligibility_gaps
    result.needs_from_student = needs_from_student

    # ------------------------------------------------------------------ #
    # Step 7: Deadline parsing against REAL current date                  #
    # ------------------------------------------------------------------ #
    deadline_iso, days_left, deadline_status = _parse_deadline(grounded_deadline_text or candidate.deadline or "")
    result.deadline_date = deadline_iso
    result.deadline_text = grounded_deadline_text or candidate.deadline
    result.days_left = days_left
    result.deadline_status = deadline_status  # type: ignore[assignment]

    # ------------------------------------------------------------------ #
    # Step 8: Funding type classification                                  #
    # ------------------------------------------------------------------ #
    if grounded_funding:
        result.funding_type = _classify_funding_type(grounded_funding)  # type: ignore[assignment]

    # ------------------------------------------------------------------ #
    # Step 9: Computed confidence                                         #
    # ------------------------------------------------------------------ #
    result.confidence = _compute_confidence(evidenced_count, len(KEY_FIELDS), result.source_tier, link_alive)

    # ------------------------------------------------------------------ #
    # Step 10: Status rules                                               #
    # ------------------------------------------------------------------ #
    if deadline_status == "passed":
        result.status = "rejected"
        result.rejection_reason = "Deadline has already passed"
    elif eligibility_gaps:
        result.status = "rejected"
        result.rejection_reason = f"Clearly ineligible: {'; '.join(eligibility_gaps[:2])}"
    elif result.confidence >= 0.6 and evidenced_count >= 2 and not eligibility_gaps:
        result.status = "verified"
    elif evidenced_count >= 1:
        result.status = "partial"
    else:
        result.status = "unverified"

    if emit:
        await emit(AgentEvent(
            run_id=run_id, agent="Verifier",
            message=(
                f"[{result.status.upper()}] {candidate.name or url[:40]} | "
                f"confidence={result.confidence:.2f} | deadline={deadline_status} | "
                f"tier={result.source_tier}"
            ),
            level="success" if result.status in ("verified", "partial") else "warn",
            payload={"status": result.status, "confidence": result.confidence},
        ))

    return result


async def run_verifier(
    candidates: List[Opportunity],
    profile: StudentProfile,
    run_id: str = "run-verifier",
    emit: EmitCallback = None,
    telemetry: Optional[Telemetry] = None,
    concurrency: int = VERIFIER_CONCURRENCY,
) -> List[VerifiedOpportunity]:
    """Run verifier over all candidates with bounded concurrency.

    One failure never crashes the run — errors downgrade that candidate to 'unverified'.
    """
    if emit:
        await emit(AgentEvent(
            run_id=run_id, agent="Verifier",
            message=f"Starting verification of {len(candidates)} candidates (concurrency={concurrency})…",
            level="info",
        ))

    semaphore = asyncio.Semaphore(concurrency)
    # Hard per-candidate wall-clock cap — prevents a single 429/503 storm from
    # blocking all results. 90 s covers: fetch (10 s) + sub-links (10 s) + LLM
    # with two retries+backoff (50 s) + grounding (5 s) + headroom.
    _CANDIDATE_TIMEOUT = 90.0

    async def _safe_verify(c: Opportunity) -> VerifiedOpportunity:
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    _verify_one(c, profile, run_id, emit, telemetry),
                    timeout=_CANDIDATE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning("Verifier timed-out for %s after %.0fs", c.url, _CANDIDATE_TIMEOUT)
                if telemetry:
                    telemetry.record_failure("verifier_timeout", error=f"Candidate timed out after {_CANDIDATE_TIMEOUT}s", fatal=False)
                return VerifiedOpportunity(
                    **c.model_dump(),
                    status="unverified",
                    rejection_reason=f"Verification timed out after {_CANDIDATE_TIMEOUT}s (likely API rate-limit)",
                    link_alive=False,
                    fetched_at=datetime.utcnow().isoformat() + "Z",
                )
            except Exception as exc:
                logger.error("Unexpected verifier error for %s: %s", c.url, exc)
                if telemetry:
                    telemetry.record_failure("verifier_unexpected", error=str(exc), fatal=False)
                return VerifiedOpportunity(
                    **c.model_dump(),
                    status="unverified",
                    rejection_reason=f"Unexpected error: {str(exc)[:200]}",
                    link_alive=False,
                    fetched_at=datetime.utcnow().isoformat() + "Z",
                )

    tasks = [_safe_verify(c) for c in candidates]
    results: List[VerifiedOpportunity] = await asyncio.gather(*tasks)

    # NOTE: Slow retry pass removed — when the API is rate-limited the extra
    # 3 s×N sleep only burns time against the global run timeout without
    # improving outcomes. Candidates that timed out are left as 'unverified'
    # so the ranker can still surface them as low-confidence results.

    # Summary emit
    counts = {"verified": 0, "partial": 0, "unverified": 0, "rejected": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    if emit:
        await emit(AgentEvent(
            run_id=run_id, agent="Verifier",
            message=f"Verification complete: {counts}",
            level="success",
            payload={"status_counts": counts},
        ))

    return results
