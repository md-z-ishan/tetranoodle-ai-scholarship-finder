# Experiments & Engineering Log (TetraNoodle Hackathon 2026)

This document tracks all real engineering trials, edge cases, scraper failures, anti-bot behaviors, and prompt iterations throughout the build.

---

## Log Entries

### [2026-10-22] - Phase 0: Project Inception & Engineering Scaffolding
- **What was set up**: Clean git repository, explicit branch workflow (`feat/phase-N`), strict `.gitignore` for secrets and data isolation, typed folder structure.
- **Key Decision**: Enforce clean virtual environment inside `backend/.venv` to keep local host system pristine and ensure zero leakage of secrets or temporary scraping artifacts.

### [2026-10-22] - Phase 1: Environment Isolation & Architecture Foundation
- **Real Finding - Python Version Pinning**: The system default Python was Python 3.9.6, but the hackathon specifications mandate Python 3.11+ for modern type hint support and Google GenAI SDK compatibility. Resolved by binding virtual environment setup explicitly to `/opt/homebrew/bin/python3.11` (Python 3.11.15) inside `backend/.venv`.
- **Zero Mock / Hardcoding Enforcement**: Tested schemas directly without any placeholder scholarship data; proved all unknown attributes default to `None` and lists to `[]` to safeguard against fabricated scholarship qualifications.
- **Async EventBus Replay**: Validated in-memory FIFO event ordering and run history buffer ensuring SSE subscribers never drop events during network reconnects.

### [2026-10-22] - Phase 2: Live Tools & Telemetry Instrumentation
- **Real Finding - DuckDuckGo Search API Block & Direct HTML Recovery**: Standard `duckduckgo_search` library returned 0 results due to automated IP challenges on its internal API endpoint. Implemented a resilient secondary fallback within `SearchTool` that directly queries `https://html.duckduckgo.com/html/` with browser headers and parses real search candidates via BeautifulSoup. Discovered 10 live results including official DAAD university programs.
- **Real Finding - WAF Anti-Bot Response on `daad.de` (HTTP 403)**: Direct fetch of `https://www.daad.de` returned an HTTP 403 Forbidden due to aggressive Cloudflare/Akamai bot detection. Verified that `FetchTool` truthfully handled this without crashing: marked `link_alive=False`, recorded HTTP 403 under `error`, and logged telemetry without making up text.
- **Real Finding - GitHub API Unauthenticated Rate Limits**: Verified `GithubTool` on public account `torvalds` (12 repos, 328k followers, 253k stars on `linux`). Captured language distributions and README excerpts. Rate limit handling verified: returns honest 403 notification rather than inventing developer metrics.
- **Real Finding - PDF Parser Scanned Detection**: Synthetic vector PDF extracted cleanly with `pypdf`. Threshold validator (`likely_scanned`) verified to flag low-density character extractions honestly.

### [2026-10-22] - Phase 3: Profile Analyst & Scout Live Discovery
- **Real Finding - Gemini SDK Schema Ref-Inlining**: The Google GenAI SDK (`google.genai`) `t_schema` transformer threw `KeyError` when handling nested Pydantic models containing `$ref` pointers after top-level `$defs` were stripped. Resolved in `llm.py` by recursively resolving and inlining all `$ref` definitions directly into the schema tree before stripping schema metadata.
- **Real Finding - Anti-Hallucination Grounding Audit**: In `ProfileAnalyst`, skills and projects extracted from PDFs were audited against raw source text using normalized substring matching. Verified that ungrounded attributes are safely filtered out, while user overrides via `ExtraInputs` are explicitly documented in `source_notes` with high confidence.
- **Real Finding - Multi-Country Scout Query Distribution**: Tested query planning on a multi-target student profile (Canada, Germany, Singapore). Scout planned 14 targeted queries distributed across all target countries and global full-ride terms. Discovered 30 unique URLs, resolved portals, and triaged 15 candidates across official (4), reputable (1), and unknown (10) tiers.

### [2026-10-22] - Phase 4: Verifier & Ranker Live Auditing & Scoring
- **Real Finding - Tiered Fetch & Tavily Extract Recovery**: During live verification of the 15 candidate URLs, 12 were fetched directly via HTTPX, while 3 blocked/dynamic portal pages were recovered via the secondary Tavily Extract fallback (`fetched_via="tavily_extract"`). One portal (`admissions.smu.edu.sg`) remained completely unreachable after all tiers and was truthfully flagged `link_alive=False` with status `unverified` (no fabricated text).
- **Real Finding - Deterministic Grounding & Mismatch Rejection**:
  - `The Tribe Network Award` had an expired deadline ("June 1, 2025"); Verifier's deadline parser against the current date immediately classified it as `passed` and status `rejected`.
  - Degree level checks detected discrepancies (e.g. graduate 'ms' required vs student's 'Bachelor of Technology') and rejected ineligible opportunities.
- **Real Finding - Free-Tier Rate Limits (5 RPM) & Graceful Degradation**: Running concurrent LLM extractions under free-tier quota limits (5 RPM for `gemini-2.5-flash`) triggered 429 `RESOURCE_EXHAUSTED`. The global semaphore and exponential backoff executed 25 retries. Candidates where retries were exhausted were downgraded to `unverified` with honest error messages rather than aborting the pipeline or fabricating details.
- **Real Finding - Ranker Diversity & Post-Check Verification**: Evaluated composite scoring across Fit (40%), Funding (25%), Urgency (15%), and Risk (20%). The soft country diversity filter prevented single-country sweeps and balanced top recommendations across Singapore and Canada. The post-check step verified all numbers in LLM-generated explanations against grounded evidence snippets, catching 0 hallucinations.

### [2026-10-22] - Phase 5: Quality Hard Filters, Writer/Critic Loop, LangGraph Orchestration & SSE
- **Real Finding - Deterministic Degree Level & Nationality Filtering**: Evaluated final-year B.Tech student profile requiring progression to graduate/Master's programmes. Adding hard filters in `Scout` and `Verifier` successfully intercepted pre-degree awards (e.g. polytechnic diplomas, Year 12 awards) and domestic-only restrictions (e.g. Singapore Citizens only) deterministically, preventing inappropriate awards from reaching the top 5 recommendations.
- **Real Finding - Token-Bucket Pacing & Disk Caching**: Pacing Gemini API calls via `TokenBucketRateLimiter(rpm=4)` smoothed burst traffic to ~15-second intervals, drastically reducing 429 quota exhaustion. Persistent SHA256 disk caching in `data/cache/` completely bypassed redundant LLM calls across repeated executions and retries.
- **Real Finding - Writer-Critic Dual-Layer Audit & Revision Bounding**: Audited Statement of Purpose generation across deterministic numeric checks and LLM quality reviews. Tested injection of ungrounded test scores (e.g. 99.8th percentile); the Critic deterministically caught the ungrounded token, demanded revisions, and halted strictly at the 2-round limit with honest logging.
- **Real Finding - LangGraph Fault Recovery & SSE Replay**: Verified the LangGraph `StateGraph` coordinator. When individual nodes encounter transient issues or sub-link timeouts, the node wrapper catches the failure, logs it in telemetry, and routes state forward to `finalize` to assemble partial results rather than crashing the SSE stream. Event replay from `EventBus` guarantees reconnecting subscribers receive full chronological history.

