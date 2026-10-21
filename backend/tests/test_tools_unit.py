"""Offline unit tests for all core tools (zero network dependencies).

Rules:
- Zero external network requests.
- Zero fake/mock scholarship data.
- Covers domain classification, evidence snippet extraction, HTML text parser,
  search key deduplication, telemetry summary, and synthetic PDF extraction.
"""

import io
import pytest
from pydantic import BaseModel
from reportlab.pdfgen import canvas
from app.tools.domains import (
    classify_source_tier,
    is_blocked_domain,
    is_official_domain,
    registrable_domain,
)
from app.tools.fetch_tool import clean_html_text, find_snippets
from app.tools.pdf_tool import PdfTool, normalize_pdf_text
from app.tools.search_tool import normalize_url_key
from app.tools.telemetry import Telemetry


def test_telemetry_collector_lifecycle():
    """Verify Telemetry tracks timings, retries, fallbacks, and produces truthful summary."""
    t = Telemetry(run_id="run-unit-001")
    t.increment("llm_calls", 2)

    with t.record_timing("unit_op"):
        sum([i for i in range(1000)])

    t.record_retry(component="fetch_tool", attempt=1, reason="HTTP 429", target="https://example.edu", backoff_seconds=1.5)
    t.record_fallback(component="search_tool", from_provider="tavily", to_provider="duckduckgo", reason="Quota")
    t.record_failure(component="github_tool", error="User not found (404)")

    summary = t.summary()
    assert summary["run_id"] == "run-unit-001"
    assert summary["counters"]["llm_calls"] == 2
    assert summary["counters"]["retries_count"] == 1
    assert summary["counters"]["fallbacks_count"] == 1
    assert summary["counters"]["failures_count"] == 1
    assert "unit_op" in summary["timings"]
    assert len(summary["events_log"]) == 3


def test_domain_tiering_and_allowlist():
    """Verify structural classification into official, reputable, and unknown."""
    # Official domains
    assert classify_source_tier("https://cs.toronto.edu/apply") == "official"
    assert classify_source_tier("https://www.daad.de/en/study-and-research-in-germany/") == "official"
    assert classify_source_tier("https://www.ox.ac.uk/admissions/graduate/fees-and-funding") == "official"
    assert classify_source_tier("https://vanier.gc.ca/en/home-accueil.html") == "official"
    assert is_official_domain("https://state.gov/fellowships") is True

    # Reputable aggregators
    assert classify_source_tier("https://www.mastersportal.com/search") == "reputable"
    assert classify_source_tier("https://www.topuniversities.com/student-info/scholarships") == "reputable"

    # Unknown
    assert classify_source_tier("https://random-personal-blog.org/tips") == "unknown"

    # Blocked domains
    assert is_blocked_domain("https://www.reddit.com/r/scholarships") is True
    assert is_blocked_domain("https://pinterest.com/pin/12345") is True

    # Registrable domain extraction
    assert registrable_domain("https://sub.domain.cam.ac.uk/path") == "cam.ac.uk"
    assert registrable_domain("https://grad.uwaterloo.ca/funding") == "uwaterloo.ca"


def test_html_cleaning_and_text_extraction():
    """Verify HTML cleaning strips script/style/nav and preserves headings and clean text."""
    sample_html = """
    <!DOCTYPE html>
    <html>
      <head><title>Graduate Fellowship Requirements</title></head>
      <body>
        <nav><a href="/home">Home</a><a href="/login">Login</a></nav>
        <script>alert('tracking');</script>
        <h1>Master of Science in Computational Intelligence</h1>
        <p>Eligibility requirement: Minimum undergraduate CGPA of 3.3 or equivalent degree.</p>
        <p>Application deadline for international students: January 15.</p>
        <footer>Copyright 2026 University. All rights reserved.</footer>
      </body>
    </html>
    """
    title, text = clean_html_text(sample_html)
    assert title == "Graduate Fellowship Requirements"
    assert "alert('tracking')" not in text
    assert "Login" not in text
    assert "Copyright" not in text
    assert "Minimum undergraduate CGPA of 3.3" in text
    assert "January 15" in text


def test_evidence_snippet_finder():
    """Verify snippet extraction locates keywords with surrounding sentence context."""
    text = (
        "The Department offers funded research fellowships. "
        "Eligibility: Applicants must hold a Bachelor's degree with strong research foundation. "
        "The annual stipend is 28000 EUR with full tuition waiver. "
        "The submission deadline is fixed strictly for December 1 annually."
    )
    snippets = find_snippets(text, keywords=["eligibility", "deadline", "stipend"], window=100)
    assert len(snippets) >= 2
    assert any("Eligibility" in s for s in snippets)
    assert any("deadline" in s.lower() for s in snippets)


def test_search_url_deduplication():
    """Verify canonical URL key normalization collapses trailing slashes and sub-paths."""
    key1 = normalize_url_key("https://www.mit.edu/admissions/graduate/")
    key2 = normalize_url_key("https://mit.edu/admissions/graduate")
    assert key1 == key2


def test_pdf_extraction_from_bytes():
    """Generate a clean synthetic PDF in-memory and verify extraction without disk dependence."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.drawString(100, 750, "Student Academic Record")
    c.drawString(100, 730, "Bachelor of Technology in Computer Science and Engineering")
    c.drawString(100, 710, "Cumulative Grade Point Average: 8.4 out of 10.0")
    c.save()

    pdf_bytes = buffer.getvalue()
    tool = PdfTool()
    res = tool.extract_pdf_text(pdf_bytes)

    assert res.page_count == 1
    assert res.char_count > 30
    assert res.likely_scanned is False
    assert "Bachelor of Technology" in res.text
    assert "8.4" in res.text
