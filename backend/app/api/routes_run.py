"""FastAPI endpoints for multi-agent swarm runs, SSE event streaming, and persistence.

Endpoints:
- POST /api/runs: multipart upload (resume, marksheet, form fields), starts background run.
- GET  /api/runs/{run_id}/events: SSE stream with history replay and live event broadcast.
- GET  /api/runs/{run_id}: status and completed RunResult.
- GET  /api/runs: history list of all saved runs from disk.
- POST /api/runs/{run_id}/rerun: re-runs using saved input files with edited parameters.
- GET  /api/runs/{run_id}/report.md: pure markdown memo for viewing/downloading.
- GET  /api/runs/{run_id}/trace.json: execution trace with all agent events and telemetry.
- GET  /api/status: booleans only for configured keys (never secrets).
"""

import asyncio
import json
import logging
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.events import AgentEvent, event_bus
from app.graph.orchestrator import run_pipeline
from app.report import generate_markdown_report
from app.schemas import ExtraInputs, RunResult
from app.tools.telemetry import Telemetry


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Swarm Runs"])

RUNS_DIR = Path("data/runs")
RUNS_DIR.mkdir(parents=True, exist_ok=True)

MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MB limit


# ---------------------------------------------------------------------------
# Background Execution Helper
# ---------------------------------------------------------------------------

