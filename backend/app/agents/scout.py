"""Scout Agent for intelligent opportunity discovery across live search engines.

Responsibilities:
1. Query Planner: Generates 8-14 targeted search queries strictly FROM the student profile:
   - Coverage: at least 2 queries per target country if specified.
   - If no countries specified, plans broad international queries and records warning.
   - Prohibits hallucinating scholarship names; targets official portals, government programs, and accredited universities.
2. Concurrent Search Execution: Runs planned queries using `search_tool` (Tavily with DDG fallback).
3. Aggregator Resolution: For aggregator hits naming specific programs, searches for the official portal.
4. Triage: Uses LLM to extract Opportunity objects strictly from returned snippets without imagination.
5. Second-Pass Recovery: If fewer than 10 candidates are found, broadens query angles to reach target.
6. Emits telemetry and granular AgentEvents for live UI observability.
"""

import asyncio
import logging
from typing import List, Optional, Set
from pydantic import BaseModel, Field
from app.events import AgentEvent
from app.schemas import Opportunity, StudentProfile
from app.tools.domains import classify_source_tier, is_official_domain
from app.tools.llm import llm_client
from app.tools.search_tool import SearchHit, normalize_url_key, search_tool
from app.tools.telemetry import EmitCallback, Telemetry


logger = logging.getLogger(__name__)

# Search execution concurrency semaphore
SCOUT_SEARCH_SEMAPHORE = asyncio.Semaphore(4)


class SearchQueryPlan(BaseModel):
    """Structured query with explicit search intent generated from the student profile."""
    query: str
    target_country: Optional[str] = None
    funding_focus: Optional[str] = None
    official_body_focus: bool = True
    intent: str


class QueryPlanList(BaseModel):
    """Collection of planned queries adhering to the coverage rules."""
    queries: List[SearchQueryPlan] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class CandidateTriageList(BaseModel):
    """Triaged candidates extracted strictly from live search hits."""
    candidates: List[Opportunity] = Field(default_factory=list)


PLAN_SYSTEM_PROMPT = """You are an expert Query Planner for an international scholarship research agent.
Your objective is to generate 8 to 14 highly effective, diverse search queries to discover REAL university scholarships and funded programs matching the student's academic profile.

MANDATORY RULES:
1. Formulate queries based ONLY on the student's degree level, academic field, target countries, and budget constraints.
2. If target countries are provided, generate AT LEAST 2 targeted queries per country.
3. If no target countries are provided, generate broad international queries and add a warning.
4. Prefer queries targeting official bodies (e.g. government scholarships, universities, .edu, .gov, DAAD, Erasmus, Vanier, Chevening).
5. NEVER invent or name a scholarship unless it is an established international public program relevant to the country (like DAAD in Germany, Vanier in Canada).
6. Do NOT search for generic blogs or listicles; append keywords like 'official portal', 'eligibility', 'funding', 'application'.
"""

TRIAGE_SYSTEM_PROMPT = """You are a strict data extraction specialist.
Convert the provided live search hits into structured Opportunity records.

STRICT TRUTHFULNESS RULES:
1. Extract name, organization, country, url, funding_summary, and deadline ONLY from the provided title, URL, and snippet text.
2. If the deadline or funding details are not mentioned in the snippet, set them strictly to null. NEVER guess.
3. Drop obvious spam, news articles, forums (reddit/quora), or unrelated commercial sites.
"""


