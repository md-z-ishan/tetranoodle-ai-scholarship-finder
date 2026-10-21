"""Offline unit tests for Profile Analyst and Scout agents.

Rules:
- Zero external network requests.
- Zero fake/mock scholarship data in app code; neutral placeholders only inside test doubles.
- Covers grounding check anti-hallucination pruning, missing_info determination,
  country query coverage rules, search deduplication, and second-pass triggers.
"""

import pytest
from app.agents.profile_analyst import (
    compute_missing_fields_and_confidence,
    is_grounded,
    normalize_tokens,
    perform_grounding_audit,
)
from app.agents.scout import (
    QueryPlanList,
    SearchQueryPlan,
    plan_queries,
    triage_hits_to_candidates,
)
from app.schemas import Opportunity, Project, StudentProfile
from app.tools.search_tool import SearchHit, normalize_url_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _conf_for(conf_list, field_name: str) -> float:
    """Return confidence value for a named field from List[FieldConfidenceScore]."""
    for item in conf_list:
        if item.field_name == field_name:
            return item.confidence
    raise KeyError(f"Field '{field_name}' not found in confidence list")


# ---------------------------------------------------------------------------
# Profile Analyst Tests
# ---------------------------------------------------------------------------

def test_grounding_check_removes_unsupported_hallucinations():
    """Verify that ungrounded skills, projects, and achievements are pruned by the grounding check."""
    source_corpus = (
        "Alex Example is a student in Computer Science. "
        "Completed course in Deep Learning and Natural Language Processing. "
        "Project: Autonomous Navigation System using PyTorch and OpenCV. "
        "Secured First Place in National Example Hackathon."
    )

    profile = StudentProfile(
        name="Alex Example",
        degree="B.Tech Computer Science",
        skills=["Deep Learning", "Quantum Computing", "PyTorch", "Blockchain Development"],
        projects=[
            Project(name="Autonomous Navigation System", tech=["PyTorch", "Rust", "Solidity"]),
            Project(name="Decentralized Financial Exchange", tech=["Solidity"]),
        ],
        achievements=["First Place in National Example Hackathon", "Published 3 Quantum Papers"],
    )

    audited_profile, pruned_items = perform_grounding_audit(profile, source_corpus)

    # Verified grounded items retained
    assert "Deep Learning" in audited_profile.skills
    assert "PyTorch" in audited_profile.skills
    assert any(p.name == "Autonomous Navigation System" for p in audited_profile.projects)
    assert "First Place in National Example Hackathon" in audited_profile.achievements

    # Hallucinations pruned
    assert "Quantum Computing" not in audited_profile.skills
    assert "Blockchain Development" not in audited_profile.skills
    assert not any(p.name == "Decentralized Financial Exchange" for p in audited_profile.projects)
    assert "Published 3 Quantum Papers" not in audited_profile.achievements

    # Pruned list contains hallucinated items
    assert any("Quantum Computing" in p for p in pruned_items)
    assert any("Decentralized Financial Exchange" in p for p in pruned_items)


def test_grounding_check_empty_corpus_prunes_all():
    """All items should be pruned when source corpus is empty."""
    profile = StudentProfile(
        skills=["Python", "React"],
        projects=[Project(name="Example Project", tech=["Docker"])],
        achievements=["Example Award"],
    )
    audited, pruned = perform_grounding_audit(profile, "")
    assert audited.skills == []
    assert audited.projects == []
    assert audited.achievements == []
    assert len(pruned) == 4  # 2 skills + 1 project + 1 achievement


def test_missing_info_and_confidence_scoring():
    """Verify deterministic computation of missing critical profile fields and weights."""
    incomplete_profile = StudentProfile(
        name="Jane Example",
        degree="Master of Science",
        field="Robotics",
        skills=["C++", "ROS"],
    )

    missing, confidence = compute_missing_fields_and_confidence(incomplete_profile)

    assert "cgpa" in missing
    assert "target_countries" in missing
    assert "budget_note" in missing
    assert _conf_for(confidence, "degree") == 1.0
    assert _conf_for(confidence, "field") == 1.0
    assert _conf_for(confidence, "cgpa") == 0.0
    assert _conf_for(confidence, "target_countries") == 0.0


def test_confidence_scoring_with_complete_profile():
    """A fully specified profile should have no missing fields."""
    full_profile = StudentProfile(
        name="Alex Example",
        degree="M.Sc Computer Science",
        field="Machine Learning",
        cgpa=3.9,
        cgpa_scale=4.0,
        graduation_year=2025,
        target_countries=["Canada", "Germany"],
        budget_note="Requires full funding",
        skills=["Python", "TensorFlow", "Kubernetes"],
        projects=[Project(name="Example AI Scheduler", tech=["Python"])],
    )
    missing, confidence = compute_missing_fields_and_confidence(full_profile)
    assert missing == []
    assert _conf_for(confidence, "degree") == 1.0
    assert _conf_for(confidence, "cgpa") == 1.0
    assert _conf_for(confidence, "target_countries") == 0.9


