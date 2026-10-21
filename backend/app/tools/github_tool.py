"""GitHub developer profile enrichment tool using GitHub REST API.

Features:
- Extracts public account details: name, bio, public_repos, followers.
- Fetches repositories, sorts by stars and activity, derives top languages.
- Retrieves trimmed README excerpts for the top repositories.
- Operates reliably without token (60 req/hr rate limit) or with GITHUB_TOKEN (5,000 req/hr).
- Truthfully handles 404 (user not found) and 403 (rate limit hit) without crashes.
"""

import base64
import logging
import re
from typing import List, Optional
import httpx
from app.config import settings
from app.schemas import GithubProfile, GithubRepo
from app.tools.telemetry import Telemetry


logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
MAX_TOP_REPOS = 5
MAX_README_CHARS = 1500


def extract_username(url_or_username: str) -> str:
    """Normalize input into a clean GitHub username."""
    if not url_or_username:
        return ""
    clean = url_or_username.strip().rstrip("/")
    if "github.com/" in clean:
        parts = clean.split("github.com/")[-1].split("/")
        return parts[0]
    return clean.lstrip("@")


class GithubTool:
    """Async GitHub REST API client for student developer enrichment."""

    def __init__(self, token: Optional[str] = None):
        self._token = token or settings.github_token

    def _get_headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "TetraNoodle-Scholarship-Finder/1.0",
        }
        if self._token and self._token.strip():
            headers["Authorization"] = f"Bearer {self._token.strip()}"
        return headers

    async def fetch_github_profile(
        self,
        url_or_username: str,
        telemetry: Optional[Telemetry] = None,
    ) -> GithubProfile:
        """Fetch developer stats, public repos, and README excerpts for top projects."""
        username = extract_username(url_or_username)
        if not username:
            return GithubProfile(username="", error="Empty or invalid GitHub username")

        headers = self._get_headers()
        async with httpx.AsyncClient(headers=headers, timeout=12.0) as client:
            # 1. Fetch user profile
            try:
                user_resp = await client.get(f"{GITHUB_API_BASE}/users/{username}")
                if user_resp.status_code == 404:
                    return GithubProfile(username=username, error=f"GitHub user '{username}' not found (404)")
                if user_resp.status_code == 403:
                    if telemetry:
                        telemetry.record_failure("github_tool", "GitHub API rate limit exceeded (403)", target=username)
                    return GithubProfile(username=username, error="GitHub API rate limit exceeded (403)")
                if user_resp.status_code >= 400:
                    return GithubProfile(username=username, error=f"GitHub API returned HTTP {user_resp.status_code}")

                user_data = user_resp.json()
            except Exception as exc:
                if telemetry:
                    telemetry.record_failure("github_tool", error=str(exc), target=username)
                return GithubProfile(username=username, error=f"Network error contacting GitHub: {str(exc)[:120]}")

            # 2. Fetch public repositories
            repos_list: List[GithubRepo] = []
            language_counts = {}
            try:
                repos_resp = await client.get(
                    f"{GITHUB_API_BASE}/users/{username}/repos",
                    params={"sort": "pushed", "direction": "desc", "per_page": 30},
                )
                if repos_resp.status_code == 200:
                    raw_repos = repos_resp.json()
                    # Sort primarily by stars, then updated date
                    sorted_repos = sorted(
                        raw_repos,
                        key=lambda r: (r.get("stargazers_count", 0), r.get("pushed_at", "")),
                        reverse=True,
                    )

                    # Compute language frequency across repositories
                    for r in raw_repos:
                        lang = r.get("language")
                        if lang:
                            language_counts[lang] = language_counts.get(lang, 0) + 1

                    # 3. Fetch README excerpt for top N repositories
                    for r in sorted_repos[:MAX_TOP_REPOS]:
                        repo_name = r.get("name", "")
                        readme_excerpt = None
                        try:
                            readme_resp = await client.get(f"{GITHUB_API_BASE}/repos/{username}/{repo_name}/readme")
                            if readme_resp.status_code == 200:
                                readme_json = readme_resp.json()
                                content_b64 = readme_json.get("content", "")
                                if content_b64:
                                    decoded = base64.b64decode(content_b64).decode("utf-8", errors="ignore")
                                    # Strip Markdown image badges & HTML
                                    clean_readme = re.sub(r"!\[.*?\]\(.*?\)", "", decoded)
                                    clean_readme = re.sub(r"<.*?>", "", clean_readme).strip()
                                    readme_excerpt = clean_readme[:MAX_README_CHARS]
                        except Exception:
                            readme_excerpt = None

                        repos_list.append(
                            GithubRepo(
                                name=repo_name,
                                description=r.get("description"),
                                language=r.get("language"),
                                topics=r.get("topics", []) or [],
                                stars=r.get("stargazers_count", 0),
                                last_pushed=r.get("pushed_at"),
                                readme_excerpt=readme_excerpt,
                            )
                        )
            except Exception as exc:
                logger.warning("Error fetching GitHub repos for %s: %s", username, exc)

            # Sort top languages by frequency
            top_languages = sorted(language_counts.keys(), key=lambda l: language_counts[l], reverse=True)

            return GithubProfile(
                username=username,
                name=user_data.get("name"),
                bio=user_data.get("bio"),
                public_repos=user_data.get("public_repos", 0),
                followers=user_data.get("followers", 0),
                top_languages=top_languages,
                repos=repos_list,
            )


# Global GitHub tool instance
github_tool = GithubTool()
