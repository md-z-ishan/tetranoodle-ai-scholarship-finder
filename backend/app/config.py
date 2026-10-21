"""Application configuration loader with deferred validation.

Follows consultant-grade design: loads environment variables cleanly, provides
typed access with defaults, and avoids crashing at import time so utilities,
health probes, and offline tests run reliably without fake or dummy secrets.
"""

from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    """Application settings with resilient, deferred validation."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API Keys (Optional at startup; validated on first agent tool access)
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    gemini_fallback_model: str = Field(default="gemini-2.5-flash-lite", alias="GEMINI_FALLBACK_MODEL")
    gemini_rpm: int = Field(default=4, alias="GEMINI_RPM")
    tavily_api_key: Optional[str] = Field(default=None, alias="TAVILY_API_KEY")
    github_token: Optional[str] = Field(default=None, alias="GITHUB_TOKEN")

    # Timeouts & Limits
    max_run_seconds: int = Field(default=180, alias="MAX_RUN_SECONDS")
    fetch_timeout_seconds: int = Field(default=10, alias="FETCH_TIMEOUT_SECONDS")
    max_candidates: int = Field(default=15, alias="MAX_CANDIDATES")

    # Server configs
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    def require_gemini_key(self) -> str:
        """Return GEMINI_API_KEY or raise clear, human-readable error."""
        if not self.gemini_api_key or not self.gemini_api_key.strip():
            raise ValueError(
                "Missing GEMINI_API_KEY. Please provide a valid Gemini API key in backend/.env "
                "or export it in your environment before invoking LLM agents."
            )
        return self.gemini_api_key.strip()

    def require_tavily_key(self) -> str:
        """Return TAVILY_API_KEY or raise clear, human-readable error."""
        if not self.tavily_api_key or not self.tavily_api_key.strip():
            raise ValueError(
                "Missing TAVILY_API_KEY. Tavily is the primary live search tool. "
                "Set TAVILY_API_KEY in backend/.env or configure fallback behavior."
            )
        return self.tavily_api_key.strip()


# Global singleton settings instance
settings = Settings()
