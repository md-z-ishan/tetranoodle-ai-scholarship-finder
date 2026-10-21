"""Offline unit tests for Phase 5 (Quality fixes, Writer, Critic, LangGraph Orchestrator, API, Cache & Rate Limiter).

Zero external network calls; all external APIs (LLM, search, fetch) are stubbed with neutral placeholders.
"""

import asyncio
import io
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.agents.critic import (
    CriticLLMResponse,
    audit_draft_grounding,
    run_critic,
    run_writer_critic_loop,
)
from app.agents.ranker import compute_score, run_ranker
from app.agents.verifier import ExtractedOpportunityData, EvidencedString, run_verifier
from app.agents.writer import GeneratedDraftResponse, run_writer
from app.main import app
from app.report import generate_markdown_report
from app.schemas import (
    ApplicationDraft,
    Evidence,
    ExtraInputs,
    Opportunity,
    Project,
    Recommendation,
    RunResult,
    StudentProfile,
    VerifiedOpportunity,
)
from app.tools.llm import TokenBucketRateLimiter, _get_cache_path, _read_cache, _write_cache


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

def _make_sample_profile() -> StudentProfile:
    return StudentProfile(
        name="Test Student",
        degree="Bachelor of Technology in Computer Science",
        target_degree_level="Master",
        field="Computer Science",
        nationality="Indian",
        cgpa=8.5,
        cgpa_scale=10.0,
        graduation_year=2025,
        skills=["Python", "FastAPI", "PyTorch"],
        projects=[
            Project(name="Neural Search Engine", description="Built neural search with vector index", tech=["Python", "PyTorch"]),
        ],
        target_countries=["Canada", "Germany"],
        budget_note="Seeking tuition waiver or full funding",
    )


def _make_verified_opp(
    *,
    name="University Excellence Award",
    status="verified",
    deadline_status="open",
    source_tier="official",
    degree_levels=None,
    nationality_notes=None,
    eligibility_met=None,
    eligibility_gaps=None,
    confidence=0.8,
) -> VerifiedOpportunity:
    return VerifiedOpportunity(
        name=name,
        organization="Example University",
        country="Canada",
        url="https://example-uni.edu/award",
        status=status,
        deadline_status=deadline_status,
        source_tier=source_tier,
        confidence=confidence,
        funding_type="full",
        degree_levels=degree_levels or ["Master"],
        nationality_notes=nationality_notes,
        eligibility_met=eligibility_met or ["Open to international applicants"],
        eligibility_gaps=eligibility_gaps or [],
        evidence=[Evidence(url="https://example-uni.edu/award", fetched_at="2026-01-01T00:00:00Z", snippet="Full funding available")],
    )


# ---------------------------------------------------------------------------
# 1. Degree Level & Nationality Hard Filters (Verifier & Quality)
# ---------------------------------------------------------------------------

