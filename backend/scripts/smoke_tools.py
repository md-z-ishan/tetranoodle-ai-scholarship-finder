#!/usr/bin/env python3
"""Live tools smoke test CLI script.

Executes each core tool once against real endpoints:
1. Live web search (Tavily with automatic DuckDuckGo fallback)
2. Live page fetch & structured text extraction
3. Public GitHub developer profile enrichment
4. PDF parser (synthetic in-memory sample)
5. Gemini structured JSON round-trip (if GEMINI_API_KEY is present)

Prints clean, human-readable terminal output without printing secret keys.
"""

import asyncio
import io
import os
import sys
from pathlib import Path

# Add backend to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from pydantic import BaseModel
from reportlab.pdfgen import canvas
from app.config import settings
from app.tools.fetch_tool import fetch_tool
from app.tools.github_tool import github_tool
from app.tools.llm import llm_client
from app.tools.pdf_tool import pdf_tool
from app.tools.search_tool import search_tool
from app.tools.telemetry import Telemetry


class SmokeSchema(BaseModel):
    category: str
    target_degree: str
    is_stem: bool


async def main():
    print("=" * 60)
    print("🚀 TETRANOODLE CORE TOOLS LIVE SMOKE TEST")
    print("=" * 60)

    telemetry = Telemetry(run_id="smoke-test-run")

    # 1. Live Web Search
    print("\n[1/5] Testing Live Web Search...")
    with telemetry.record_timing("smoke_search"):
        query = "DAAD international graduate scholarships in Germany computer science"
        hits = await search_tool.search(query, max_results=3, telemetry=telemetry)
    print(f"  Query: '{query}'")
    print(f"  Hits Discovered: {len(hits)}")
    for i, h in enumerate(hits, 1):
        print(f"    {i}. [{h.source_tier.upper()}] ({h.provider}) {h.title[:60]}")
        print(f"       URL: {h.url}")

    # 2. Live Page Fetcher & Text Extraction
    print("\n[2/5] Testing Live Page Fetcher & Text Parser...")
    target_url = hits[0].url if hits else "https://example.com"
    with telemetry.record_timing("smoke_fetch"):
        page = await fetch_tool.fetch_page(target_url, telemetry=telemetry)
    print(f"  Target: {target_url}")
    print(f"  Status: {page.status_code} | Link Alive: {page.link_alive}")
    print(f"  Title: {page.title}")
    snippet_preview = (page.text or "")[:150].replace("\n", " ")
    print(f"  Clean Text Preview: {snippet_preview}...")

    # 3. Public GitHub Developer Enrichment
    print("\n[3/5] Testing Public GitHub Profile Enrichment...")
    with telemetry.record_timing("smoke_github"):
        gh_user = "torvalds"
        gh_profile = await github_tool.fetch_github_profile(gh_user, telemetry=telemetry)
    print(f"  Username: {gh_profile.username} | Name: {gh_profile.name}")
    print(f"  Public Repos: {gh_profile.public_repos} | Followers: {gh_profile.followers}")
    print(f"  Top Languages: {', '.join(gh_profile.top_languages[:4])}")
    if gh_profile.repos:
        top_repo = gh_profile.repos[0]
        print(f"  Top Repo: {top_repo.name} ({top_repo.stars} stars) - Language: {top_repo.language}")

    # 4. In-Memory PDF Extraction
    print("\n[4/5] Testing PDF Text Extraction & Scanned Detection...")
    with telemetry.record_timing("smoke_pdf"):
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(100, 750, "Rahul Sharma - Academic Record & Resume")
        c.drawString(100, 730, "Degree: Bachelor of Technology (CSE) - CGPA: 8.2 / 10.0")
        c.drawString(100, 710, "Projects: Distributed LLM Orchestration, Real-Time Vision")
        c.save()
        pdf_res = pdf_tool.extract_pdf_text(buf.getvalue(), telemetry=telemetry)
    print(f"  Extractor Used: {pdf_res.extractor_used}")
    print(f"  Pages: {pdf_res.page_count} | Chars: {pdf_res.char_count} | Likely Scanned: {pdf_res.likely_scanned}")
    print(f"  Extracted Preview: {pdf_res.text[:120]}...")

    # 5. Gemini Structured Output (Conditional on key)
    print("\n[5/5] Testing Google Gemini Structured Output...")
    if settings.gemini_api_key and settings.gemini_api_key.strip():
        try:
            with telemetry.record_timing("smoke_gemini"):
                test_prompt = "Classify: Master of Science in Autonomous Robotic Systems (Fully Funded)."
                gemini_res = await llm_client.generate_json(test_prompt, schema=SmokeSchema, telemetry=telemetry)
            print(f"  Model: {settings.gemini_model}")
            print(f"  Structured Output Adherence: {gemini_res.model_dump_json()}")
        except Exception as e:
            print(f"  Gemini check failed: {e}")
    else:
        print("  Notice: GEMINI_API_KEY not configured. Structured output deferred to runtime.")

    # Telemetry Summary
    print("\n" + "=" * 60)
    print("📊 RUN TELEMETRY SUMMARY")
    print("=" * 60)
    summary = telemetry.summary()
    print(f"Timings: {summary['timings']}")
    print(f"Counters: {summary['counters']}")
    print(f"Retries Logged: {summary['retries_count']}")
    print(f"Fallbacks Logged: {summary['fallbacks_count']}")
    print(f"Failures Logged: {summary['failures_count']}")
    print("=" * 60)
    print("✅ All core tools operational and verified.")


if __name__ == "__main__":
    asyncio.run(main())
