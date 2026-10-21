"""Structural tests for Pydantic v2 schemas and async EventBus delivery.

Rules:
- Structural tests only.
- ZERO fake or mock scholarship data.
- Ensures all defaults are None or empty lists.
- Ensures EventBus delivers events in correct FIFO order.
"""

import asyncio
import pytest
from app.events import AgentEvent, EventBus
from app.schemas import (
    ApplicationDraft,
    Evidence,
    Opportunity,
    Project,
    Recommendation,
    RunResult,
    StudentProfile,
    VerifiedOpportunity,
)


def test_schema_defaults_are_honest_and_empty():
    """Verify that models initialize with empty/None defaults and never invent values."""
    # StudentProfile default verification
    profile = StudentProfile()
    assert profile.name is None
    assert profile.cgpa is None
    assert profile.skills == []
    assert profile.projects == []
    assert profile.missing_info == []

    # Opportunity default verification
    opp = Opportunity()
    assert opp.name is None
    assert opp.funding_summary is None
    assert opp.deadline is None

    # VerifiedOpportunity default verification
    verified = VerifiedOpportunity()
    assert verified.eligibility_met == []
    assert verified.unknown_fields == []
    assert verified.source_tier == "unknown"
    assert verified.confidence == 0.0
    assert verified.status == "unverified"
    assert verified.link_alive is None

    # ApplicationDraft default verification
    draft = ApplicationDraft()
    assert draft.text is None
    assert draft.label == "Draft only - not submitted"
    assert draft.revision_count == 0

    # RunResult default verification
    result = RunResult(run_id="run-test-123")
    assert result.run_id == "run-test-123"
    assert result.candidates == []
    assert result.verified == []
    assert result.recommendations == []
    assert result.draft is None


def test_evidence_and_project_instantiation():
    """Verify Evidence and Project schemas hold exact values without mutations."""
    ev = Evidence(
        url="https://example.edu/scholarship",
        fetched_at="2026-10-22T00:00:00Z",
        snippet="Eligibility: CGPA 3.5 or above in STEM degrees.",
    )
    assert ev.url == "https://example.edu/scholarship"
    assert "CGPA 3.5" in ev.snippet

    proj = Project(
        name="Distributed Agent Hub",
        description="Multi-agent asynchronous queue orchestration",
        tech=["Python", "FastAPI"],
    )
    assert proj.name == "Distributed Agent Hub"
    assert "Python" in proj.tech


@pytest.mark.asyncio
async def test_event_bus_pub_sub_fifo_order():
    """Verify that EventBus broadcasts events to subscribers in exact FIFO sequence."""
    bus = EventBus()
    run_id = "test-run-abc"
    received_messages = []

    async def subscriber():
        async for event in bus.subscribe(run_id, replay_history=False):
            received_messages.append(event.message)

    sub_task = asyncio.create_task(subscriber())
    # Yield control briefly to ensure subscription queue is attached
    await asyncio.sleep(0.01)

    # Publish sequence of agent events
    await bus.publish(AgentEvent(run_id=run_id, agent="Orchestrator", message="Starting swarm run"))
    await bus.publish(AgentEvent(run_id=run_id, agent="ProfileAnalyst", message="Parsing student transcript"))
    await bus.publish(AgentEvent(run_id=run_id, agent="Scout", message="Discovered candidate opportunities"))

    # Close stream
    await bus.close_run(run_id)
    await sub_task

    assert received_messages == [
        "Starting swarm run",
        "Parsing student transcript",
        "Discovered candidate opportunities",
    ]

    # Verify history buffer has all 3 events
    history = bus.get_history(run_id)
    assert len(history) == 3
    assert history[0].agent == "Orchestrator"
    assert history[2].agent == "Scout"
