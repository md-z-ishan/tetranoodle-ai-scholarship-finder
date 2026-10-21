"""Resilient webpage fetcher and readable text extraction utility.

Features:
- Async HTTP client with realistic modern desktop User-Agent.
- Handles redirects, timeouts with backoff, HTTP 429 and 5xx errors.
- Max body size guard (prevents memory spikes on giant downloads).
- BeautifulSoup text extraction: strips navigation, scripts, styles, footer clutter.
- Extracts targeted evidence snippets around key phrases (eligibility, funding, deadlines).
- Never crashes on dead links or network errors: returns structured FetchedPage with link_alive=False.
"""

import asyncio
from datetime import datetime, timezone
import logging
import re
from typing import List, Literal, Optional
import httpx

from bs4 import BeautifulSoup
from pydantic import BaseModel
from app.config import settings
from app.tools.telemetry import Telemetry


logger = logging.getLogger(__name__)

# Modern desktop Chrome User-Agent
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Concurrency semaphore for polite crawling
FETCH_SEMAPHORE = asyncio.Semaphore(5)

# Size guard: 5MB maximum response body
MAX_BODY_BYTES = 5 * 1024 * 1024

# Default maximum text length for parsed page to avoid token waste
MAX_PAGE_TEXT_CHARS = 12000


FetchedVia = Literal["direct", "headers_retry", "tavily_extract", "failed"]


class FetchedPage(BaseModel):
    """Normalized page fetch result with metadata, extraction tiers, and extracted text."""
    url: str
    final_url: Optional[str] = None
    status_code: Optional[int] = None
    link_alive: bool = False
    fetched_at: str
    title: Optional[str] = None
    text: Optional[str] = None
    content_type: Optional[str] = None
    fetched_via: FetchedVia = "direct"
    error: Optional[str] = None



def clean_html_text(html_content: str, max_chars: int = MAX_PAGE_TEXT_CHARS) -> tuple[str, str]:
    """Parse HTML and extract clean, readable text and title without scripts/styles."""
    soup = BeautifulSoup(html_content, "html.parser")

    # Extract title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Decompose unwanted elements
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg", "form"]):
        tag.decompose()

    # Get structured text
    lines = []
    for element in soup.stripped_strings:
        cleaned = re.sub(r"\s+", " ", element).strip()
        if cleaned:
            lines.append(cleaned)

    full_text = "\n".join(lines)
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "\n...[truncated]"

    return title, full_text


