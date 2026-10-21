"""Live web search tool with dual-provider architecture.

Primary: Tavily search API (curated, rich metadata).
Fallback: DuckDuckGo search (zero API key required, highly reliable).

Features:
- De-duplication by registrable domain + URL path.
- Automatic filtering of blocked/irrelevant domains (social media, link farms).
- Source tier attribution (official / reputable / unknown) via domains.py.
- Telemetry recording for all fallbacks and queries.
"""

import asyncio
import logging
from typing import List, Optional
from urllib.parse import urlparse
from pydantic import BaseModel
from tavily import TavilyClient
from duckduckgo_search import DDGS
from app.config import settings
from app.schemas import SourceTier
from app.tools.domains import (
    classify_source_tier,
    is_blocked_domain,
    registrable_domain,
)
from app.tools.telemetry import Telemetry


logger = logging.getLogger(__name__)


class SearchHit(BaseModel):
    """Normalized search hit from Tavily or DuckDuckGo."""
    title: str
    url: str
    snippet: str
    source_tier: SourceTier = "unknown"
    provider: str = "tavily"


def normalize_url_key(url: str) -> str:
    """Generate canonical key for de-duplication: domain + path without trailing slash."""
    try:
        parsed = urlparse(url.strip())
        domain = registrable_domain(url)
        path = parsed.path.rstrip("/")
        return f"{domain}{path}".lower()
    except Exception:
        return url.strip().lower()


class SearchTool:
    """Async-friendly web search provider with automatic DuckDuckGo fallback."""

    def __init__(self, tavily_api_key: Optional[str] = None):
        self._tavily_key = tavily_api_key or settings.tavily_api_key
        self._tavily_client: Optional[TavilyClient] = None

    def _get_tavily_client(self) -> Optional[TavilyClient]:
        if not self._tavily_client and self._tavily_key:
            self._tavily_client = TavilyClient(api_key=self._tavily_key)
        return self._tavily_client

    async def search(
        self,
        query: str,
        max_results: int = 10,
        telemetry: Optional[Telemetry] = None,
    ) -> List[SearchHit]:
        """Execute live search using Tavily, falling back to DuckDuckGo on any failure."""
        if telemetry:
            telemetry.increment("search_queries")

        hits: List[SearchHit] = []
        used_provider = "tavily"

        # 1. Attempt Primary: Tavily Search
        tavily_client = self._get_tavily_client()
        if tavily_client:
            try:
                # Run blocking Tavily client in thread
                raw_response = await asyncio.to_thread(
                    tavily_client.search,
                    query=query,
                    max_results=max_results,
                    search_depth="advanced",
                )
                raw_results = raw_response.get("results", [])
                for item in raw_results:
                    url = item.get("url", "")
                    if url and not is_blocked_domain(url):
                        hits.append(
                            SearchHit(
                                title=item.get("title", ""),
                                url=url,
                                snippet=item.get("content", ""),
                                source_tier=classify_source_tier(url),
                                provider="tavily",
                            )
                        )
            except Exception as exc:
                logger.warning("Tavily search failed for '%s': %s. Falling back to DuckDuckGo...", query, exc)
                if telemetry:
                    telemetry.record_fallback(
                        component="search_tool",
                        from_provider="tavily",
                        to_provider="duckduckgo",
                        reason=f"Tavily error: {str(exc)[:150]}",
                    )
                hits = []
        else:
            if telemetry:
                telemetry.record_fallback(
                    component="search_tool",
                    from_provider="tavily",
                    to_provider="duckduckgo",
                    reason="TAVILY_API_KEY not configured",
                )

        # 2. Fallback: DuckDuckGo Search (if Tavily produced no hits or errored)
        if not hits:
            used_provider = "duckduckgo"
            try:
                def _run_ddg():
                    raw_hits = []
                    # Try DDGS API
                    try:
                        with DDGS() as ddgs:
                            raw_hits = list(ddgs.text(query, max_results=max_results))
                    except Exception:
                        raw_hits = []

                    # If DDGS returned empty (common in automated IP challenges), fall back to direct DDG HTML parsing
                    if not raw_hits:
                        import urllib.parse
                        import urllib.request
                        from bs4 import BeautifulSoup

                        html_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
                        req = urllib.request.Request(
                            html_url,
                            headers={
                                "User-Agent": (
                                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                                ),
                                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            },
                        )
                        with urllib.request.urlopen(req, timeout=8) as resp:
                            soup = BeautifulSoup(resp.read().decode("utf-8", errors="ignore"), "html.parser")
                            for res in soup.select(".result")[:max_results]:
                                a = res.select_one(".result__title a")
                                snip = res.select_one(".result__snippet")
                                if a and a.get("href"):
                                    href = a["href"]
                                    # Extract target URL from DDG redirect url if present
                                    if "uddg=" in href:
                                        target_url = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
                                    else:
                                        target_url = href
                                    raw_hits.append({
                                        "title": a.get_text(strip=True),
                                        "href": target_url,
                                        "body": snip.get_text(strip=True) if snip else "",
                                    })
                    return raw_hits

                ddg_results = await asyncio.to_thread(_run_ddg)
                for item in ddg_results:
                    url = item.get("href", "")
                    if url and url.startswith("http") and not is_blocked_domain(url):
                        hits.append(
                            SearchHit(
                                title=item.get("title", ""),
                                url=url,
                                snippet=item.get("body", "") or item.get("snippet", ""),
                                source_tier=classify_source_tier(url),
                                provider="duckduckgo",
                            )
                        )
            except Exception as exc:
                logger.error("DuckDuckGo search also failed for '%s': %s", query, exc)
                if telemetry:
                    telemetry.record_failure(
                        component="search_tool",
                        error=f"Both Tavily and DuckDuckGo failed: {str(exc)}",
                        target=query,
                    )


        # 3. De-duplicate by canonical URL key while preserving official rank priority
        deduped_hits: List[SearchHit] = []
        seen_keys = set()

        for hit in hits:
            key = normalize_url_key(hit.url)
            if key not in seen_keys:
                seen_keys.add(key)
                deduped_hits.append(hit)

        return deduped_hits


# Global default search tool
search_tool = SearchTool()
