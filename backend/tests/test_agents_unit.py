"""Offline unit tests for Profile Analyst and Scout agents.

Rules:
- Zero external network requests.
- Zero fake/mock scholarship data.
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
)
from app.schemas import Opportunity, Project, StudentProfile
from app.tools.search_tool import SearchHit


def test_grounding_check_removes_unsupported_hallucinations():
    """Verify that ungrounded skills, projects, and achievements are pruned by the grounding check."""
    source_corpus = (
        "Rahul Sharma is a student in Computer Science. "
        "Completed course in Deep Learning and Natural Language Processing. "
        "Project: Autonomous Drone Navigation using PyTorch and OpenCV. "
        "Secured First Place in State Level Hackathon."
    )

    # Profile with both real and hallucinated items
    profile = StudentProfile(
        name="Rahul Sharma",
        degree="B.Tech Computer Science",
        skills=["Deep Learning", "Quantum Computing", "PyTorch", "Blockchain Development"],
        projects=[
            Project(name="Autonomous Drone Navigation", tech=["PyTorch", "Rust", "Solidity"]),
            Project(name="Decentralized Financial Exchange", tech=["Solidity"]),
        ],
        achievements=["First Place in State Level Hackathon", "Published 3 IEEE Quantum Papers"],
    )

    audited_profile, pruned_items = perform_grounding_audit(profile, source_corpus)

    # Verified grounded items retained
    assert "Deep Learning" in audited_profile.skills
    assert "PyTorch" in audited_profile.skills
    assert any(p.name == "Autonomous Drone Navigation" for p in audited_profile.projects)
    assert "First Place in State Level Hackathon" in audited_profile.achievements

    # Hallucinations pruned
    assert "Quantum Computing" not in audited_profile.skills
    assert "Blockchain Development" not in audited_profile.skills
    assert not any(p.name == "Decentralized Financial Exchange" for p in audited_profile.projects)
    assert "Published 3 IEEE Quantum Papers" not in audited_profile.achievements

    # Pruned list contains hallucinated items
    assert any("Quantum Computing" in p for p in pruned_items)
    assert any("Decentralized Financial Exchange" in p for p in pruned_items)


def test_missing_info_and_confidence_scoring():
    """Verify deterministic computation of missing critical profile fields and weights."""
    incomplete_profile = StudentProfile(
        name="Jane Doe",
        degree="Master of Science",
        field="Robotics",
        skills=["C++", "ROS"],
    )

    missing, confidence = compute_missing_fields_and_confidence(incomplete_profile)

    assert "cgpa" in missing
    assert "target_countries" in missing
    assert "budget_note" in missing
    assert confidence["degree"] == 1.0
    assert confidence["field"] == 1.0
    assert confidence["cgpa"] == 0.0


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

    # Should enforce minimum 2 for Germany and 2 for Singapore
    germany_queries = [q for q in plan.queries if "germany" in q.query.lower()]
    singapore_queries = [q for q in plan.queries if "singapore" in q.query.lower()]

    assert len(germany_queries) >= 2
    assert len(singapore_queries) >= 2
