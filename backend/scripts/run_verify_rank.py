#!/usr/bin/env python3
"""CLI runner to execute Verifier and Ranker LIVE end-to-end.

Loads data/last_run_profile_scout.json (or runs full pipeline from scratch),
verifies each candidate against live official pages, ranks the results, and
prints a full report with telemetry summary.

Usage:
  # Use saved scout run (fastest):
  python backend/scripts/run_verify_rank.py

  # Run full pipeline from scratch:
  python backend/scripts/run_verify_rank.py \\
    --resume samples/sample_resume_rahul.pdf \\
    --marksheet samples/sample_marksheet_rahul.pdf \\
    --countries "Canada,Germany,Singapore" \\
    --budget "Modest family budget, requires tuition waiver or full scholarship"
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.agents.profile_analyst import run_profile_analyst
from app.agents.ranker import run_ranker
from app.agents.scout import run_scout
from app.agents.verifier import run_verifier
from app.events import AgentEvent
from app.schemas import ExtraInputs, Opportunity, StudentProfile
from app.tools.telemetry import Telemetry


# ---------------------------------------------------------------------------
# Console emitter
# ---------------------------------------------------------------------------

async def console_emitter(event: AgentEvent):
    symbol = {"info": "ℹ️", "warn": "⚠️", "error": "❌", "success": "✅"}.get(event.level, "·")
    print(f"  [{event.agent.upper():12s}] {symbol}  {event.message}")


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def _hr(char="─", width=78):
    print(char * width)


def print_candidates_table(verified):
    _hr()
    print(f"{'#':<3} {'TIER':<10} {'STATUS':<11} {'CONF':<6} {'DL STATUS':<14} {'FUNDING':<16} {'NAME':<30}")
    _hr()
    for i, v in enumerate(verified, 1):
        name = (v.name or "Unknown")[:30]
        print(
            f"{i:<3} {v.source_tier:<10} {v.status:<11} {v.confidence:<6.2f} "
            f"{v.deadline_status:<14} {v.funding_type:<16} {name}"
        )
    _hr()


def print_top5(recommendations):
    for rec in recommendations:
        opp = rec.opportunity
        print(f"\n{'━'*78}")
        print(f"  Rank #{rec.rank}  |  Score: {rec.score:.1f}/100  |  {opp.name or 'Unknown'}")
        print(f"  Org: {opp.organization or 'Unknown'}  |  Country: {opp.country or '?'}  |  Tier: {opp.source_tier}")
        print(f"  URL: {opp.url}")
        print(f"  Status: {opp.status}  |  Confidence: {opp.confidence:.2f}  |  Deadline: {opp.deadline_text or 'Unknown'} ({opp.deadline_status})")
        print(f"  Funding: {opp.funding_type}  |  Days left: {opp.days_left}")
        if rec.score_breakdown:
            bd = rec.score_breakdown
            print(
                f"  Score breakdown: fit={bd.fit:.1f} ({bd.fit_reason[:50]})  |  "
                f"funding={bd.funding:.1f}  |  urgency={bd.urgency:.1f}  |  risk={bd.risk:.1f}"
            )
        if rec.why_it_fits:
            print(f"  Why it fits: {rec.why_it_fits[:200]}")
        if rec.how_to_apply:
            print(f"  How to apply:")
            for step in rec.how_to_apply[:5]:
                print(f"    • {step}")
        if rec.still_needed:
            print(f"  Still needed from student:")
            for item in rec.still_needed[:5]:
                print(f"    ○ {item}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Live Verifier and Ranker Pipeline Runner")
    parser.add_argument("--scout-output", default="data/last_run_profile_scout.json",
                        help="Path to scout output JSON (default: data/last_run_profile_scout.json)")
    parser.add_argument("--resume", default=None, help="Path to resume PDF (triggers full pipeline)")
    parser.add_argument("--marksheet", default=None, help="Path to marksheet PDF")
    parser.add_argument("--countries", default="", help="Comma-separated target countries")
    parser.add_argument("--budget", default=None, help="Budget constraints note")
    parser.add_argument("--output", default="data/last_run_verify_rank.json",
                        help="Path to save output JSON")
    args = parser.parse_args()

    print("=" * 78)
    print("🔬 TETRANOODLE LIVE PIPELINE: VERIFIER + RANKER")
    print(f"   Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
    print("=" * 78)

    run_id = f"vr-run-{int(asyncio.get_event_loop().time())}"
    telemetry = Telemetry(run_id=run_id)

    # ---------------------------------------------------------------- #
    # Load or run scout                                                  #
    # ---------------------------------------------------------------- #
    scout_path = Path(args.scout_output)
    profile: StudentProfile
    candidates: list

    if scout_path.exists() and args.resume is None:
        print(f"\n📂 Loading scout output from: {scout_path}")
        data = json.loads(scout_path.read_text())
        profile = StudentProfile.model_validate(data["profile"])
        candidates = [Opportunity.model_validate(c) for c in data["candidates"]]
        print(f"   Profile: {profile.name} | {profile.degree} | CGPA {profile.cgpa}/{profile.cgpa_scale or 10}")
        print(f"   Candidates loaded: {len(candidates)}")
    else:
        if not args.resume:
            print("❌ No scout output found and --resume not provided. Exiting.")
            sys.exit(1)
        print("\n🚀 Running full pipeline (Profile Analyst → Scout)…")
        target_countries = [c.strip() for c in args.countries.split(",") if c.strip()]
        extra = ExtraInputs(
            target_countries=target_countries,
            budget_note=args.budget,
        )
        profile = await run_profile_analyst(
            resume_source=Path(args.resume),
            marksheet_source=Path(args.marksheet) if args.marksheet else None,
            extra_inputs=extra,
            run_id=run_id,
            emit=console_emitter,
            telemetry=telemetry,
        )
        candidates = await run_scout(
            profile=profile,
            run_id=run_id,
            emit=console_emitter,
            telemetry=telemetry,
        )

    # ---------------------------------------------------------------- #
    # Step 1: Verify                                                     #
    # ---------------------------------------------------------------- #
    print(f"\n{'─'*78}")
    print(f"📋 PHASE 4A: VERIFIER  ({len(candidates)} candidates, concurrency=3)")
    print(f"{'─'*78}")
    verified = await run_verifier(
        candidates=candidates,
        profile=profile,
        run_id=run_id,
        emit=console_emitter,
        telemetry=telemetry,
        concurrency=3,
    )

    print(f"\n📊 VERIFICATION RESULTS ({len(verified)} candidates):")
    print_candidates_table(verified)

    status_counts = {}
    tier_counts = {}
    fetched_via_counts = {}
    for v in verified:
        status_counts[v.status] = status_counts.get(v.status, 0) + 1
        tier_counts[v.source_tier] = tier_counts.get(v.source_tier, 0) + 1
        fv = v.fetched_via or "unknown"
        fetched_via_counts[fv] = fetched_via_counts.get(fv, 0) + 1

    print(f"  Status counts:     {status_counts}")
    print(f"  Tier counts:       {tier_counts}")
    print(f"  Fetched-via:       {fetched_via_counts}")

    # ---------------------------------------------------------------- #
    # Step 2: Rank                                                       #
    # ---------------------------------------------------------------- #
    print(f"\n{'─'*78}")
    print(f"🏆 PHASE 4B: RANKER")
    print(f"{'─'*78}")
    recommendations, executive_summary, could_not_verify, needs_from_student = await run_ranker(
        verified=verified,
        profile=profile,
        run_id=run_id,
        emit=console_emitter,
        telemetry=telemetry,
    )

    print(f"\n🏅 TOP {len(recommendations)} RECOMMENDATIONS:")
    print_top5(recommendations)

    print(f"\n{'━'*78}")
    print("📝 EXECUTIVE SUMMARY (consultant memo):")
    _hr("─")
    print(executive_summary)

    if could_not_verify:
        print(f"\n⚠️  COULD NOT VERIFY ({len(could_not_verify)}):")
        for item in could_not_verify[:10]:
            print(f"   • {item}")

    if needs_from_student:
        print(f"\n🔔 NEEDS FROM STUDENT ({len(needs_from_student)}):")
        for item in needs_from_student[:10]:
            print(f"   • {item}")

    # ---------------------------------------------------------------- #
    # Telemetry summary                                                  #
    # ---------------------------------------------------------------- #
    summary = telemetry.summary()
    print(f"\n{'─'*78}")
    print("📡 TELEMETRY SUMMARY:")
    print(f"   LLM calls:           {summary['counters'].get('llm_calls', 0)}")
    print(f"   Search queries:      {summary['counters'].get('search_queries', 0)}")
    print(f"   Pages fetched:       {summary['counters'].get('pages_fetched', 0)}")
    print(f"   Retries:             {summary['retries_count']}")
    print(f"   Fallbacks:           {summary['fallbacks_count']}")
    print(f"   Failures:            {summary['failures_count']}")
    print(f"   Fetched-via counts:  {fetched_via_counts}")
    print(f"   Events timeline:     {len(summary.get('events', []))} events recorded")

    # ---------------------------------------------------------------- #
    # Save JSON snapshot                                                 #
    # ---------------------------------------------------------------- #
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "run_at": datetime.utcnow().isoformat() + "Z",
        "profile": profile.model_dump(),
        "candidates": [c.model_dump() for c in candidates],
        "verified": [v.model_dump() for v in verified],
        "recommendations": [r.model_dump() for r in recommendations],
        "executive_summary": executive_summary,
        "could_not_verify": could_not_verify,
        "needs_from_student": needs_from_student,
        "status_counts": status_counts,
        "tier_counts": tier_counts,
        "fetched_via_counts": fetched_via_counts,
        "telemetry": summary,
    }
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\n💾 Saved execution artifact to: {output_path}")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