async def _execute_swarm_task(
    run_id: str,
    resume_path: Path,
    marksheet_path: Optional[Path],
    extra_inputs: Optional[ExtraInputs],
    chosen_opp: Optional[str],
):
    """Background task running orchestrator pipeline and persisting artifacts."""
    run_dir = RUNS_DIR / run_id
    events_log = run_dir / "events.jsonl"
    meta_path = run_dir / "meta.json"
    telemetry = Telemetry(run_id=run_id)

    async def _emit_and_persist(event: AgentEvent):
        # 1. Publish to in-memory FIFO EventBus
        await event_bus.publish(event)
        # 2. Append to disk events.jsonl
        try:
            with open(events_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.model_dump(), default=str) + "\n")
        except Exception as e:
            logger.debug("Failed writing event log for %s: %s", run_id, e)

    try:
        result: RunResult = await run_pipeline(
            run_id=run_id,
            resume_path=str(resume_path),
            marksheet_path=str(marksheet_path) if marksheet_path else None,
            extra_inputs=extra_inputs,
            chosen_opportunity_name=chosen_opp,
            emit=_emit_and_persist,
            telemetry=telemetry,
        )

        # Persist result.json
        (run_dir / "result.json").write_text(
            json.dumps(result.model_dump(), indent=2, default=str), encoding="utf-8"
        )

        # Generate and persist report.md
        report_text = generate_markdown_report(result)
        (run_dir / "report.md").write_text(report_text, encoding="utf-8")

        # Update meta.json
        top_rec_name = (
            result.recommendations[0].opportunity.name
            if result.recommendations and result.recommendations[0].opportunity
            else "None identified"
        )
        student_name = (
            result.profile.name
            if result.profile and result.profile.name
            else "Applicant"
        )

        meta = {
            "run_id": run_id,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "status": "completed",
            "student_name": student_name,
            "top_recommendation": top_rec_name,
            "recommendation_count": len(result.recommendations),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # Emit completion terminal event
        await _emit_and_persist(AgentEvent(
            run_id=run_id,
            agent="Orchestrator",
            message="Run completed successfully.",
            level="success",
            payload={"event": "complete", "run_id": run_id},
        ))

    except Exception as exc:
        logger.exception("Swarm background execution failed for %s: %s", run_id, exc)
        meta = {
            "run_id": run_id,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "status": "failed",
            "error": str(exc),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        await _emit_and_persist(AgentEvent(
            run_id=run_id,
            agent="Orchestrator",
            message=f"Run failed: {exc}",
            level="error",
            payload={"event": "error", "error": str(exc)},
        ))


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    resume: UploadFile = File(..., description="Student resume (PDF required)"),
    marksheet: Optional[UploadFile] = File(None, description="Academic marksheet/transcript (PDF optional)"),
    github_url: Optional[str] = Form(None),
    linkedin_url: Optional[str] = Form(None),
    target_countries: Optional[str] = Form(None),
    budget_note: Optional[str] = Form(None),
    degree_level: Optional[str] = Form(None),
    target_degree_level: Optional[str] = Form(None),
    nationality: Optional[str] = Form(None),
    chosen_opportunity: Optional[str] = Form(None),
):
    """Initiate a new scholarship swarm run from uploaded PDFs and form parameters."""
    # 1. Validate resume
    if not resume.filename or not resume.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resume must be a valid PDF file (.pdf extension required).",
        )

    resume_bytes = await resume.read()
    if len(resume_bytes) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Resume file size exceeds maximum limit of {MAX_PDF_BYTES // (1024*1024)} MB.",
        )
    if len(resume_bytes) < 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded resume file is empty or corrupted.",
        )

    # 2. Validate marksheet if provided
    marksheet_bytes = None
    if marksheet and marksheet.filename:
        if not marksheet.filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Marksheet must be a valid PDF file (.pdf extension required).",
            )
        marksheet_bytes = await marksheet.read()
        if len(marksheet_bytes) > MAX_PDF_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Marksheet file size exceeds limit of {MAX_PDF_BYTES // (1024*1024)} MB.",
            )

    # 3. Create run directory
    run_id = f"run_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    run_dir = RUNS_DIR / run_id
    inputs_dir = run_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    # Save inputs
    resume_path = inputs_dir / "resume.pdf"
    resume_path.write_bytes(resume_bytes)

    marksheet_path = None
    if marksheet_bytes:
        marksheet_path = inputs_dir / "marksheet.pdf"
        marksheet_path.write_bytes(marksheet_bytes)

    # Parse extra inputs
    country_list = [c.strip() for c in target_countries.split(",") if c.strip()] if target_countries else []
    extra = ExtraInputs(
        github_url=github_url,
        linkedin_url=linkedin_url,
        target_countries=country_list,
        budget_note=budget_note,
        degree_level=degree_level,
        target_degree_level=target_degree_level,
        nationality=nationality,
    )

    # Write initial meta.json
    meta = {
        "run_id": run_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "status": "running",
        "student_name": "Parsing in progress…",
        "extra_inputs": extra.model_dump(),
        "chosen_opportunity": chosen_opportunity,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # 4. Launch background execution task
    asyncio.create_task(
        _execute_swarm_task(
            run_id=run_id,
            resume_path=resume_path,
            marksheet_path=marksheet_path,
            extra_inputs=extra,
            chosen_opp=chosen_opportunity,
        )
    )

    return {"run_id": run_id, "status": "pending", "message": "Swarm pipeline started."}


@router.get("/runs/{run_id}/events")
async def stream_run_events(run_id: str):
    """Server-Sent Events (SSE) stream delivering historical and live agent events."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    async def event_generator():
        # Step A: Replay existing events from EventBus
        history = event_bus.replay(run_id)
        seen_ids = set()
        for evt in history:
            seen_ids.add(evt.event_id)
            yield {
                "event": "agent_event",
                "data": json.dumps(evt.model_dump(), default=str),
            }

        # Check if already completed
        meta_path = run_dir / "meta.json"
        is_completed = False
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("status") in ("completed", "failed"):
                    is_completed = True
            except Exception:
                pass

        if is_completed:
            yield {
                "event": "complete",
                "data": json.dumps({"run_id": run_id, "status": "completed"}),
            }
            return

        # Step B: Stream live events as they occur
        queue = await event_bus.subscribe(run_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    if event.event_id in seen_ids:
                        continue
                    seen_ids.add(event.event_id)

                    yield {
                        "event": "agent_event",
                        "data": json.dumps(event.model_dump(), default=str),
                    }

                    # Check for completion marker in payload
                    if event.payload and event.payload.get("event") in ("complete", "error"):
                        yield {
                            "event": "complete",
                            "data": json.dumps({"run_id": run_id, "status": "completed"}),
                        }
                        break

                except asyncio.TimeoutError:
                    # Send periodic keep-alive comment so proxies/browsers don't drop the connection
                    yield {"event": "ping", "data": "keep-alive"}

        finally:
            event_bus.unsubscribe(run_id, queue)

    return EventSourceResponse(event_generator())


@router.get("/runs/{run_id}")
async def get_run_details(run_id: str):
    """Retrieve execution status and compiled RunResult when complete."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    meta_file = run_dir / "meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {"status": "unknown"}

    result_file = run_dir / "result.json"
    if result_file.exists():
        try:
            result_data = json.loads(result_file.read_text(encoding="utf-8"))
            return {"status": "completed", "run_id": run_id, "result": result_data}
        except Exception as exc:
            logger.error("Failed parsing result.json for %s: %s", run_id, exc)

    return {"status": meta.get("status", "running"), "run_id": run_id, "meta": meta}


@router.get("/runs")
async def list_runs():
    """List all previous swarm runs stored on disk."""
    runs = []
    if RUNS_DIR.exists():
        for d in RUNS_DIR.iterdir():
            if d.is_dir() and (d / "meta.json").is_file():
                try:
                    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                    runs.append(meta)
                except Exception:
                    continue

    # Sort descending by created_at
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"runs": runs, "count": len(runs)}


