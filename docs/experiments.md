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


