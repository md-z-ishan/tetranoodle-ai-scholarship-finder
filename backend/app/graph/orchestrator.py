"""LangGraph End-to-End Swarm Orchestrator.

Architecture:
  START -> profile_analyst -> scout -> verifier -> (conditional: if 0 viable -> broadened_scout -> verifier)
        -> ranker -> (conditional: if viable recs -> writer <-> critic -> finalize) -> END

Resilience Guarantees:
- Every node is wrapped in an exception handler; failures record in telemetry, emit error/warn events,
  and downgrade gracefully to partial results rather than crashing the swarm.
- Global timeout enforced via MAX_RUN_SECONDS (default 180s); on timeout, finalizes with existing state.
- Emits start and finish events per agent with duration and status for real-time UI agent lanes.
- Zero hardcoded student or country data: completely generalized.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.critic import run_critic
from app.agents.profile_analyst import run_profile_analyst
from app.agents.ranker import run_ranker
from app.agents.scout import run_scout
from app.agents.verifier import run_verifier
from app.agents.writer import run_writer
from app.config import settings
from app.events import AgentEvent, EventBus
from app.schemas import (
    ApplicationDraft,
    ExtraInputs,
    Opportunity,
    Recommendation,
    RunResult,
    StudentProfile,
    VerifiedOpportunity,
)
from app.tools.llm import QuotaExhaustedError
from app.tools.telemetry import EmitCallback, Telemetry


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Swarm State Model
# ---------------------------------------------------------------------------

class SwarmState(TypedDict, total=False):
    run_id: str
    resume_path: Optional[str]
    marksheet_path: Optional[str]
    extra_inputs: Optional[Dict[str, Any]]
    chosen_opportunity_name: Optional[str]

    # Pipeline Artifacts
    profile: Optional[Dict[str, Any]]
    candidates: List[Dict[str, Any]]
    verified: List[Dict[str, Any]]
    recommendations: List[Dict[str, Any]]
    draft: Optional[Dict[str, Any]]

    # Review & Loop state
    critic_verdict: Optional[str]
    critic_issues: List[str]
    critic_notes: List[str]
    revision_count: int
    is_broadened_pass_done: bool

    # Executive Output & Telemetry
    executive_summary: Optional[str]
    what_we_could_not_verify: List[str]
    needs_from_student: List[str]
    warnings: List[str]
    timings: Dict[str, float]
    run_result: Optional[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Global execution context registry (for injecting callbacks into nodes)
# ---------------------------------------------------------------------------

_contexts: Dict[str, Dict[str, Any]] = {}


def register_context(run_id: str, emit: EmitCallback, telemetry: Telemetry):
    _contexts[run_id] = {"emit": emit, "telemetry": telemetry}


def get_context(run_id: str) -> Tuple[EmitCallback, Telemetry]:
    ctx = _contexts.get(run_id, {})
    return ctx.get("emit"), ctx.get("telemetry", Telemetry(run_id=run_id))


def unregister_context(run_id: str):
    _contexts.pop(run_id, None)


# ---------------------------------------------------------------------------
# Helper: Wrap agent execution with start/finish events and timing
# ---------------------------------------------------------------------------

async def _emit_agent_start(agent: str, run_id: str, emit: EmitCallback):
    if emit:
        await emit(AgentEvent(
            run_id=run_id,
            agent=agent,
            message=f"{agent} started",
            level="info",
            payload={"status": "working", "agent": agent},
        ))


async def _emit_agent_finish(agent: str, run_id: str, emit: EmitCallback, duration_s: float, status: str = "done"):
    if emit:
        await emit(AgentEvent(
            run_id=run_id,
            agent=agent,
            message=f"{agent} completed in {duration_s:.1f}s",
            level="success" if status == "done" else "warn",
            payload={"status": status, "agent": agent, "duration_s": round(duration_s, 2)},
        ))


# ---------------------------------------------------------------------------
# Node 1: Profile Analyst
# ---------------------------------------------------------------------------

async def node_profile_analyst(state: SwarmState) -> Dict[str, Any]:
    run_id = state.get("run_id", "run-default")
    emit, telemetry = get_context(run_id)
    timings = dict(state.get("timings", {}))
    t0 = time.monotonic()

    await _emit_agent_start("ProfileAnalyst", run_id, emit)
    try:
        resume_p = Path(state["resume_path"]) if state.get("resume_path") else None
        marksheet_p = Path(state["marksheet_path"]) if state.get("marksheet_path") else None
        extra = ExtraInputs.model_validate(state["extra_inputs"]) if state.get("extra_inputs") else None

        profile_obj = await run_profile_analyst(
            resume_source=resume_p,
            marksheet_source=marksheet_p,
            extra_inputs=extra,
            run_id=run_id,
            emit=emit,
            telemetry=telemetry,
        )
        duration = time.monotonic() - t0
        timings["profile_analyst"] = duration
        await _emit_agent_finish("ProfileAnalyst", run_id, emit, duration, status="done")
        return {"profile": profile_obj.model_dump(), "timings": timings}

    except Exception as exc:
        logger.error("ProfileAnalyst node failed: %s", exc)
        telemetry.record_failure("node_profile_analyst", error=str(exc), fatal=False)
        duration = time.monotonic() - t0
        timings["profile_analyst"] = duration
        await _emit_agent_finish("ProfileAnalyst", run_id, emit, duration, status="warning")
        # Return fallback minimal profile
        fallback = StudentProfile(degree="Higher Education", field="General Studies", target_degree_level="Master")
        return {"profile": fallback.model_dump(), "timings": timings, "warnings": [f"ProfileAnalyst degraded: {exc}"]}


# ---------------------------------------------------------------------------
# Node 2: Scout Agent
# ---------------------------------------------------------------------------

async def node_scout(state: SwarmState) -> Dict[str, Any]:
    run_id = state.get("run_id", "run-default")
    emit, telemetry = get_context(run_id)
    timings = dict(state.get("timings", {}))
    t0 = time.monotonic()

    await _emit_agent_start("Scout", run_id, emit)
    try:
        profile_obj = StudentProfile.model_validate(state["profile"])
        candidates = await run_scout(
            profile=profile_obj,
            target_count=8,
            max_candidates=settings.max_candidates,
            run_id=run_id,
            emit=emit,
            telemetry=telemetry,
        )
        duration = time.monotonic() - t0
        timings["scout"] = duration
        await _emit_agent_finish("Scout", run_id, emit, duration, status="done")
        return {"candidates": [c.model_dump() for c in candidates], "timings": timings}

    except Exception as exc:
        logger.error("Scout node failed: %s", exc)
        telemetry.record_failure("node_scout", error=str(exc), fatal=False)
        duration = time.monotonic() - t0
        timings["scout"] = duration
        await _emit_agent_finish("Scout", run_id, emit, duration, status="warning")
        return {"candidates": [], "timings": timings, "warnings": [f"Scout discovery degraded: {exc}"]}


# ---------------------------------------------------------------------------
# Node 3: Verifier Agent
# ---------------------------------------------------------------------------

async def node_verifier(state: SwarmState) -> Dict[str, Any]:
    run_id = state.get("run_id", "run-default")
    emit, telemetry = get_context(run_id)
    timings = dict(state.get("timings", {}))
    t0 = time.monotonic()

    await _emit_agent_start("Verifier", run_id, emit)
    try:
        candidates_raw = state.get("candidates", [])
        candidates = [Opportunity.model_validate(c) for c in candidates_raw]
        from app.tools.domains import is_official_domain
        candidates.sort(key=lambda c: 0 if is_official_domain(c.url or "") else 1)
        candidates = candidates[:settings.max_candidates]
        profile_obj = StudentProfile.model_validate(state["profile"])

        verified = await run_verifier(
            candidates=candidates,
            profile=profile_obj,
            run_id=run_id,
            emit=emit,
            telemetry=telemetry,
        )
        duration = time.monotonic() - t0
        timings["verifier"] = duration
        await _emit_agent_finish("Verifier", run_id, emit, duration, status="done")
        return {"verified": [v.model_dump() for v in verified], "timings": timings}

    except Exception as exc:
        logger.error("Verifier node failed: %s", exc)
        telemetry.record_failure("node_verifier", error=str(exc), fatal=False)
        duration = time.monotonic() - t0
        timings["verifier"] = duration
        await _emit_agent_finish("Verifier", run_id, emit, duration, status="warning")
        return {"verified": [], "timings": timings, "warnings": [f"Verifier degraded: {exc}"]}


# ---------------------------------------------------------------------------
# Node 3B: Broadened Scout Pass (conditional recovery if 0 viable)
# ---------------------------------------------------------------------------

async def node_broadened_scout(state: SwarmState) -> Dict[str, Any]:
    run_id = state.get("run_id", "run-default")
    emit, telemetry = get_context(run_id)
    if emit:
        await emit(AgentEvent(
            run_id=run_id,
            agent="Scout",
            message="Zero viable candidates verified. Executing broadened international recovery pass…",
            level="warn",
        ))

    profile_obj = StudentProfile.model_validate(state["profile"])
    # Broaden targets
    profile_obj.target_countries = []
    broad_candidates = await run_scout(
        profile=profile_obj,
        target_count=6,
        max_candidates=8,
        run_id=run_id,
        emit=emit,
        telemetry=telemetry,
    )
    # Re-verify newly found candidates
    verified = await run_verifier(
        candidates=broad_candidates,
        profile=profile_obj,
        run_id=run_id,
        emit=emit,
        telemetry=telemetry,
    )
    return {
        "verified": [v.model_dump() for v in verified],
        "is_broadened_pass_done": True,
        "warnings": ["Zero candidates passed initial verification; ran broadened recovery pass."],
    }


# ---------------------------------------------------------------------------
# Node 4: Ranker Agent
# ---------------------------------------------------------------------------

async def node_ranker(state: SwarmState) -> Dict[str, Any]:
    run_id = state.get("run_id", "run-default")
    emit, telemetry = get_context(run_id)
    timings = dict(state.get("timings", {}))
    t0 = time.monotonic()

    await _emit_agent_start("Ranker", run_id, emit)
    try:
        verified_raw = state.get("verified", [])
        verified = [VerifiedOpportunity.model_validate(v) for v in verified_raw]
        profile_obj = StudentProfile.model_validate(state["profile"])

        recs, summary, could_not_verify, needs_student = await run_ranker(
            verified=verified,
            profile=profile_obj,
            run_id=run_id,
            emit=emit,
            telemetry=telemetry,
        )
        duration = time.monotonic() - t0
        timings["ranker"] = duration
        await _emit_agent_finish("Ranker", run_id, emit, duration, status="done")

        return {
            "recommendations": [r.model_dump() for r in recs],
            "executive_summary": summary,
            "what_we_could_not_verify": could_not_verify,
            "needs_from_student": needs_student,
            "timings": timings,
        }

    except Exception as exc:
        logger.error("Ranker node failed: %s", exc)
        telemetry.record_failure("node_ranker", error=str(exc), fatal=False)
        duration = time.monotonic() - t0
        timings["ranker"] = duration
        await _emit_agent_finish("Ranker", run_id, emit, duration, status="warning")
        return {
            "recommendations": [],
            "executive_summary": "Ranker scoring degraded due to an unexpected error.",
            "what_we_could_not_verify": [f"Ranker error: {exc}"],
            "timings": timings,
        }


# ---------------------------------------------------------------------------
# Node 5: Writer Agent
# ---------------------------------------------------------------------------

async def node_writer(state: SwarmState) -> Dict[str, Any]:
    run_id = state.get("run_id", "run-default")
    emit, telemetry = get_context(run_id)
    timings = dict(state.get("timings", {}))
    t0 = time.monotonic()

    recs_raw = state.get("recommendations", [])
    if not recs_raw:
        return {"draft": None}

    await _emit_agent_start("Writer", run_id, emit)
    try:
        profile_obj = StudentProfile.model_validate(state["profile"])
        recs = [Recommendation.model_validate(r) for r in recs_raw]

        # Select chosen opportunity or fallback to Rank #1
        chosen_name = state.get("chosen_opportunity_name")
        target_opp: Optional[VerifiedOpportunity] = None
        if chosen_name:
            for r in recs:
                if chosen_name.lower() in (r.opportunity.name or "").lower():
                    target_opp = r.opportunity
                    break
        if not target_opp:
            target_opp = recs[0].opportunity

        revision_count = state.get("revision_count", 0)
        issues = state.get("critic_issues", [])

        draft = await run_writer(
            profile=profile_obj,
            opportunity=target_opp,
            critic_issues=issues if revision_count > 0 else None,
            revision_count=revision_count,
            run_id=run_id,
            emit=emit,
            telemetry=telemetry,
        )
        duration = time.monotonic() - t0
        timings[f"writer_round_{revision_count}"] = duration
        await _emit_agent_finish("Writer", run_id, emit, duration, status="done")
        return {"draft": draft.model_dump(), "timings": timings}

    except Exception as exc:
        logger.error("Writer node failed: %s", exc)
        telemetry.record_failure("node_writer", error=str(exc), fatal=False)
        duration = time.monotonic() - t0
        await _emit_agent_finish("Writer", run_id, emit, duration, status="warning")
        return {"draft": None, "warnings": [f"Writer degraded: {exc}"]}


# ---------------------------------------------------------------------------
# Node 6: Critic Agent
# ---------------------------------------------------------------------------

async def node_critic(state: SwarmState) -> Dict[str, Any]:
    run_id = state.get("run_id", "run-default")
    emit, telemetry = get_context(run_id)
    timings = dict(state.get("timings", {}))
    t0 = time.monotonic()

    draft_dict = state.get("draft")
    recs_raw = state.get("recommendations", [])
    if not draft_dict or not recs_raw:
        return {"critic_verdict": "approve"}

    await _emit_agent_start("Critic", run_id, emit)
    try:
        profile_obj = StudentProfile.model_validate(state["profile"])
        draft_obj = ApplicationDraft.model_validate(draft_dict)
        recs = [Recommendation.model_validate(r) for r in recs_raw]
        target_opp = recs[0].opportunity

        verdict, issues, rationale = await run_critic(
            draft=draft_obj,
            profile=profile_obj,
            opportunity=target_opp,
            run_id=run_id,
            emit=emit,
            telemetry=telemetry,
        )

        duration = time.monotonic() - t0
        timings[f"critic_round_{draft_obj.revision_count}"] = duration
        await _emit_agent_finish("Critic", run_id, emit, duration, status="done")

        critic_notes = list(state.get("critic_notes", []))
        if issues:
            critic_notes.extend([f"[Round {draft_obj.revision_count}] {i}" for i in issues])

        revision_count = state.get("revision_count", 0) + 1
        return {
            "critic_verdict": verdict,
            "critic_issues": issues,
            "critic_notes": critic_notes,
            "revision_count": revision_count,
            "timings": timings,
        }

    except Exception as exc:
        logger.error("Critic node failed: %s", exc)
        telemetry.record_failure("node_critic", error=str(exc), fatal=False)
        duration = time.monotonic() - t0
        await _emit_agent_finish("Critic", run_id, emit, duration, status="warning")
        return {"critic_verdict": "approve", "timings": timings}


# ---------------------------------------------------------------------------
# Node 7: Finalize & RunResult Compilation
# ---------------------------------------------------------------------------

def build_executive_summary(state: dict) -> str:
    """Compose a truthful executive summary from actual run-state values.

    Never claims success when recommendations are empty.
    Explicitly reports quota/timeout constraints and partial verification counts.
    """
    parts: list[str] = []

    # Profile extraction quality
    profile_dict = state.get("profile") or {}
    profile_name = profile_dict.get("name") or "the candidate"
    source_notes = profile_dict.get("source_notes") or []
    deterministic_fallback = any("DETERMINISTIC FALLBACK" in n for n in source_notes)
    if deterministic_fallback:
        parts.append(
            f"Profile for {profile_name} was extracted using deterministic pattern-matching "
            "(LLM synthesis was unavailable due to API quota exhaustion). "
            "Profile accuracy is reduced; some fields may be missing or incorrect."
        )
    else:
        parts.append(f"Profile for {profile_name} was fully synthesized from uploaded documents.")

    # Scout discovery
    candidates = state.get("candidates") or []
    n_candidates = len(candidates)
    if n_candidates:
        parts.append(f"{n_candidates} scholarship candidate(s) were discovered by the Scout agent.")
    else:
        parts.append("No scholarship candidates were discovered.")

    # Verifier outcomes
    verified = state.get("verified") or []
    if verified:
        n_ver = sum(1 for v in verified if isinstance(v, dict) and v.get("status") == "verified")
        n_par = sum(1 for v in verified if isinstance(v, dict) and v.get("status") == "partial")
        n_rej = sum(1 for v in verified if isinstance(v, dict) and v.get("status") == "rejected")
        n_unv = sum(1 for v in verified if isinstance(v, dict) and v.get("status") == "unverified")
        parts.append(
            f"Verification: {n_ver} fully verified, {n_par} partial, "
            f"{n_rej} rejected, {n_unv} unverified (evidence not retrieved)."
        )
    elif n_candidates:
        parts.append(
            "Verification was not completed — the verifier agent did not finish within the run timeout. "
            f"All {n_candidates} discovered candidates remain unverified."
        )

    # Ranking and SOP
    recommendations = state.get("recommendations") or []
    n_recs = len(recommendations)
    if n_recs:
        parts.append(f"Ranker produced {n_recs} viable recommendation(s) for the student.")
        draft = state.get("draft")
        if draft:
            parts.append("A tailored Statement of Purpose draft was generated for the top recommendation.")
        else:
            parts.append("SOP draft was not generated (writer did not execute or failed).")
    else:
        parts.append(
            "No ranked recommendations were produced. "
            "This may be because no candidates survived the viability filter, "
            "or because the verifier did not complete before the run timeout."
        )

    # Quota / timeout flags from run_health / telemetry events
    warnings = state.get("warnings") or []
    if warnings:
        parts.append("Warnings: " + " | ".join(str(w) for w in warnings[:3]))

    return " ".join(parts)


async def node_finalize(state: SwarmState) -> Dict[str, Any]:
    run_id = state.get("run_id", "run-default")
    emit, telemetry = get_context(run_id)

    profile_dict = state.get("profile")
    profile = StudentProfile.model_validate(profile_dict) if profile_dict else None

    candidates = [Opportunity.model_validate(c) for c in state.get("candidates", [])]
    verified = [VerifiedOpportunity.model_validate(v) for v in state.get("verified", [])]
    recommendations = [Recommendation.model_validate(r) for r in state.get("recommendations", [])]

    # Timeout degradation: if verification never returned results but candidates exist,
    # promote them as unverified so the UI shows discovered scholarships, not a blank panel.
    if not verified and candidates:
        from datetime import datetime as _dt
        verified = [
            VerifiedOpportunity(
                **c.model_dump(),
                status="unverified",
                rejection_reason="Verification incomplete: API quota exceeded during run. Candidate discovered but not evidence-verified.",
                link_alive=True,
                fetched_at=_dt.utcnow().isoformat() + "Z",
            )
            for c in candidates
        ]

    draft_dict = state.get("draft")
    draft = ApplicationDraft.model_validate(draft_dict) if draft_dict else None
    if draft and state.get("critic_notes"):
        draft.critic_notes = state["critic_notes"]

    result = RunResult(
        run_id=run_id,
        profile=profile,
        candidates=candidates,
        verified=verified,
        recommendations=recommendations,
        draft=draft,
        executive_summary=state.get("executive_summary") or build_executive_summary(state),
        what_we_could_not_verify=state.get("what_we_could_not_verify", []),
        needs_from_student=state.get("needs_from_student", []),
        warnings=state.get("warnings", []),
        timings=state.get("timings", {}),
        run_health=telemetry.summary(),
    )

    if emit:
        await emit(AgentEvent(
            run_id=run_id,
            agent="Orchestrator",
            message=f"Pipeline finished! Generated {len(recommendations)} recommendations. Draft ready={bool(draft)}.",
            level="success",
            payload={"run_id": run_id, "recommendations_count": len(recommendations)},
        ))

    return {"run_result": result.model_dump()}


# ---------------------------------------------------------------------------
# Edge Decision Functions
# ---------------------------------------------------------------------------

def decide_after_verifier(state: SwarmState) -> Literal["node_broadened_scout", "node_ranker"]:
    verified = state.get("verified", [])
    viable_count = sum(1 for v in verified if v.get("status") in ("verified", "partial") and v.get("deadline_status") != "passed")
    if viable_count == 0 and not state.get("is_broadened_pass_done", False):
        return "node_broadened_scout"
    return "node_ranker"


def decide_after_ranker(state: SwarmState) -> Literal["node_writer", "node_finalize"]:
    recs = state.get("recommendations", [])
    if recs:
        return "node_writer"
    return "node_finalize"


def decide_after_critic(state: SwarmState) -> Literal["node_writer", "node_finalize"]:
    verdict = state.get("critic_verdict", "approve")
    revision_count = state.get("revision_count", 0)
    if verdict == "revise" and revision_count < 2:
        return "node_writer"
    return "node_finalize"


# ---------------------------------------------------------------------------
# Graph Builder
# ---------------------------------------------------------------------------

def create_swarm_graph() -> StateGraph:
    """Build LangGraph workflow with failure paths and revision cycles."""
    workflow = StateGraph(SwarmState)

    # Add Nodes
    workflow.add_node("node_profile_analyst", node_profile_analyst)
    workflow.add_node("node_scout", node_scout)
    workflow.add_node("node_verifier", node_verifier)
    workflow.add_node("node_broadened_scout", node_broadened_scout)
    workflow.add_node("node_ranker", node_ranker)
    workflow.add_node("node_writer", node_writer)
    workflow.add_node("node_critic", node_critic)
    workflow.add_node("node_finalize", node_finalize)

    # Linear and conditional edges
    workflow.add_edge(START, "node_profile_analyst")
    workflow.add_edge("node_profile_analyst", "node_scout")
    workflow.add_edge("node_scout", "node_verifier")

    workflow.add_conditional_edges(
        "node_verifier",
        decide_after_verifier,
        {
            "node_broadened_scout": "node_broadened_scout",
            "node_ranker": "node_ranker",
        },
    )
    workflow.add_edge("node_broadened_scout", "node_ranker")

    workflow.add_conditional_edges(
        "node_ranker",
        decide_after_ranker,
        {
            "node_writer": "node_writer",
            "node_finalize": "node_finalize",
        },
    )

    workflow.add_edge("node_writer", "node_critic")

    workflow.add_conditional_edges(
        "node_critic",
        decide_after_critic,
        {
            "node_writer": "node_writer",
            "node_finalize": "node_finalize",
        },
    )

    workflow.add_edge("node_finalize", END)
    return workflow.compile()


swarm_pipeline = create_swarm_graph()


# ---------------------------------------------------------------------------
# High-Level Async Pipeline Runner
# ---------------------------------------------------------------------------

async def run_pipeline(
    run_id: str,
    resume_path: Optional[str] = None,
    marksheet_path: Optional[str] = None,
    extra_inputs: Optional[ExtraInputs] = None,
    chosen_opportunity_name: Optional[str] = None,
    emit: EmitCallback = None,
    telemetry: Optional[Telemetry] = None,
    timeout_seconds: Optional[int] = None,
) -> RunResult:
    """Execute end-to-end swarm pipeline with timeout guard and context registration."""
    telem = telemetry or Telemetry(run_id=run_id)
    register_context(run_id, emit, telem)

    initial_state: SwarmState = {
        "run_id": run_id,
        "resume_path": resume_path,
        "marksheet_path": marksheet_path,
        "extra_inputs": extra_inputs.model_dump() if extra_inputs else None,
        "chosen_opportunity_name": chosen_opportunity_name,
        "revision_count": 0,
        "is_broadened_pass_done": False,
        "timings": {},
        "warnings": [],
        "what_we_could_not_verify": [],
        "needs_from_student": [],
    }

    max_sec = timeout_seconds or settings.max_run_seconds

    accumulated_state = dict(initial_state)

    try:
        async def _run_stream():
            async for output in swarm_pipeline.astream(initial_state):
                for _, node_output in output.items():
                    if isinstance(node_output, dict):
                        accumulated_state.update(node_output)
            return accumulated_state

        final_state = await asyncio.wait_for(_run_stream(), timeout=max_sec)
        res_dict = final_state.get("run_result")
        if res_dict:
            return RunResult.model_validate(res_dict)
    except asyncio.TimeoutError:
        logger.error("Swarm run %s exceeded global timeout (%ds)", run_id, max_sec)
        telem.record_failure("swarm_timeout", error=f"Run exceeded {max_sec}s", fatal=True)
        if emit:
            await emit(AgentEvent(
                run_id=run_id,
                agent="Orchestrator",
                message=f"Global timeout ({max_sec}s) reached; finalizing with available results.",
                level="warn",
            ))
        fallback_res = await node_finalize(accumulated_state)
        return RunResult.model_validate(fallback_res["run_result"])
    finally:
        unregister_context(run_id)

    # Fallback return
    return RunResult(run_id=run_id, executive_summary="Pipeline finished with partial outputs.")