async def plan_queries(
    profile: StudentProfile,
    telemetry: Optional[Telemetry] = None,
) -> QueryPlanList:
    """Generate diverse, profile-grounded search queries with country coverage validation."""
    countries_str = ", ".join(profile.target_countries) if profile.target_countries else "None specified"
    skills_str = ", ".join(profile.skills[:6]) if profile.skills else "General"
    target_level = profile.target_degree_level or "Master"

    plan_prompt = (
        f"Student Profile:\n"
        f"- Current Degree: {profile.degree or 'Undergraduate'}\n"
        f"- TARGET DEGREE LEVEL (MANDATORY in all queries): {target_level}\n"
        f"- Field of Study: {profile.field or 'Computer Science / Engineering'}\n"
        f"- Target Countries: {countries_str}\n"
        f"- Academic Standing / CGPA: {profile.cgpa or 'Not specified'}\n"
        f"- Key Skills / Focus: {skills_str}\n"
        f"- Budget Constraints: {profile.budget_note or 'Seeking full/substantial funding'}\n\n"
        f"Generate 8 to 14 diverse search queries following the coverage rules. "
        f"CRITICAL: Every query MUST explicitly target the student's next level '{target_level}' "
        f"(e.g., do NOT search for secondary school or polytechnic diplomas when target is Master's)."
    )

    try:
        plan_res: QueryPlanList = await llm_client.generate_json(
            prompt=plan_prompt,
            schema=QueryPlanList,
            system_instruction=PLAN_SYSTEM_PROMPT,
            telemetry=telemetry,
        )
    except Exception as exc:
        logger.warning("Scout plan_queries LLM unavailable (%s); using deterministic query planning.", exc)
        if telemetry:
            telemetry.record_failure("scout_plan_llm", error=str(exc)[:200], fatal=False)
        plan_res = QueryPlanList(
            queries=[],
            warnings=["LLM query planning unavailable (high demand / quota); generated deterministic targeted queries."]
        )

    # Post-validation: ensure minimum 2 queries per target country with target_level
    if profile.target_countries:
        for country in profile.target_countries:
            field = profile.field or "Engineering"
            country_queries = [q for q in plan_res.queries if country.lower() in q.query.lower()]
            while len(country_queries) < 2:
                idx = len(country_queries) + 1
                if idx == 1:
                    query_text = f"official {country} government and university scholarships for international students {field} {target_level} full funding"
                else:
                    query_text = f"top accredited universities in {country} offering fully funded {target_level} {field} scholarships"

                new_plan = SearchQueryPlan(
                    query=query_text,
                    target_country=country,
                    funding_focus="full",
                    official_body_focus=True,
                    intent=f"Country coverage requirement for {country} {target_level} (query #{idx})",
                )
                plan_res.queries.append(new_plan)
                country_queries.append(new_plan)
    else:
        plan_res.warnings.append("No target countries specified by student; planned broad international queries.")


    return plan_res


async def resolve_aggregator_hit(
    hit: SearchHit,
    telemetry: Optional[Telemetry] = None,
) -> SearchHit:
    """If hit is on an aggregator domain but specifies a named opportunity, locate official portal."""
    if is_official_domain(hit.url):
        return hit

    # Check if title looks like a specific scholarship
    title = hit.title.strip()
    if len(title) > 5 and any(word in title.lower() for word in ["scholarship", "fellowship", "award", "program"]):
        resolution_query = f"{title} official portal application"
        try:
            res_hits = await search_tool.search(resolution_query, max_results=3, telemetry=telemetry)
            for res_hit in res_hits:
                if is_official_domain(res_hit.url):
                    if telemetry:
                        telemetry.increment("aggregator_resolutions")
                    return SearchHit(
                        title=hit.title,
                        url=res_hit.url,
                        snippet=res_hit.snippet or hit.snippet,
                        source_tier="official",
                        provider=res_hit.provider,
                    )
        except Exception as exc:
            logger.debug("Resolution failed for %s: %s", hit.url, exc)

    return hit


