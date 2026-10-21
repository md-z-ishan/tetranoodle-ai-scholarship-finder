"""FastAPI application entrypoint for TetraNoodle AI Scholarship Finder.

Features:
- CORS middleware configured for secure local development.
- GET /health endpoint for observability & health checks.
- Static file serving for the frontend web application when index.html is present.
"""

from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import settings


app = FastAPI(
    title="TetraNoodle AI Scholarship Finder",
    description="Consultant-grade multi-agent swarm for scholarship discovery, verification, and application synthesis.",
    version="1.0.0",
    debug=settings.debug,
)

# CORS configuration for frontend and localhost testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["Health"])
async def health_check():
    """Health endpoint returning operational status."""
    return {"status": "ok"}


# Mount frontend static files if index.html is present
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.is_dir() and (FRONTEND_DIR / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
