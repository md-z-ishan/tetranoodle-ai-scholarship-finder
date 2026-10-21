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

