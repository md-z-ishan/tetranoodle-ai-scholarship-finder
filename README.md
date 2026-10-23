# TetraNoodle AI Scholarship Finder 🎓🤖

> **Consultant-Grade Multi-Agent Swarm for Autonomous Student Opportunity Discovery, Verification, Ranking, and Application Synthesis.**
>
> Built for the **TetraNoodle AI Hackathon 2026** (Assessment Round for the TetraNoodle AI/ML Fellowship).

---

## 🌟 Overview

The **TetraNoodle AI Scholarship Finder** is an autonomous multi-agent system designed to replace fragmented, manual scholarship searching with an evidence-grounded research pipeline. 

Unlike conversational wrappers or static scrapers:
- **Zero Mock / Hardcoded Data**: Every discovered scholarship is retrieved through live search tools and verified against live official university / institutional domains.
- **Truthful Uncertainty**: Information not found on an official webpage is explicitly marked as `null` under `unknown_fields`.
- **Full Traceability**: Every recommended opportunity is backed by official source URLs, retrieval timestamps, exact text evidence snippets, and confidence scores.
- **Dynamic Adaptability**: Evaluated profiles can change dynamically without requiring code or system modifications.

---

## 🏗️ Multi-Agent Swarm Architecture

The pipeline uses **7 specialized agent roles** coordinated via **LangGraph**:

1. **Orchestrator**: Routes state between agents, handles timeouts, retries, and broadcasts real-time execution events via Server-Sent Events (SSE).
2. **Profile Analyst**: Parses uploaded student documents (Resume + Transcripts) and enriches the profile using the GitHub REST API without hallucinations.
3. **Scout**: Generates dynamic, multi-dimensional search queries and identifies 10+ candidate opportunities across live search providers (Tavily with DuckDuckGo fallback).
4. **Verifier**: Crawls official portals, extracts structured eligibility, funding, and deadline criteria, flags gaps, and checks domain legitimacy.
5. **Ranker**: Applies transparent multi-factor scoring (academic fit, funding coverage, deadline urgency, risk) to select the Top 5 opportunities.
6. **Writer**: Synthesizes a tailored, compelling Statement of Purpose (SOP) draft for the top match using strictly verified student profile facts.
7. **Critic**: Performs a strict factual audit of the SOP draft, demanding evidence verification and managing a real revision loop.

---

## 🚀 Quick Start (Local Setup)

### Prerequisites
- Python 3.11+
- Virtual environment (`venv`)

### Installation & Run

```bash
# 1. Clone repository
git clone https://github.com/md-z-ishan/tetranoodle-ai-scholarship-finder.git
cd tetranoodle-ai-scholarship-finder

# 2. Set up virtual environment (Python 3.11+)
cd backend
python3.11 -m venv .venv
source .venv/bin/activate

# 3. Install pinned dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Edit .env with your GEMINI_API_KEY, TAVILY_API_KEY, and optional GITHUB_TOKEN

# 5. Run test suite (Structural & Integrity validation)
pytest tests/ -v

# 6. Launch the FastAPI backend server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Verify service health at:
- `http://localhost:8000/` -> Interactive Consultant-Grade Web Application
- `http://localhost:8000/health` -> `{"status": "ok"}`
- `http://localhost:8000/docs` -> Interactive OpenAPI documentation

---

## 💻 Web Application Dashboard

The frontend is a single-page application served directly by FastAPI at `/`:

- **Student Profile Panel (Left)**: Drag-and-drop PDF upload for resume and transcripts with 10MB validation, interactive country chips with emoji flags, target degree selector, and a "New Student" one-click form reset.
- **Agent Swarm Arena (Center)**: Seven live agent lanes (Orchestrator, Profile Analyst, Scout, Verifier, Ranker, Writer, Critic) with animated state badges, lit handoff connectors, Writer-Critic revision loop counters, and a color-coded event feed with auto-scroll and pause-on-hover.
- **Swarm Intelligence Results (Right)**:
  - **Top Matches**: Ranked recommendation cards with score dials (0–100), transparent 4-part breakdown (fit, funding, urgency, risk), official source links, and verbatim evidence snippets.
  - **SOP Draft**: Grounded Statement of Purpose with a "Draft only - not submitted" watermark, copy button, and Critic audit trail.
  - **All Candidates**: Filterable table showing all discovered candidates with source tiers, statuses, and explicit real rejection reasons.
  - **Run Health**: Real-time observability dashboard displaying LLM calls, cache hits, retries, fetch distribution, and per-agent latency bars.
  - **History Drawer**: Access past runs directly from disk with instant result loading without re-executing.


---

## 📡 API Reference & Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/runs` | Upload resume (PDF) & optional marksheet + form inputs. Launches background swarm run. |
| `GET` | `/api/runs/{run_id}/events` | Server-Sent Events (SSE) stream delivering event replay and live agent updates. |
| `GET` | `/api/runs/{run_id}` | Retrieve run status, metadata, and full compiled `RunResult`. |
| `GET` | `/api/runs` | Retrieve chronological history of all previous runs saved to disk. |
| `POST` | `/api/runs/{run_id}/rerun` | Re-run a previous student profile using saved documents with edited form fields. |
| `GET` | `/api/runs/{run_id}/report.md` | Download or view the consultant-grade Markdown advisory report. |
| `GET` | `/api/runs/{run_id}/trace.json` | Complete execution audit trace containing all agent events and telemetry metrics. |
| `GET` | `/api/status` | Configuration check returning readiness booleans (never exposes secret keys). |

### Core Environment Variables

```bash
# LLM Configuration (Google Gemini)
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash
GEMINI_FALLBACK_MODEL=gemini-2.0-flash # Triggered automatically upon repeated 429 quota exhaustion
GEMINI_RPM=4                               # Token-bucket rate limit ceiling (requests per minute)

# Web Search Tool (Primary: Tavily)
TAVILY_API_KEY=your_key_here

# GitHub REST API (Optional: boosts rate limit from 60 to 5,000 req/hr)
GITHUB_TOKEN=your_token_optional
```

## 🧪 Live Experiments Log

All real trials, scrapers observations, LLM hallucinations caught by the Critic, and fallback events are continuously documented in [docs/experiments.md](docs/experiments.md).

---

## 📄 License
This project is licensed under the [MIT License](LICENSE).