# ---------------------------------------------------------------------------
# Scout Agent Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scout_country_query_coverage_rule(monkeypatch):
    """Verify plan_queries guarantees at least 2 queries per target country."""
    profile = StudentProfile(
        degree="Master of Science",
        field="Computer Science",
        target_countries=["Germany", "Singapore"],
    )

    # Stub LLM returning single query for Germany and none for Singapore
    async def mock_generate_json(prompt, schema, system_instruction=None, telemetry=None):
        return QueryPlanList(
            queries=[
                SearchQueryPlan(
                    query="DAAD Master of Science Computer Science Germany",
                    target_country="Germany",
                    intent="General check",
                )
            ]
        )

    from app.tools.llm import llm_client
    monkeypatch.setattr(llm_client, "generate_json", mock_generate_json)

    plan = await plan_queries(profile)

    germany_queries = [q for q in plan.queries if "germany" in q.query.lower()]
    singapore_queries = [q for q in plan.queries if "singapore" in q.query.lower()]

    assert len(germany_queries) >= 2
    assert len(singapore_queries) >= 2


@pytest.mark.asyncio
async def test_scout_no_countries_adds_warning(monkeypatch):
    """plan_queries should add a warning when no target countries are specified."""
    profile = StudentProfile(degree="PhD", field="Physics")

    async def mock_generate_json(prompt, schema, system_instruction=None, telemetry=None):
        return QueryPlanList(
            queries=[
                SearchQueryPlan(query="international physics PhD scholarship", intent="Broad")
            ]
        )

    from app.tools.llm import llm_client
    monkeypatch.setattr(llm_client, "generate_json", mock_generate_json)

    plan = await plan_queries(profile)
    assert any("no target countries" in w.lower() for w in plan.warnings)


@pytest.mark.asyncio
async def test_triage_deduplicates_urls(monkeypatch):
    """triage_hits_to_candidates must deduplicate candidates sharing the same URL key."""
    async def mock_generate_json(prompt, schema, system_instruction=None, telemetry=None):
        # Return two candidates with identical normalized URL
        from app.agents.scout import CandidateTriageList
        return CandidateTriageList(
            candidates=[
                Opportunity(name="Example Scholarship A", url="https://example-org.edu/scholarship", country="Germany"),
                Opportunity(name="Example Scholarship A", url="https://example-org.edu/scholarship/", country="Germany"),
                Opportunity(name="Example Scholarship B", url="https://another-example.edu/award", country="Canada"),
            ]
        )

    from app.tools.llm import llm_client
    monkeypatch.setattr(llm_client, "generate_json", mock_generate_json)

    hits = [
        SearchHit(title="Example Scholarship", url="https://example-org.edu/scholarship", snippet="Full funding"),
        SearchHit(title="Example Award", url="https://another-example.edu/award", snippet="Partial"),
    ]
    candidates = await triage_hits_to_candidates(hits)
    # Should deduplicate the two identical URLs to one
    urls = [normalize_url_key(c.url) for c in candidates]
    assert len(urls) == len(set(urls)), "Duplicate URLs were not deduplicated"


@pytest.mark.asyncio
async def test_second_pass_triggered_when_under_target(monkeypatch):
    """run_scout should trigger second-pass recovery when candidates < target_count."""
    from app.agents.scout import run_scout
    call_log = []

    async def mock_search(query, max_results=5, telemetry=None):
        call_log.append(query)
        return [
            SearchHit(title="Example Result", url=f"https://example-{len(call_log)}.edu/prog", snippet="Funded program")
        ]

    async def mock_generate_json(prompt, schema, system_instruction=None, telemetry=None):
        # Always return a small set to trigger second-pass
        if "QueryPlanList" in str(schema) or hasattr(schema, "__name__") and "QueryPlan" in schema.__name__:
            return QueryPlanList(
                queries=[
                    SearchQueryPlan(query="example STEM scholarship", target_country="Canada", intent="Test"),
                    SearchQueryPlan(query="example STEM scholarship", target_country="Canada", intent="Test2"),
                ]
            )
        # Triage returns very few
        from app.agents.scout import CandidateTriageList
        return CandidateTriageList(
            candidates=[
                Opportunity(name="Example Prog", url=f"https://example-uni.edu/prog{len(call_log)}", country="Canada"),
            ]
        )

    from app.tools import search_tool as st_module
    from app.tools.llm import llm_client
    monkeypatch.setattr(st_module.search_tool, "search", mock_search)
    monkeypatch.setattr(llm_client, "generate_json", mock_generate_json)

    profile = StudentProfile(degree="Masters", field="Engineering", target_countries=["Canada"])
    await run_scout(profile=profile, target_count=10, max_candidates=15)

    # Second pass should fire extra broad queries (look for 'funded' or 'commonwealth' keywords)
    broad_queries = [q for q in call_log if any(kw in q.lower() for kw in ["funded", "commonwealth", "tuition", "bilateral"])]
    assert len(broad_queries) > 0, "Second-pass recovery queries were not triggered"
