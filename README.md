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

# 2. Set up virtual environment
cd backend
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Edit .env with your GEMINI_API_KEY, TAVILY_API_KEY, etc.

# 5. Launch application
uvicorn app.main:app --reload --port 8000
```

Open your browser at `http://localhost:8000` to access the interactive dashboard.

---

## 🧪 Live Experiments Log

All real trials, scrapers observations, LLM hallucinations caught by the Critic, and fallback events are continuously documented in [docs/experiments.md](docs/experiments.md).

---

## 📄 License
This project is licensed under the [MIT License](LICENSE).
