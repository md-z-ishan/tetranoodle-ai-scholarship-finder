# Experiments & Engineering Log (TetraNoodle Hackathon 2026)

This document tracks all real engineering trials, edge cases, scraper failures, anti-bot behaviors, and prompt iterations throughout the build.

---

## Log Entries

### [2026-10-22] - Phase 0: Project Inception & Engineering Scaffolding
- **What was set up**: Clean git repository, explicit branch workflow (`feat/phase-N`), strict `.gitignore` for secrets and data isolation, typed folder structure.
- **Key Decision**: Enforce clean virtual environment inside `backend/.venv` to keep local host system pristine and ensure zero leakage of secrets or temporary scraping artifacts.
