"""Resilient PDF text extraction tool for student resumes and marksheets.

Primary: pypdf (lightweight, rapid).
Fallback: pdfplumber (deep character & table extraction).

Features:
- Handles raw bytes or file paths.
- Reports total page count and extracted character length.
- Scanned detection: Sets 'likely_scanned=True' when negligible text is extracted
  (Honest about uncertainty: never pretends OCR happened).
- Cleans hyphenated linebreaks and repeated whitespace while preserving original content.
"""

import io
import logging
import re
from pathlib import Path
from typing import Optional, Union
from pydantic import BaseModel
import pypdf
import pdfplumber
from app.tools.telemetry import Telemetry


logger = logging.getLogger(__name__)

# Minimum characters expected per page to consider a document machine-readable
MIN_CHARS_PER_PAGE_THRESHOLD = 30


class ExtractedPdf(BaseModel):
    """Result of PDF text extraction with extraction telemetry and quality flags."""
    text: str
    page_count: int
    char_count: int
    likely_scanned: bool = False
    extractor_used: str = "pypdf"
    error: Optional[str] = None


def normalize_pdf_text(text: str) -> str:
    """Clean hyphenated line wraps and excessive whitespace without mutating facts."""
    if not text:
        return ""
    # Re-join hyphenated words split across lines (e.g., "algo-\nrithm" -> "algorithm")
    cleaned = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)
    # Normalize multiple blank spaces while keeping paragraph line breaks
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    # Limit consecutive newlines to maximum 2
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


class PdfTool:
    """PDF parser with dual-extractor fallback and scanned document detection."""

    def extract_pdf_text(
        self,
        source: Union[bytes, str, Path],
        telemetry: Optional[Telemetry] = None,
    ) -> ExtractedPdf:
        """Extract text from PDF bytes or filesystem path with fallback to pdfplumber."""
        # Normalize into bytes buffer
        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.is_file():
                return ExtractedPdf(
                    text="", page_count=0, char_count=0, error=f"File not found: {path}"
                )
            pdf_bytes = path.read_bytes()
        else:
            pdf_bytes = source

        if not pdf_bytes:
            return ExtractedPdf(text="", page_count=0, char_count=0, error="Empty PDF payload")

        # 1. Primary: pypdf extraction
        pages_text = []
        page_count = 0
        extractor_used = "pypdf"

        try:
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            page_count = len(reader.pages)
            for page in reader.pages:
                extracted = page.extract_text() or ""
                pages_text.append(extracted)
        except Exception as pypdf_err:
            logger.warning("pypdf failed: %s. Falling back to pdfplumber...", pypdf_err)
            if telemetry:
                telemetry.record_fallback(
                    component="pdf_tool",
                    from_provider="pypdf",
                    to_provider="pdfplumber",
                    reason=f"pypdf error: {str(pypdf_err)[:100]}",
                )
            pages_text = []

        combined_text = "\n".join(pages_text).strip()

        # 2. Fallback: pdfplumber if pypdf extracted very little text or errored
        if not combined_text or len(combined_text) < (page_count * MIN_CHARS_PER_PAGE_THRESHOLD):
            try:
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    page_count = len(pdf.pages)
                    plumber_pages = []
                    for page in pdf.pages:
                        extracted = page.extract_text() or ""
                        plumber_pages.append(extracted)
                    combined_text = "\n".join(plumber_pages).strip()
                    extractor_used = "pdfplumber"
            except Exception as plumber_err:
                logger.error("pdfplumber also failed: %s", plumber_err)
                if telemetry:
                    telemetry.record_failure("pdf_tool", error=str(plumber_err))

        clean_text = normalize_pdf_text(combined_text)
        char_count = len(clean_text)

        # Flag scanned document if total text is almost zero despite having pages
        likely_scanned = False
        if page_count > 0 and char_count < (page_count * 15):
            likely_scanned = True

        return ExtractedPdf(
            text=clean_text,
            page_count=page_count,
            char_count=char_count,
            likely_scanned=likely_scanned,
            extractor_used=extractor_used,
        )


# Global PDF tool instance
pdf_tool = PdfTool()