class TestVerifierHardFilters:
    @pytest.mark.asyncio
    async def test_degree_level_mismatch_rejected(self):
        """Undergraduate-only scholarship rejected when target is Master's."""
        profile = _make_sample_profile()  # target_degree_level = "Master"
        opp = Opportunity(
            name="High School Diploma Merit Award",
            url="https://example-college.edu/hs-award",
            country="Canada",
        )

        with patch("app.agents.verifier.fetch_tool.fetch_page", new_callable=AsyncMock) as mock_fetch, \
             patch("app.agents.verifier.search_tool.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = []
            # Page text clearly stating polytechnic diploma / secondary school (length > 100)
            mock_fetch.return_value = MagicMock(
                text="Award for polytechnic diploma and secondary school students. Minimum GPA 3.0 required. Detailed terms and conditions for high school and polytechnic diploma applicants.",
                fetched_via="direct",
            )
            verified = await run_verifier([opp], profile)
            assert len(verified) == 1
            assert verified[0].status == "rejected"
            assert "incompatible" in (verified[0].rejection_reason or "").lower()

    @pytest.mark.asyncio
    async def test_citizenship_restriction_rejected(self):
        """Citizens-only award rejected when student nationality is Indian."""
        profile = _make_sample_profile()
        profile.nationality = "Indian"
        opp = Opportunity(
            name="Singapore Armed Forces Scholarship",
            url="https://example-gov.sg/award",
            country="Singapore",
        )

        with patch("app.agents.verifier.fetch_tool.fetch_page", new_callable=AsyncMock) as mock_fetch, \
             patch("app.agents.verifier.search_tool.search", new_callable=AsyncMock) as mock_search, \
             patch("app.agents.verifier.llm_client.generate_json", new_callable=AsyncMock) as mock_llm:
            mock_search.return_value = []
            mock_fetch.return_value = MagicMock(
                text="Award details: applicants must be Singapore citizen only. Full funding provided. Length is padded to exceed one hundred characters.",
                fetched_via="direct",
            )
            mock_llm.return_value = ExtractedOpportunityData(
                eligibility_criteria=[EvidencedString(value="Singapore citizen only", evidence_snippet="Singapore citizen only")],
                funding_details=EvidencedString(value="Full funding", evidence_snippet="Full funding"),
                deadline_text=EvidencedString(value="December 31, 2026", evidence_snippet="December 31, 2026"),
            )
            verified = await run_verifier([opp], profile)
            assert len(verified) == 1
            assert verified[0].status == "rejected"


# ---------------------------------------------------------------------------
# 2. Ranker Viability Rule
# ---------------------------------------------------------------------------

class TestRankerViabilityRule:
    @pytest.mark.asyncio
    async def test_only_viable_candidates_enter_top5(self):
        """Unverified, rejected, expired, or gap-blocked candidates never enter top 5."""
        profile = _make_sample_profile()
        verified_list = [
            _make_verified_opp(name="Good Opp 1", status="verified", deadline_status="open"),
            _make_verified_opp(name="Good Opp 2", status="partial", deadline_status="open"),
            _make_verified_opp(name="Rejected Expired", status="rejected", deadline_status="passed"),
            _make_verified_opp(name="Unverified Opp", status="unverified", confidence=0.0),
            _make_verified_opp(name="Gap Blocked", status="partial", eligibility_gaps=["CGPA requirement not met"]),
        ]

        with patch("app.agents.ranker.llm_client.generate_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = MagicMock(
                explanations=[MagicMock(why_it_fits="Good fit", how_to_apply=[], still_needed=[])],
                executive_summary="Summary",
            )
            recs, summary, could_not_verify, needs_student = await run_ranker(verified_list, profile)

            # Only Good Opp 1 and Good Opp 2 may be in recommendations
            assert len(recs) == 2
            rec_names = [r.opportunity.name for r in recs]
            assert "Good Opp 1" in rec_names
            assert "Good Opp 2" in rec_names
            assert "Rejected Expired" not in rec_names
            assert "Unverified Opp" not in rec_names
            assert "Gap Blocked" not in rec_names

            # Excluded items must be recorded in could_not_verify
            assert len(could_not_verify) == 3


# ---------------------------------------------------------------------------
# 3. Writer Grounding & Fields Audit
# ---------------------------------------------------------------------------

class TestWriterGrounding:
    @pytest.mark.asyncio
    async def test_writer_produces_draft_with_profile_fields(self):
        """Writer draft returns based_on_profile_fields and header label."""
        profile = _make_sample_profile()
        opp = _make_verified_opp()

        with patch("app.agents.writer.llm_client.generate_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = GeneratedDraftResponse(
                text="[Draft only - not submitted]\n\nI am applying based on my B.Tech degree and Neural Search Engine project.",
                based_on_profile_fields=["degree", "projects[0]"],
            )
            draft = await run_writer(profile, opp)
            assert draft.label == "Draft only - not submitted"
            assert "degree" in draft.based_on_profile_fields
            assert "Neural Search Engine" in draft.text

    def test_audit_draft_grounding_catches_invented_number(self):
        """Deterministic grounding audit catches fake metrics not in profile."""
        profile = _make_sample_profile()
        opp = _make_verified_opp()

        # Draft contains fake metric "99.8" percentile
        fake_draft = "I achieved an outstanding 99.8 percentile in national examinations."
        issues = audit_draft_grounding(fake_draft, profile, opp)
        assert len(issues) > 0
        assert any("99.8" in iss for iss in issues)


# ---------------------------------------------------------------------------
# 4. Critic Revision Loop (Max 2 Rounds)
# ---------------------------------------------------------------------------

class TestCriticRevisionLoop:
    @pytest.mark.asyncio
    async def test_revision_loop_stops_at_max_revisions(self):
        """Revision loop iterates when critic requests revisions and strictly halts at 2 rounds."""
        profile = _make_sample_profile()
        opp = _make_verified_opp()

        async def fake_generate_json(**kwargs):
            schema = kwargs.get("schema")
            if schema == GeneratedDraftResponse:
                return GeneratedDraftResponse(
                    text="[Draft only - not submitted]\n\nDraft content...",
                    based_on_profile_fields=["degree"],
                )
            if schema == CriticLLMResponse:
                return CriticLLMResponse(
                    verdict="revise",
                    issues=["Tone needs improvement", "Expand project details"],
                    rationale="Needs revision",
                )
            return MagicMock()

        with patch("app.tools.llm.llm_client.generate_json", side_effect=fake_generate_json):
            draft = await run_writer_critic_loop(profile, opp, max_revisions=2)
            # Must have stopped at revision_count == 2
            assert draft.revision_count == 2
            assert len(draft.critic_notes) > 0


# ---------------------------------------------------------------------------
# 5. Rate Limiter and Disk Cache
# ---------------------------------------------------------------------------

class TestRateLimiterAndCache:
    @pytest.mark.asyncio
    async def test_token_bucket_spacing(self):
        """Rate limiter enforces time spacing between requests."""
        limiter = TokenBucketRateLimiter(rpm=60)  # 1 req/sec
        t0 = asyncio.get_event_loop().time()
        await limiter.acquire()
        await limiter.acquire()
        elapsed = asyncio.get_event_loop().time() - t0
        assert elapsed >= 0.8  # Enforced ~1 second interval

    def test_disk_cache_roundtrip(self, tmp_path):
        """Disk cache correctly hashes, writes, and reads cached payloads."""
        with patch("app.tools.llm.CACHE_DIR", tmp_path):
            cache_p = _get_cache_path("test-model", "Test prompt", "System inst", "{}")
            assert not cache_p.exists()

            _write_cache(cache_p, '{"test_key": "test_val"}')
            assert cache_p.exists()

            read_back = _read_cache(cache_p)
            assert read_back == '{"test_key": "test_val"}'


# ---------------------------------------------------------------------------
# 6. Report Formatter
# ---------------------------------------------------------------------------

class TestReportFormatter:
    def test_generate_markdown_report_structure(self):
        """Markdown report includes executive summary, top recs, SOP draft, and telemetry."""
        profile = _make_sample_profile()
        opp = _make_verified_opp()
        rec = Recommendation(rank=1, opportunity=opp, score=85.0, why_it_fits="High alignment")
        result = RunResult(
            run_id="run-test-123",
            profile=profile,
            recommendations=[rec],
            verified=[opp],
            executive_summary="Candidate has strong profile for Canadian opportunities.",
            draft=ApplicationDraft(text="[Draft only - not submitted]\nSOP text"),
            run_health={"counters": {"llm_calls": 5, "llm_cache_hits": 2}},
        )

        md = generate_markdown_report(result)
        assert "# Scholarship Advisory Report: run-test-123" in md
        assert "## 2. Executive Summary" in md
        assert "## 3. Top Ranked Recommendations" in md
        assert "## 7. Tailored Statement of Purpose (SOP)" in md
        assert "## 8. Run Telemetry & Execution Health" in md


# ---------------------------------------------------------------------------
# 7. FastAPI API Endpoints (Lifecycle Test)
# ---------------------------------------------------------------------------

class TestFastAPIRoutes:
    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_status_endpoint(self, client):
        """GET /api/status returns booleans only, never secret strings."""
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("gemini_configured"), bool)
        assert isinstance(data.get("tavily_configured"), bool)
        assert isinstance(data.get("github_configured"), bool)
        # Ensure no secret strings leaked
        for k, v in data.items():
            if isinstance(v, str):
                assert not v.startswith("AIza") and not v.startswith("tvly-")

    def test_create_run_bad_file_rejected(self, client):
        """POST /api/runs rejects non-PDF file."""
        resp = client.post(
            "/api/runs",
            files={"resume": ("test.txt", b"plain text content", "text/plain")},
        )
        assert resp.status_code == 400
        assert "PDF" in resp.json()["detail"]

    def test_create_run_valid_pdf_starts(self, client, tmp_path):
        """POST /api/runs starts a valid run and returns run_id."""
        with patch("app.api.routes_run.RUNS_DIR", tmp_path), \
             patch("app.api.routes_run.asyncio.create_task") as mock_task:

            fake_pdf = b"%PDF-1.4 " + b"0" * 200
            resp = client.post(
                "/api/runs",
                files={"resume": ("sample_resume.pdf", fake_pdf, "application/pdf")},
                data={"target_countries": "Canada,Germany", "nationality": "Indian"},
            )
            assert resp.status_code == 202
            data = resp.json()
            assert "run_id" in data
            assert data["status"] == "pending"
            assert mock_task.called