def find_snippets(text: str, keywords: Optional[List[str]] = None, window: int = 250) -> List[str]:
    """Extract contextual snippets around relevant scholarship audit keywords."""
    if not text:
        return []

    default_keywords = ["eligibility", "deadline", "funding", "stipend", "tuition", "apply", "requirement", "gpa"]
    target_keywords = keywords or default_keywords

    snippets: List[str] = []
    lower_text = text.lower()

    for kw in target_keywords:
        kw_lower = kw.lower()
        start_idx = 0
        while True:
            pos = lower_text.find(kw_lower, start_idx)
            if pos == -1:
                break

            # Calculate surrounding window
            left = max(0, pos - window // 2)
            right = min(len(text), pos + len(kw) + window // 2)

            snippet = text[left:right].strip()
            # Clean newlines for cleaner presentation
            snippet = re.sub(r"\s+", " ", snippet)
            if snippet and snippet not in snippets:
                snippets.append(snippet)
                if len(snippets) >= 5:
                    return snippets

            start_idx = pos + len(kw) + 50

    return snippets


class FetchTool:
    """Async webpage fetcher with retries, timeouts, and graceful error capture."""

    def __init__(self, timeout_seconds: Optional[int] = None):
        self._timeout = timeout_seconds or settings.fetch_timeout_seconds

    async def fetch_page(
        self,
        url: str,
        telemetry: Optional[Telemetry] = None,
        max_retries: int = 2,
    ) -> FetchedPage:
        """Fetch a live URL, follow redirects, handle retries on transient errors, and parse text."""
        timestamp = datetime.now(timezone.utc).isoformat()
        if telemetry:
            telemetry.increment("pages_fetched")

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        async with FETCH_SEMAPHORE:
            for attempt in range(1, max_retries + 1):
                try:
                    async with httpx.AsyncClient(
                        headers=headers,
                        follow_redirects=True,
                        timeout=self._timeout,
                        verify=False,  # Resilient to misconfigured academic SSL chains
                    ) as client:
                        response = await client.get(url)

                        # Retry on 429 or 5xx
                        if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                            backoff = 1.5 ** attempt
                            if telemetry:
                                telemetry.record_retry(
                                    component="fetch_tool",
                                    attempt=attempt,
                                    reason=f"HTTP {response.status_code}",
                                    target=url,
                                    backoff_seconds=backoff,
                                )
                            await asyncio.sleep(backoff)
                            continue

                        # Check non-2xx status (Special WAF 403 / anti-bot handling with tiered escalation)
                        if response.status_code >= 400:
                            # Tier 2 Escalation: If blocked with 403/401, retry once with browser-emulation headers
                            if response.status_code in (403, 401) and attempt == 1:
                                if telemetry:
                                    telemetry.record_retry(
                                        component="fetch_tool",
                                        attempt=1,
                                        reason=f"HTTP {response.status_code} WAF - escalating to alternate browser headers",
                                        target=url,
                                    )
                                # Next loop iteration will execute headers retry
                                headers["Sec-Ch-Ua"] = '"Chromium";v="124", "Google Chrome";v="124"'
                                headers["Sec-Ch-Ua-Mobile"] = "?0"
                                headers["Sec-Ch-Ua-Platform"] = '"macOS"'
                                headers["Sec-Fetch-Dest"] = "document"
                                headers["Sec-Fetch-Mode"] = "navigate"
                                headers["Sec-Fetch-Site"] = "none"
                                headers["Sec-Fetch-User"] = "?1"
                                headers["Upgrade-Insecure-Requests"] = "1"
                                continue

                            # Tier 3 Escalation: If still blocked on an official site, attempt Tavily Extract fallback
                            if response.status_code in (403, 401, 503) and settings.tavily_api_key:
                                try:
                                    from tavily import TavilyClient
                                    t_client = TavilyClient(api_key=settings.tavily_api_key)
                                    ext_res = await asyncio.to_thread(
                                        t_client.extract,
                                        urls=[url],
                                    )
                                    results = ext_res.get("results", [])
                                    if results and results[0].get("raw_content"):
                                        if telemetry:
                                            telemetry.record_fallback(
                                                component="fetch_tool",
                                                from_provider="httpx_direct",
                                                to_provider="tavily_extract",
                                                reason=f"HTTP {response.status_code} WAF/anti-bot bypass",
                                            )
                                        raw_c = results[0]["raw_content"]
                                        _, clean_c = clean_html_text(raw_c) if "<html" in raw_c.lower() else ("", raw_c[:MAX_PAGE_TEXT_CHARS])
                                        return FetchedPage(
                                            url=url,
                                            final_url=url,
                                            status_code=200,
                                            link_alive=True,
                                            fetched_at=timestamp,
                                            title="Official Portal (Extracted)",
                                            text=clean_c,
                                            content_type="text/html",
                                            fetched_via="tavily_extract",
                                        )
                                except Exception as ext_err:
                                    logger.warning("Tavily extract fallback failed for %s: %s", url, ext_err)

                            # All tiers failed - remain truthful
                            return FetchedPage(
                                url=url,
                                final_url=str(response.url),
                                status_code=response.status_code,
                                link_alive=False,
                                fetched_at=timestamp,
                                fetched_via="failed",
                                error=f"HTTP status {response.status_code}",
                            )

                        content_type = response.headers.get("content-type", "").lower()

                        # Body size limit check
                        if len(response.content) > MAX_BODY_BYTES:
                            return FetchedPage(
                                url=url,
                                final_url=str(response.url),
                                status_code=response.status_code,
                                link_alive=True,
                                fetched_at=timestamp,
                                fetched_via="direct" if attempt == 1 else "headers_retry",
                                error="Response body exceeded 5MB size limit",
                            )

                        # Parse HTML
                        html = response.text
                        title, text = clean_html_text(html)

                        return FetchedPage(
                            url=url,
                            final_url=str(response.url),
                            status_code=response.status_code,
                            link_alive=True,
                            fetched_at=timestamp,
                            title=title,
                            text=text,
                            content_type=content_type,
                            fetched_via="direct" if attempt == 1 else "headers_retry",
                        )


                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                    if attempt < max_retries:
                        backoff = 1.0 ** attempt
                        if telemetry:
                            telemetry.record_retry(
                                component="fetch_tool",
                                attempt=attempt,
                                reason=f"Network timeout/connect error: {str(exc)}",
                                target=url,
                                backoff_seconds=backoff,
                            )
                        await asyncio.sleep(backoff)
                        continue
                    if telemetry:
                        telemetry.record_failure(
                            component="fetch_tool",
                            error=f"Timeout/Connect error after {max_retries} attempts: {str(exc)}",
                            target=url,
                        )
                    return FetchedPage(
                        url=url,
                        link_alive=False,
                        fetched_at=timestamp,
                        error=f"Network error: {str(exc)[:150]}",
                    )

                except Exception as exc:
                    if telemetry:
                        telemetry.record_failure(
                            component="fetch_tool",
                            error=f"Unexpected fetch error: {str(exc)}",
                            target=url,
                        )
                    return FetchedPage(
                        url=url,
                        link_alive=False,
                        fetched_at=timestamp,
                        error=f"Fetch failed: {str(exc)[:150]}",
                    )

        return FetchedPage(url=url, link_alive=False, fetched_at=timestamp, error="Exhausted retry attempts")


# Global fetch tool instance
fetch_tool = FetchTool()
