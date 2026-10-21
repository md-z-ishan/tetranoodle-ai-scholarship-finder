#!/usr/bin/env python3
"""CLI runner to execute Profile Analyst and Scout LIVE end-to-end.

Usage:
  python backend/scripts/run_profile_scout.py \
    --resume samples/sample_resume_rahul.pdf \
    --marksheet samples/sample_marksheet_rahul.pdf \
    --countries "Canada, Germany, Singapore" \
    --budget "Modest family budget, requires tuition waiver or stipend"
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add backend to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.agents.profile_analyst import run_profile_analyst
from app.agents.scout import plan_queries, run_scout
from app.events import AgentEvent
from app.schemas import ExtraInputs
from app.tools.domains import classify_source_tier
from app.tools.telemetry import Telemetry


async def main():
    parser = argparse.ArgumentParser(description="Live Profile Analyst and Scout Pipeline Runner")
    parser.add_argument("--resume", type=str, required=True, help="Path to resume PDF")
    parser.add_argument("--marksheet", type=str, required=True, help="Path to academic marksheet PDF")
    parser.add_argument("--github", type=str, default=None, help="Optional public GitHub URL or username")
    parser.add_argument("--countries", type=str, default="", help="Comma-separated target countries")
    parser.add_argument("--budget", type=str, default=None, help="Budget constraints note")
    parser.add_argument("--degree", type=str, default=None, help="Intended degree level")
    parser.add_argument("--output", type=str, default="data/last_run_profile_scout.json", help="Path to save output JSON")
    args = parser.parse_args()

    print("=" * 70)
    print("🚀 TETRANOODLE LIVE PIPELINE: PROFILE ANALYST + SCOUT")
    print("=" * 70)

    run_id = f"cli-run-{int(asyncio.get_event_loop().time())}"
    telemetry = Telemetry(run_id=run_id)

    async def console_emitter(event: AgentEvent):
        symbol = "ℹ️"
        if event.level == "warn":
            symbol = "⚠️"
        elif event.level == "error":
            symbol = "❌"
        elif event.level == "success":
            symbol = "✅"
        print(f"[{event.agent.upper()}] {symbol} {event.message}")

    target_countries = [c.strip() for c in args.countries.split(",") if c.strip()]
    extra = ExtraInputs(
        github_url=args.github,
        target_countries=target_countries,
        budget_note=args.budget,
        degree_level=args.degree,
    )

    # 1. Execute Profile Analyst
    print("\n--- PHASE 3A: PROFILE ANALYST ---")
    profile = await run_profile_analyst(
        resume_source=Path(args.resume),
        marksheet_source=Path(args.marksheet),
        extra_inputs=extra,
        run_id=run_id,
        emit=console_emitter,
        telemetry=telemetry,
    )

    print("\n📋 EXTRACTED & GROUNDED STUDENT PROFILE:")
    print(f"  Name: {profile.name}")
    print(f"  Degree: {profile.degree} in {profile.field}")
    print(f"  CGPA: {profile.cgpa} / {profile.cgpa_scale or 10.0}")
    print(f"  Target Countries: {', '.join(profile.target_countries)}")
    print(f"  Budget Note: {profile.budget_note}")
    print(f"  Skills ({len(profile.skills)}): {', '.join(profile.skills)}")
    print(f"  Projects ({len(profile.projects)}):")
    for p in profile.projects:
        print(f"    - {p.name}: {p.description or ''} (Tech: {', '.join(p.tech)})")
    print(f"  Missing Info ({len(profile.missing_info)}): {profile.missing_info}")
    print(f"  Field Confidence: {profile.field_confidence}")
    print(f"  Source Notes: {profile.source_notes}")

    # 2. Execute Scout
    print("\n--- PHASE 3B: SCOUT AGENT ---")
    planned_plan = await plan_queries(profile, telemetry=telemetry)
    print(f"\n🔍 PLANNED SEARCH QUERIES ({len(planned_plan.queries)}):")
    for i, q in enumerate(planned_plan.queries, 1):
        print(f"  {i:2d}. [{q.target_country or 'Global'}] {q.query}")

    print("\n📡 RUNNING LIVE DISCOVERY, RESOLUTION & TRIAGE...")
    candidates = await run_scout(
        profile=profile,
        target_count=10,
        max_candidates=15,
        run_id=run_id,
        emit=console_emitter,
        telemetry=telemetry,
    )

    print(f"\n🎯 DISCOVERED CANDIDATE OPPORTUNITIES ({len(candidates)}):")
    print("-" * 70)
    print(f"{'#':<3} | {'TIER':<9} | {'COUNTRY':<12} | {'OPPORTUNITY NAME':<35}")
    print("-" * 70)
    for idx, c in enumerate(candidates, 1):
        tier = classify_source_tier(c.url or "").upper()
        country = (c.country or "International")[:12]
        name = (c.name or "Program")[:35]
        print(f"{idx:<3} | {tier:<9} | {country:<12} | {name:<35}")
        print(f"    URL: {c.url}")
        if c.funding_summary:
            print(f"    Funding: {c.funding_summary}")
        if c.deadline:
            print(f"    Deadline: {c.deadline}")

    print("-" * 70)

    # 3. Telemetry Summary
    summary = telemetry.summary()
    print("\n📊 RUN TELEMETRY & HEALTH:")
    print(f"  Queries Executed: {summary['counters'].get('search_queries', 0)}")
    print(f"  Pages Crawled: {summary['counters'].get('pages_fetched', 0)}")
    print(f"  LLM Calls: {summary['counters'].get('llm_calls', 0)}")
    print(f"  Retries / Escalations: {summary['retries_count']}")
    print(f"  Fallbacks: {summary['fallbacks_count']}")
    print(f"  Failures: {summary['failures_count']}")

    # 4. Save JSON snapshot
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "profile": profile.model_dump(),
        "planned_queries": [q.model_dump() for q in planned_plan.queries],
        "candidates": [c.model_dump() for c in candidates],
        "telemetry": summary,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n💾 Saved execution artifact to: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
