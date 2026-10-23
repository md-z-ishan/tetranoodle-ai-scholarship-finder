"""Phase 8 offline tests: quota resilience, deterministic fallback, partial verifier,
query budget, truthful summary, and metadata updates.

Rules:
- Zero real network calls — all external APIs stubbed with AsyncMock / MagicMock.
- Zero fabricated scholarship data.
- All profiles use neutral fictional names (not Rahul, not Priya).
"""

import asyncio
import json
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai.errors import APIError

from app.agents.profile_analyst import (
    build_deterministic_profile,
    compute_missing_fields_and_confidence,
)
from app.agents.scout import QueryPlanList, SearchQueryPlan, run_scout
from app.agents.verifier import run_verifier
from app.graph.orchestrator import build_executive_summary
from app.schemas import (
    ExtraInputs,
    Opportunity,
    StudentProfile,
    VerifiedOpportunity,
)
from app.tools.llm import (
    DEFAULT_MAX_RETRIES,
    QuotaExhaustedError,
    _compute_backoff,
    _is_quota_error,
    GeminiClient,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile(**kwargs) -> StudentProfile:
    defaults = dict(
        name="Alex Sample",
        degree="Bachelor of Technology in Computer Science",
        target_degree_level="Master",
        field="Computer Science",
        cgpa=8.0,
        cgpa_scale=10.0,
        target_countries=["Germany", "Canada"],
        budget_note="Seeking full funding",
    )
    defaults.update(kwargs)
    return StudentProfile(**defaults)


class MockAPIError(APIError):
    def __init__(self, code: int = 429, msg: str = "RESOURCE_EXHAUSTED"):
        self.code = code
        self.msg = msg
        super().__init__(code, {"error": {"code": code, "message": msg}})

    def __str__(self) -> str:
        return f"{self.code} {self.msg} error"


def _api_error(code: int, msg: str = "rate limit") -> APIError:
    """Construct a minimal APIError subclass instance that can be raised."""
    return MockAPIError(code, msg)


# ---------------------------------------------------------------------------
# 1. Retry budget and bounded backoff
# ---------------------------------------------------------------------------

class TestRetryBudgetAndBackoff:
    """LLM client bounded-retry and backoff behaviour (no real API calls)."""

    def test_is_quota_error_detects_429(self):
        assert _is_quota_error("429 RESOURCE_EXHAUSTED too many requests")

    def test_is_quota_error_detects_503(self):
        assert _is_quota_error("503 UNAVAILABLE service down")

    def test_is_quota_error_false_for_normal_error(self):
        assert not _is_quota_error("400 INVALID_ARGUMENT bad request")

    def test_compute_backoff_grows_with_attempt(self):
        b1 = _compute_backoff(1, "429 error")
        b2 = _compute_backoff(2, "429 error")
        b3 = _compute_backoff(3, "429 error")
        assert b2 > b1
        assert b3 > b2

    def test_compute_backoff_respects_retry_after(self):
        err_str = "429 RESOURCE_EXHAUSTED Retry-After: 60"
        backoff = _compute_backoff(1, err_str)
        # Should be ~60s (with jitter), not the default 2^1
        assert backoff >= 55  # 60 * (1 - JITTER_FACTOR)

    def test_compute_backoff_caps_at_max(self):
        from app.tools.llm import MAX_BACKOFF_SECONDS, JITTER_FACTOR
        backoff = _compute_backoff(10, "503 UNAVAILABLE")
        # Without Retry-After, base = min(2^10, MAX) = MAX; with jitter <= MAX*(1+JITTER)
        assert backoff <= MAX_BACKOFF_SECONDS * (1 + JITTER_FACTOR) + 1

    @pytest.mark.asyncio
    async def test_generate_text_raises_quota_exhausted_when_budget_spent(self):
        """When all retry attempts get 429, QuotaExhaustedError must be raised."""
        client = GeminiClient()

        quota_exc = _api_error(429, "RESOURCE_EXHAUSTED quota")

        with patch.object(client, "_get_client") as mock_get_client:
            mock_genai = MagicMock()
            # Every generate_content call raises 429
            mock_genai.models.generate_content.side_effect = quota_exc
            mock_get_client.return_value = mock_genai

            with patch("app.tools.llm._rate_limiter.acquire", new_callable=AsyncMock):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(QuotaExhaustedError) as exc_info:
                        await client.generate_text("hello", max_retries=2)

        assert "quota exhausted" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_generate_json_raises_quota_exhausted_when_budget_spent(self):
        """generate_json also raises QuotaExhaustedError when both models are unavailable."""
        client = GeminiClient()

        quota_exc = _api_error(503, "UNAVAILABLE high demand")

        with patch.object(client, "_get_client") as mock_get_client:
            mock_genai = MagicMock()
            mock_genai.models.generate_content.side_effect = quota_exc
            mock_get_client.return_value = mock_genai

            with patch("app.tools.llm._rate_limiter.acquire", new_callable=AsyncMock):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(QuotaExhaustedError):
                        await client.generate_json("hello", schema=StudentProfile, max_retries=2)

    @pytest.mark.asyncio
    async def test_fallback_model_activated_on_first_quota_error(self):
        """After first 429, fallback model is activated; subsequent calls use it."""
        client = GeminiClient()
        client._model_name = "primary-model"
        client._fallback_model = "fallback-model"

        call_models: List[str] = []

        def fake_generate(model, contents, config):
            call_models.append(model)
            if model == "primary-model":
                raise _api_error(429, "RESOURCE_EXHAUSTED")
            resp = MagicMock()
            resp.text = "OK"
            resp.usage_metadata = None
            return resp

        with patch.object(client, "_get_client") as mock_get_client:
            mock_genai = MagicMock()
            mock_genai.models.generate_content.side_effect = fake_generate
            mock_get_client.return_value = mock_genai

            with patch("app.tools.llm._rate_limiter.acquire", new_callable=AsyncMock):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with patch("app.tools.llm._read_cache", return_value=None):
                        result = await client.generate_text("hello-fallback-test", max_retries=3)

        assert result == "OK"
        assert "primary-model" in call_models
        assert "fallback-model" in call_models

    @pytest.mark.asyncio
    async def test_both_models_exhausted_raises_quota_error(self):
        """When both primary AND fallback return 429, QuotaExhaustedError is raised."""
        client = GeminiClient()
        client._model_name = "primary-model"
        client._fallback_model = "fallback-model"

        def raise_quota(model, contents, config):
            raise _api_error(429, "RESOURCE_EXHAUSTED")

        with patch.object(client, "_get_client") as mock_get_client:
            mock_genai = MagicMock()
            mock_genai.models.generate_content.side_effect = raise_quota
            mock_get_client.return_value = mock_genai

            with patch("app.tools.llm._rate_limiter.acquire", new_callable=AsyncMock):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with patch("app.tools.llm._read_cache", return_value=None):
                        with pytest.raises(QuotaExhaustedError):
                            await client.generate_text("hello-unique-prompt", max_retries=3)


# ---------------------------------------------------------------------------
# 2. Deterministic Profile Analyst fallback
# ---------------------------------------------------------------------------

class TestDeterministicProfileFallback:
    """build_deterministic_profile() extracts fields without LLM."""

    RESUME_TEXT = (
        "Alex Sample\n"
        "Bangalore, India | alex.sample@example.com\n"
        "Bachelor of Technology in Computer Science (Final Year, 2026)\n"
        "Sample Institute\n"
        "CGPA: 8.4 / 10.0\n"
        "Skills: Python, PyTorch, FastAPI\n"
        "Project: Semantic Retrieval Engine\n"
        "Interests: Fully funded Master programs in Germany and Canada.\n"
        "indian nationality\n"
    )
    MARKSHEET_TEXT = (
        "OFFICIAL TRANSCRIPT\n"
        "Student: Alex Sample\n"
        "CGPA: 8.4 / 10.0\n"
        "Program: B.Tech Computer Science\n"
    )

    def test_name_extracted_from_resume(self):
        p = build_deterministic_profile(self.RESUME_TEXT, self.MARKSHEET_TEXT)
        assert p.name == "Alex Sample"

    def test_cgpa_extracted_correctly(self):
        p = build_deterministic_profile(self.RESUME_TEXT, self.MARKSHEET_TEXT)
        assert p.cgpa == 8.4
        assert p.cgpa_scale == 10.0

    def test_degree_keyword_matched(self):
        p = build_deterministic_profile(self.RESUME_TEXT, self.MARKSHEET_TEXT)
        assert p.degree is not None
        assert "Bachelor" in p.degree or "B.Tech" in p.degree

    def test_field_keyword_matched(self):
        p = build_deterministic_profile(self.RESUME_TEXT, self.MARKSHEET_TEXT)
        assert p.field is not None
        assert "Computer Science" in p.field

    def test_nationality_extracted(self):
        p = build_deterministic_profile(self.RESUME_TEXT, self.MARKSHEET_TEXT)
        assert p.nationality is not None
        assert "Indian" in p.nationality

    def test_source_notes_marked_as_fallback(self):
        p = build_deterministic_profile(self.RESUME_TEXT, self.MARKSHEET_TEXT)
        assert any("DETERMINISTIC FALLBACK" in n for n in p.source_notes)

    def test_extra_inputs_countries_applied(self):
        extra = ExtraInputs(target_countries=["Netherlands", "Ireland"])
        p = build_deterministic_profile(self.RESUME_TEXT, self.MARKSHEET_TEXT, extra_inputs=extra)
        assert "Netherlands" in p.target_countries
        assert "Ireland" in p.target_countries

    def test_no_invented_values_when_absent(self):
        """Empty text → most fields null, never fabricated."""
        p = build_deterministic_profile("", "")
        assert p.name is None
        assert p.cgpa is None
        assert p.field is None

    def test_target_degree_derived_from_bachelor(self):
        p = build_deterministic_profile(self.RESUME_TEXT, self.MARKSHEET_TEXT)
        assert p.target_degree_level == "Master"

    @pytest.mark.asyncio
    async def test_run_profile_analyst_falls_back_on_quota_exhausted(self):
        """When LLM raises QuotaExhaustedError, run_profile_analyst uses deterministic fallback."""
        resume_bytes = self.RESUME_TEXT.encode()
        marksheet_bytes = self.MARKSHEET_TEXT.encode()

        events_captured = []

        async def capture_emit(event):
            events_captured.append(event)

        with patch("app.agents.profile_analyst.llm_client") as mock_llm:
            mock_llm.generate_json = AsyncMock(
                side_effect=QuotaExhaustedError("Quota exhausted for test")
            )
            with patch("app.agents.profile_analyst.pdf_tool") as mock_pdf:
                mock_pdf.extract_pdf_text.return_value = MagicMock(
                    text=self.RESUME_TEXT, likely_scanned=False
                )
                with patch("app.agents.profile_analyst.github_tool") as mock_gh:
                    mock_gh.fetch_github_profile = AsyncMock(
                        return_value=MagicMock(error="Not found", repos=[])
                    )
                    from app.agents.profile_analyst import run_profile_analyst
                    profile = await run_profile_analyst(
                        resume_source=resume_bytes,
                        marksheet_source=marksheet_bytes,
                        run_id="test-fallback",
                        emit=capture_emit,
                    )

        # Profile returned, not None
        assert profile is not None
        # Marked as deterministic fallback
        assert any("DETERMINISTIC FALLBACK" in n for n in profile.source_notes)
        # A warn event was emitted
        warn_events = [e for e in events_captured if e.level == "warn"]
        assert any("quota exhausted" in e.message.lower() or "deterministic" in e.message.lower()
                   for e in warn_events)


# ---------------------------------------------------------------------------
# 3. Verifier partial timeout — candidates preserved as unverified
# ---------------------------------------------------------------------------

class TestVerifierPartialTimeout:
    """Verifier: per-candidate 60s timeout doesn't crash the run."""

    @pytest.mark.asyncio
    async def test_timed_out_candidate_returned_as_unverified(self):
        """A candidate that times out (asyncio.wait_for raises TimeoutError) → unverified status."""
        profile = _make_profile()
        candidates = [
            Opportunity(name="Slow Scholarship", url="https://slow.example.edu/scholarship"),
        ]

        async def _slow_verify(*args, **kwargs):
            await asyncio.sleep(1000)  # simulates timeout
            return MagicMock()

        with patch("app.agents.verifier._verify_one", side_effect=_slow_verify):
            # Set very short timeout to trigger TimeoutError immediately
            with patch("app.agents.verifier.asyncio.wait_for", side_effect=asyncio.TimeoutError):
                results = await run_verifier(
                    candidates=candidates,
                    profile=profile,
                    run_id="test-timeout",
                )

        assert len(results) == 1
        assert results[0].status == "unverified"
        assert "timed out" in (results[0].rejection_reason or "").lower()

    @pytest.mark.asyncio
    async def test_one_failed_candidate_does_not_crash_others(self):
        """One candidate error → that candidate unverified, others still processed."""
        profile = _make_profile()
        candidates = [
            Opportunity(name="Good Scholarship", url="https://good.example.edu/"),
            Opportunity(name="Bad Scholarship", url="https://bad.example.edu/"),
        ]
        call_count = 0

        async def _selective_fail(candidate, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if "bad" in (candidate.url or ""):
                raise RuntimeError("Unexpected verifier error")
            return VerifiedOpportunity(
                name=candidate.name,
                url=candidate.url,
                status="partial",
                link_alive=True,
                fetched_at="2026-01-01T00:00:00Z",
            )

        with patch("app.agents.verifier._verify_one", side_effect=_selective_fail):
            results = await run_verifier(
                candidates=candidates,
                profile=profile,
                run_id="test-partial",
            )

        assert len(results) == 2
        statuses = {r.name: r.status for r in results}
        assert statuses["Good Scholarship"] == "partial"
        assert statuses["Bad Scholarship"] == "unverified"

    @pytest.mark.asyncio
    async def test_partial_verification_preserves_all_candidates(self):
        """Even if none verify, all candidates come back (as unverified), not dropped."""
        profile = _make_profile()
        candidates = [
            Opportunity(name=f"Scholarship {i}", url=f"https://example{i}.edu/")
            for i in range(5)
        ]

        async def _always_fail(candidate, *args, **kwargs):
            raise RuntimeError("Simulated failure")

        with patch("app.agents.verifier._verify_one", side_effect=_always_fail):
            results = await run_verifier(candidates=candidates, profile=profile, run_id="test")

        assert len(results) == 5
        assert all(r.status == "unverified" for r in results)


# ---------------------------------------------------------------------------
# 4. Scout query budget
# ---------------------------------------------------------------------------

class TestScoutQueryBudget:
    """Query budget trims excess queries while preserving country coverage."""

    def _make_over_budget_plan(self, n_total: int = 25) -> QueryPlanList:
        queries = []
        for i in range(n_total):
            country = "Germany" if i < 4 else ("Canada" if i < 8 else None)
            queries.append(SearchQueryPlan(
                query=f"sample query {i}",
                target_country=country,
                intent=f"intent {i}",
            ))
        return QueryPlanList(queries=queries)

    @pytest.mark.asyncio
    async def test_query_budget_trims_to_max(self):
        """When plan has 25 queries and budget=20, Scout runs at most 20."""
        profile = _make_profile()
        over_budget_plan = self._make_over_budget_plan(25)
        executed_queries = []

        async def fake_search(query, max_results=3, telemetry=None):
            executed_queries.append(query)
            return []

        with patch("app.agents.scout.plan_queries", return_value=over_budget_plan):
            with patch("app.agents.scout.search_tool") as mock_search:
                mock_search.search = AsyncMock(side_effect=fake_search)
                with patch("app.agents.scout.triage_hits_to_candidates", return_value=[]):
                    await run_scout(profile=profile, max_queries=20, run_id="test-budget")

        assert len(executed_queries) <= 20

    @pytest.mark.asyncio
    async def test_query_budget_preserves_country_coverage(self):
        """Country-targeted queries are never dropped even when budget is tight."""
        profile = _make_profile(target_countries=["Germany", "Canada"])
        over_budget_plan = self._make_over_budget_plan(25)
        # 4 Germany + 4 Canada = 8 country queries in plan
        country_q_texts = {q.query for q in over_budget_plan.queries if q.target_country in ["Germany", "Canada"]}

        executed_queries = []

        async def fake_search(query, max_results=3, telemetry=None):
            executed_queries.append(query)
            return []

        with patch("app.agents.scout.plan_queries", return_value=over_budget_plan):
            with patch("app.agents.scout.search_tool") as mock_search:
                mock_search.search = AsyncMock(side_effect=fake_search)
                with patch("app.agents.scout.triage_hits_to_candidates", return_value=[]):
                    await run_scout(profile=profile, max_queries=10, run_id="test-coverage")

        executed_set = set(executed_queries)
        for cq in country_q_texts:
            assert cq in executed_set, f"Country query '{cq}' was dropped despite budget preservation rule"

    @pytest.mark.asyncio
    async def test_query_budget_zero_disables_limit(self):
        """Budget=0 means no limit; all planned queries execute."""
        profile = _make_profile()
        over_budget_plan = self._make_over_budget_plan(25)
        executed_queries = []

        async def fake_search(query, max_results=3, telemetry=None):
            executed_queries.append(query)
            return []

        with patch("app.agents.scout.plan_queries", return_value=over_budget_plan):
            with patch("app.agents.scout.search_tool") as mock_search:
                mock_search.search = AsyncMock(side_effect=fake_search)
                with patch("app.agents.scout.triage_hits_to_candidates", return_value=[]):
                    await run_scout(profile=profile, target_count=0, max_queries=0, run_id="test-no-budget")

        assert len(executed_queries) == 25


# ---------------------------------------------------------------------------
# 5. Truthful executive summary
# ---------------------------------------------------------------------------

class TestTruthfulExecutiveSummary:
    """build_executive_summary() reflects actual pipeline state."""

    def test_full_success_mentions_profile_and_recs(self):
        state = {
            "profile": {"name": "Test Student", "source_notes": []},
            "candidates": [{"name": "Scholarship A"}] * 5,
            "verified": [{"status": "verified"}, {"status": "partial"}, {"status": "rejected"}],
            "recommendations": [{"name": "Scholarship A"}],
            "draft": {"sop_text": "Some text"},
            "warnings": [],
        }
        summary = build_executive_summary(state)
        assert "fully synthesized" in summary.lower()
        assert "5 scholarship" in summary.lower()
        assert "1 viable recommendation" in summary.lower()
        assert "statement of purpose" in summary.lower()

    def test_quota_exhausted_fallback_mentioned(self):
        state = {
            "profile": {
                "name": "Test Student",
                "source_notes": ["[DETERMINISTIC FALLBACK] LLM synthesis unavailable"],
            },
            "candidates": [],
            "verified": [],
            "recommendations": [],
            "draft": None,
            "warnings": [],
        }
        summary = build_executive_summary(state)
        assert "deterministic pattern-matching" in summary.lower()
        assert "quota exhaustion" in summary.lower() or "quota" in summary.lower()

    def test_incomplete_verification_mentioned(self):
        state = {
            "profile": {"name": "Test Student", "source_notes": []},
            "candidates": [{"name": f"C{i}"} for i in range(6)],
            "verified": [],
            "recommendations": [],
            "draft": None,
            "warnings": [],
        }
        summary = build_executive_summary(state)
        assert "verification was not completed" in summary.lower()
        assert "6" in summary

    def test_zero_recommendations_not_claimed_as_success(self):
        state = {
            "profile": {"name": "Test Student", "source_notes": []},
            "candidates": [{"name": "C1"}],
            "verified": [{"status": "rejected"}],
            "recommendations": [],
            "draft": None,
            "warnings": [],
        }
        summary = build_executive_summary(state)
        assert "no ranked recommendation" in summary.lower()

    def test_warnings_included_in_summary(self):
        state = {
            "profile": {"name": "Test Student", "source_notes": []},
            "candidates": [],
            "verified": [],
            "recommendations": [],
            "draft": None,
            "warnings": ["API quota exhausted mid-run"],
        }
        summary = build_executive_summary(state)
        assert "api quota exhausted" in summary.lower()

    def test_empty_state_does_not_crash(self):
        """build_executive_summary handles a fully empty state dict gracefully."""
        summary = build_executive_summary({})
        assert isinstance(summary, str)
        assert len(summary) > 0


# ---------------------------------------------------------------------------
# 6. Metadata and report behavior
# ---------------------------------------------------------------------------

class TestMetadataAndReport:
    """verify report generation handles incomplete pipeline runs."""

    def test_report_generation_with_zero_recommendations(self):
        """generate_markdown_report doesn't crash when recommendations is empty."""
        from app.report import generate_markdown_report
        from app.schemas import RunResult

        result = RunResult(
            run_id="test-zero-recs",
            profile=_make_profile(),
            candidates=[Opportunity(name="Test Opp", url="https://test.edu/")],
            verified=[],
            recommendations=[],
            executive_summary="No recommendations produced due to quota exhaustion.",
        )
        report = generate_markdown_report(result)
        assert "No recommendations produced" in report or "test-zero-recs" in report
        assert "fabricat" not in report.lower()

    def test_report_shows_unverified_candidates(self):
        """Report includes unverified candidates in the audit log when present."""
        from app.report import generate_markdown_report
        from app.schemas import RunResult

        verified_unv = VerifiedOpportunity(
            name="Unverified Scholarship",
            url="https://unverified.edu/",
            status="unverified",
            rejection_reason="Timed out",
            link_alive=False,
            fetched_at="2026-01-01T00:00:00Z",
        )
        result = RunResult(
            run_id="test-unverified",
            profile=_make_profile(),
            candidates=[],
            verified=[verified_unv],
            recommendations=[],
            executive_summary="Verification incomplete.",
        )
        report = generate_markdown_report(result)
        # Should appear in the audit log section
        assert "unverified" in report.lower() or "Unverified Scholarship" in report