async def triage_hits_to_candidates(
    hits: List[SearchHit],
    target_degree_level: Optional[str] = None,
    telemetry: Optional[Telemetry] = None,
) -> List[Opportunity]:
    """Triage raw search hits into Opportunity schemas using structured LLM extraction and level filtering."""
    if not hits:
        return []

    hits_text = ""
    for i, h in enumerate(hits, 1):
        hits_text += (
            f"Hit #{i}:\n"
            f"Title: {h.title}\n"
            f"URL: {h.url}\n"
            f"Snippet: {h.snippet}\n"
            f"Tier: {h.source_tier}\n\n"
        )

    target_note = f" (TARGET DEGREE LEVEL: {target_degree_level})" if target_degree_level else ""
    triage_prompt = (
        f"Review these live search hits and extract valid scholarship/university candidate opportunities{target_note}.\n"
        f"Do NOT invent details not mentioned in the snippets. Drop hits that do not match {target_degree_level or 'the student level'}.\n\n"
        f"{hits_text}"
    )

    try:
        triage_result: CandidateTriageList = await llm_client.generate_json(
            prompt=triage_prompt,
            schema=CandidateTriageList,
            system_instruction=TRIAGE_SYSTEM_PROMPT,
            telemetry=telemetry,
        )
    except Exception as exc:
        logger.warning("Scout triage LLM unavailable (%s); falling back to direct search hit triage.", exc)
        if telemetry:
            telemetry.record_failure("scout_triage_llm", error=str(exc)[:200], fatal=False)
        triage_result = CandidateTriageList(
            candidates=[
                Opportunity(
                    name=h.title[:120] if h.title else "University Scholarship Opportunity",
                    url=h.url,
                    funding_summary=h.snippet[:250] if h.snippet else "Funding information on official university portal",
                    source_snippet=h.snippet,
                )
                for h in hits if h.url
            ]
        )

    # Clean, match URLs, and apply deterministic degree-level filter (A1)
    valid_candidates: List[Opportunity] = []
    seen_urls: Set[str] = set()

    # Generic conflicting level keywords
    is_graduate_target = target_degree_level and any(
        kw in target_degree_level.lower() for kw in ["master", "msc", "phd", "doctoral", "postgraduate"]
    )
    conflicting_kws = ["polytechnic", "diploma", "year 12", "secondary school", "high school", "k-12", "military service bond"]

    for cand in triage_result.candidates:
        if cand.url:
            clean_key = normalize_url_key(cand.url)
            if clean_key not in seen_urls:
                text_block = f"{cand.name or ''} {cand.funding_summary or ''} {cand.source_snippet or ''}".lower()
                # If target is graduate, drop obvious pre-degree/diploma items
                if is_graduate_target and any(ck in text_block for ck in conflicting_kws):
                    logger.info("Triage dropped non-graduate opportunity: %s", cand.name)
                    continue
                seen_urls.add(clean_key)
                valid_candidates.append(cand)

    return valid_candidates


