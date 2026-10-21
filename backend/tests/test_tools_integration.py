"""Live integration tests for real APIs (skipped automatically when keys are missing).

Tests:
- Gemini structured JSON round-trip
- Tavily search live query (falls back to DuckDuckGo seamlessly)
- Fetching a live public webpage (e.g. https://www.google.com or academic domain)
- Public GitHub profile retrieval (e.g. torvalds or octocat)
"""

import pytest
from pydantic import BaseModel
from app.config import settings
from app.tools.fetch_tool import fetch_tool
from app.tools.github_tool import github_tool
from app.tools.llm import llm_client
from app.tools.search_tool import search_tool


class SampleExtraction(BaseModel):
    category: str
    target_degree: str
    is_stem: bool


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_gemini_structured_json():
    """Verify Gemini returns structured Pydantic object when GEMINI_API_KEY is configured."""
    if not settings.gemini_api_key or not settings.gemini_api_key.strip():
        pytest.skip("GEMINI_API_KEY not configured in environment or backend/.env")

    prompt = "Extract details: Master of Science in Robotics and Artificial Intelligence."
    result = await llm_client.generate_json(prompt, schema=SampleExtraction)
    assert isinstance(result, SampleExtraction)
    assert result.is_stem is True
    assert "master" in result.target_degree.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_search_query():
    """Verify live search returns non-empty hits (via Tavily or DuckDuckGo)."""
    hits = await search_tool.search("international graduate scholarships computer science", max_results=5)
    assert len(hits) > 0
    assert hits[0].url.startswith("http")
    assert hits[0].title != ""


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_web_fetch():
    """Verify live page fetcher retrieves and extracts text from a reliable public portal."""
    page = await fetch_tool.fetch_page("https://example.com")
    assert page.link_alive is True
    assert page.status_code == 200
    assert page.text is not None
    assert "Example Domain" in page.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_github_enrichment():
    """Verify GitHub tool retrieves public developer profile and repos."""
    profile = await github_tool.fetch_github_profile("octocat")
    assert profile.username.lower() == "octocat"
    assert profile.error is None
    assert profile.public_repos >= 0