@router.post("/runs/{run_id}/rerun", status_code=status.HTTP_202_ACCEPTED)
async def rerun_pipeline(
    run_id: str,
    target_countries: Optional[str] = Form(None),
    budget_note: Optional[str] = Form(None),
    degree_level: Optional[str] = Form(None),
    nationality: Optional[str] = Form(None),
    chosen_opportunity: Optional[str] = Form(None),
):
    """Re-run a previous student run using the saved resume/marksheet with modified inputs."""
    orig_dir = RUNS_DIR / run_id
    if not orig_dir.exists() or not (orig_dir / "inputs" / "resume.pdf").exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Original run '{run_id}' or its uploaded resume file was not found.",
        )

    orig_resume = orig_dir / "inputs" / "resume.pdf"
    orig_marksheet = orig_dir / "inputs" / "marksheet.pdf"

    # Create new run
    new_run_id = f"run_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    new_dir = RUNS_DIR / new_run_id
    new_inputs = new_dir / "inputs"
    new_inputs.mkdir(parents=True, exist_ok=True)

    # Copy files
    shutil.copy(orig_resume, new_inputs / "resume.pdf")
    new_marksheet_path = None
    if orig_marksheet.exists():
        new_marksheet_path = new_inputs / "marksheet.pdf"
        shutil.copy(orig_marksheet, new_marksheet_path)

    # Parse inputs
    country_list = [c.strip() for c in target_countries.split(",") if c.strip()] if target_countries else []
    extra = ExtraInputs(
        target_countries=country_list,
        budget_note=budget_note,
        degree_level=degree_level,
        nationality=nationality,
    )

    meta = {
        "run_id": new_run_id,
        "rerun_of": run_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "status": "running",
        "student_name": "Rerun in progress…",
    }
    (new_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    asyncio.create_task(
        _execute_swarm_task(
            run_id=new_run_id,
            resume_path=new_inputs / "resume.pdf",
            marksheet_path=new_marksheet_path,
            extra_inputs=extra,
            chosen_opp=chosen_opportunity,
        )
    )

    return {"run_id": new_run_id, "status": "pending", "rerun_of": run_id}


@router.get("/runs/{run_id}/report.md")
async def get_run_report(run_id: str):
    """Return the consultant-grade Markdown advisory report."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    report_file = run_dir / "report.md"
    if report_file.exists():
        return PlainTextResponse(report_file.read_text(encoding="utf-8"), media_type="text/markdown")

    # If result.json exists, synthesize on-the-fly
    result_file = run_dir / "result.json"
    if result_file.exists():
        try:
            res_data = json.loads(result_file.read_text(encoding="utf-8"))
            result = RunResult.model_validate(res_data)
            report_text = generate_markdown_report(result)
            return PlainTextResponse(report_text, media_type="text/markdown")
        except Exception as exc:
            logger.error("Failed synthesizing report: %s", exc)

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report is not ready yet.")


@router.get("/runs/{run_id}/trace.json")
async def get_run_trace(run_id: str):
    """Return full chronological audit trace with events and telemetry summary."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    events = []
    events_file = run_dir / "events.jsonl"
    if events_file.exists():
        with open(events_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass

    telemetry_data = {}
    result_file = run_dir / "result.json"
    if result_file.exists():
        try:
            res = json.loads(result_file.read_text(encoding="utf-8"))
            telemetry_data = res.get("run_health", {})
        except Exception:
            pass

    return {"run_id": run_id, "events": events, "telemetry": telemetry_data}


@router.get("/status")
async def get_system_status():
    """Return configuration readiness booleans (never exposes actual secret keys)."""
    return {
        "gemini_configured": bool(settings.gemini_api_key and settings.gemini_api_key.strip()),
        "tavily_configured": bool(settings.tavily_api_key and settings.tavily_api_key.strip()),
        "github_configured": bool(settings.github_token and settings.github_token.strip()),
        "gemini_model": settings.gemini_model,
        "gemini_rpm": settings.gemini_rpm,
    }