async def run_scout(
    profile: StudentProfile,
    target_count: int = 10,
    max_candidates: int = 15,
    max_queries: int = 20,  # Phase 8: configurable query budget
    run_id: str = "run-scout",
    emit: EmitCallback = None,
    telemetry: Optional[Telemetry] = None,
) -> List[Opportunity]:
    """Execute end-to-end Scout pipeline: Query Planning -> Live Search -> Resolution -> Triage.

    Args:
        max_queries: Hard upper limit on search queries executed (default 20).
            Country coverage (>=2 per target country) is guaranteed before trimming.
            Any trimming is recorded in telemetry. Setting to 0 disables the budget.
    """
    if emit:
        await emit(AgentEvent(run_id=run_id, agent="Scout", message="Planning targeted search queries from student profile...", level="info"))

    # Step 1: Query Planning
    query_plan = await plan_queries(profile, telemetry=telemetry)

    # Phase 8: Apply query budget — preserve country coverage first, then trim extras
    if max_queries > 0 and len(query_plan.queries) > max_queries:
        country_queries: List[SearchQueryPlan] = []
        other_queries: List[SearchQueryPlan] = []
        if profile.target_countries:
            country_set = {c.lower() for c in profile.target_countries}
            for q in query_plan.queries:
                if q.target_country and q.target_country.lower() in country_set:
                    country_queries.append(q)
                else:
                    other_queries.append(q)
        else:
            other_queries = list(query_plan.queries)

        # Keep all country queries (guaranteed coverage) + fill remaining budget from others
        budget_remaining = max(0, max_queries - len(country_queries))
        trimmed_queries = country_queries + other_queries[:budget_remaining]
        n_dropped = len(query_plan.queries) - len(trimmed_queries)

        if n_dropped > 0:
            logger.info("Scout: query budget trimmed %d queries (%d -> %d)", n_dropped, len(query_plan.queries), len(trimmed_queries))
            if telemetry:
                telemetry.record_failure(
                    "scout_query_budget",
                    error=f"Query budget ({max_queries}) trimmed {n_dropped} queries to stay within limit.",
                    fatal=False,
                )
            query_plan.queries = trimmed_queries
            query_plan.warnings.append(
                f"Query budget limit ({max_queries}): {n_dropped} queries dropped. "
                f"Country coverage preserved ({len(country_queries)} country-specific queries retained)."
            )

    if emit:
        await emit(AgentEvent(
            run_id=run_id,
            agent="Scout",
            message=f"Planned {len(query_plan.queries)} search queries across countries: {', '.join(profile.target_countries) or 'Global'}",
            level="info",
            payload={"query_count": len(query_plan.queries)},
        ))

    # Step 2: Concurrent Live Search Execution
    raw_hits: List[SearchHit] = []

    async def _execute_query(q_obj: SearchQueryPlan):
        async with SCOUT_SEARCH_SEMAPHORE:
            return await search_tool.search(q_obj.query, max_results=3, telemetry=telemetry)

    search_tasks = [_execute_query(q) for q in query_plan.queries]
    nested_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    for res in nested_results:
        if isinstance(res, list):
            raw_hits.extend(res)
        elif isinstance(res, Exception):
            logger.warning("Scout query task raised error: %s", res)

    # De-duplicate raw hits
    deduped_hits: List[SearchHit] = []
    seen_keys: Set[str] = set()
    for hit in raw_hits:
        key = normalize_url_key(hit.url)
        if key not in seen_keys:
            seen_keys.add(key)
            deduped_hits.append(hit)

    if emit:
        official_count = sum(1 for h in deduped_hits if h.source_tier == "official")
        await emit(AgentEvent(
            run_id=run_id,
            agent="Scout",
            message=f"Discovered {len(deduped_hits)} unique web resources ({official_count} official domains). Resolving portals...",
            level="info",
        ))

    # Step 3: Resolve Aggregators to Official Portals
    resolution_tasks = [resolve_aggregator_hit(h, telemetry=telemetry) for h in deduped_hits[:20]]
    resolved_hits = await asyncio.gather(*resolution_tasks)

    # Step 4: Triage into Opportunities
    candidates = await triage_hits_to_candidates(
        resolved_hits,
        target_degree_level=profile.target_degree_level,
        telemetry=telemetry,
    )

    # Step 5: Second-Pass Recovery if fewer than target
    remaining_budget = (max_queries - len(query_plan.queries)) if max_queries > 0 else None
    if len(candidates) < target_count and (remaining_budget is None or remaining_budget > 0):
        if emit:
            await emit(AgentEvent(
                run_id=run_id,
                agent="Scout",
                message=f"Candidate count ({len(candidates)}) is under target ({target_count}). Executing second-pass broadened recovery...",
                level="warn",
            ))
        if telemetry:
            telemetry.record_retry(
                component="scout_recovery",
                attempt=2,
                reason=f"Found {len(candidates)} candidates, target is {target_count}",
            )

        # Generate broadened fallback queries using target_degree_level
        target_lvl = profile.target_degree_level or "Master"
        field = profile.field or "STEM"
        broad_queries = [
            f"fully funded {target_lvl} scholarships in Europe for international students {field}",
            f"international student tuition fee waiver {target_lvl} {field} scholarships official",
            f"commonwealth and bilateral government scholarships {target_lvl} international students",
        ]
        if remaining_budget is not None:
            broad_queries = broad_queries[:remaining_budget]

        if broad_queries:
            broad_tasks = [search_tool.search(bq, max_results=4, telemetry=telemetry) for bq in broad_queries]
            second_pass_results = await asyncio.gather(*broad_tasks, return_exceptions=True)
            second_hits: List[SearchHit] = []
            for r in second_pass_results:
                if isinstance(r, list):
                    second_hits.extend(r)

            second_candidates = await triage_hits_to_candidates(
                second_hits,
                target_degree_level=profile.target_degree_level,
                telemetry=telemetry,
            )
            for sc in second_candidates:
                if normalize_url_key(sc.url) not in {normalize_url_key(c.url) for c in candidates}:
                    candidates.append(sc)
                    if len(candidates) >= max_candidates:
                        break

    # Prioritize candidates by source tier (A6: official first, then reputable, then unknown)
    tier_priority = {"official": 0, "reputable": 1, "unknown": 2}
    candidates.sort(key=lambda c: tier_priority.get(classify_source_tier(c.url or ""), 2))

    # Cap to max_candidates (A6: default 12)
    candidates = candidates[:max_candidates]

    if emit:
        tier_counts = {
            "official": sum(1 for c in candidates if classify_source_tier(c.url or "") == "official"),
            "reputable": sum(1 for c in candidates if classify_source_tier(c.url or "") == "reputable"),
            "unknown": sum(1 for c in candidates if classify_source_tier(c.url or "") == "unknown"),
        }
        await emit(AgentEvent(
            run_id=run_id,
            agent="Scout",
            message=f"Candidate scouting complete: {len(candidates)} opportunities verified across tiers: {tier_counts}",
            level="success",
            payload={"candidates_count": len(candidates), "tiers": tier_counts},
        ))

    return candidates
