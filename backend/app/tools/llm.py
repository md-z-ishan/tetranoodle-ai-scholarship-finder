"""Gemini LLM client via google-genai SDK.

Features:
- Structured output using Pydantic schemas (response_schema / json mode).
- Defensive one-shot JSON repair loop when output validation fails.
- Exponential backoff with jitter on 429 / 503 transient errors.
- Retry-After header respected when present in the API error.
- Shared retry budget across primary + fallback model to avoid storm.
- Async semaphore concurrency limiter + global token-bucket rate limiter (GEMINI_RPM).
- On-disk persistent prompt cache in data/cache/ (hash of model + prompt + schema).
- Fallback model (GEMINI_FALLBACK_MODEL) triggered upon repeated 429 quota exhaustion.
- Token usage & call counter logging in run Telemetry.
- Clear, human-readable model error handling on unrecognized model names.
- QuotaExhaustedError raised when total retry budget is spent on both models.
"""

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Type, TypeVar

from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.tools.telemetry import Telemetry


logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Default concurrency limit
DEFAULT_CONCURRENCY_LIMIT = 3
_llm_semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY_LIMIT)

# Disk cache directory
CACHE_DIR = Path("data/cache")

# ---------------------------------------------------------------------------
# Retry budget constants
# ---------------------------------------------------------------------------
# Total number of attempts across primary + fallback combined.
# e.g. 4 means: try primary up to 2 times, switch to fallback, try fallback up to 2 more times.
DEFAULT_MAX_RETRIES = 4
# Max backoff ceiling (seconds) — caps exponential growth to avoid blocking run for minutes
MAX_BACKOFF_SECONDS = 30.0
# Jitter fraction applied to backoff to desynchronise parallel callers
JITTER_FACTOR = 0.3


# ---------------------------------------------------------------------------
# Quota exhaustion sentinel
# ---------------------------------------------------------------------------

class QuotaExhaustedError(RuntimeError):
    """Raised when both primary and fallback Gemini models are quota-exhausted
    and the total retry budget has been spent.  Callers MUST catch this and
    degrade gracefully — never silently swallow or retry."""
    pass


# ---------------------------------------------------------------------------
# Token Bucket / Rate Limiter (paced by GEMINI_RPM)
# ---------------------------------------------------------------------------

class TokenBucketRateLimiter:
    """Thread-safe, async rate limiter ensuring requests do not exceed RPM ceiling."""

    def __init__(self, rpm: int = 4):
        self.rpm = max(1, rpm)
        self.interval = 60.0 / self.rpm
        self._lock = asyncio.Lock()
        self._last_request_time = 0.0

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.interval:
                sleep_needed = self.interval - elapsed
                await asyncio.sleep(sleep_needed)
            self._last_request_time = time.monotonic()


_rate_limiter = TokenBucketRateLimiter(rpm=settings.gemini_rpm)


# ---------------------------------------------------------------------------
# Backoff helper
# ---------------------------------------------------------------------------

def _compute_backoff(attempt: int, error_str: str) -> float:
    """Exponential backoff with jitter, respecting Retry-After if present."""
    # Try to parse Retry-After from error text (Google API sometimes includes it)
    retry_after_match = re.search(r"[Rr]etry.?[Aa]fter[\":\s]+(\d+)", error_str)
    if retry_after_match:
        base = float(retry_after_match.group(1))
    else:
        base = min(2.0 ** attempt, MAX_BACKOFF_SECONDS)
    jitter = random.uniform(0, JITTER_FACTOR * base)
    return round(base + jitter, 2)


def _is_quota_error(err_str: str) -> bool:
    return any(k in err_str for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE"))


# ---------------------------------------------------------------------------
# Disk Cache Helpers
# ---------------------------------------------------------------------------

def _get_cache_path(model: str, prompt: str, system_instruction: Optional[str], schema_str: str) -> Path:
    """Compute deterministic SHA256 cache path for a prompt."""
    payload = f"{model}:::{system_instruction or ''}:::{schema_str}:::{prompt}"
    key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{key}.json"


def _read_cache(cache_path: Path) -> Optional[str]:
    """Read cached JSON/text if present and valid."""
    try:
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("Cache read failed for %s: %s", cache_path, exc)
    return None


def _write_cache(cache_path: Path, content: str):
    """Write output content to cache file safely."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.debug("Cache write failed for %s: %s", cache_path, exc)


# ---------------------------------------------------------------------------
# Gemini Client
# ---------------------------------------------------------------------------

class GeminiClient:
    """Production-grade LLM wrapper with structured outputs, repair, and rate-limit guardrails.

    Retry policy (Phase 8):
    - Total attempts shared across primary + fallback model = max_retries (default 4).
    - Fallback model is activated after attempt >= 2 on primary.
    - On each retryable error: exponential backoff with jitter, Retry-After respected.
    - When budget is exhausted on both models: raises QuotaExhaustedError (not RuntimeError).
    - 503 treated identically to 429 (service unavailable = transient quota pressure).
    """

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self._explicit_key = api_key
        self._model_name = model_name or settings.gemini_model
        self._fallback_model = settings.gemini_fallback_model
        self._client: Optional[genai.Client] = None

    def _get_client(self) -> genai.Client:
        """Instantiate genai.Client on first call (deferred validation)."""
        if self._client is None:
            key = self._explicit_key or settings.require_gemini_key()
            self._client = genai.Client(api_key=key)
        return self._client

    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        telemetry: Optional[Telemetry] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> str:
        """Generate unstructured markdown/text response with rate limiting, cache, and fallback.

        Raises:
            QuotaExhaustedError: when all retry budget is spent on quota errors.
            ValueError: when the model name is invalid.
        """
        cache_path = _get_cache_path(self._model_name, prompt, system_instruction, "text")
        cached = _read_cache(cache_path)
        if cached is not None:
            if telemetry:
                telemetry.increment("llm_cache_hits")
            return cached

        client = self._get_client()
        active_model = self._model_name
        fallback_activated = False

        config = types.GenerateContentConfig()
        if system_instruction:
            config.system_instruction = system_instruction

        last_error: Optional[Exception] = None

        async with _llm_semaphore:
            for attempt in range(1, max_retries + 1):
                await _rate_limiter.acquire()
                try:
                    if telemetry:
                        telemetry.increment("llm_calls")
                    response = await asyncio.to_thread(
                        client.models.generate_content,
                        model=active_model,
                        contents=prompt,
                        config=config,
                    )

                    if telemetry and response.usage_metadata:
                        telemetry.increment(
                            "llm_prompt_tokens",
                            getattr(response.usage_metadata, "prompt_token_count", 0) or 0,
                        )
                        telemetry.increment(
                            "llm_response_tokens",
                            getattr(response.usage_metadata, "candidates_token_count", 0) or 0,
                        )

                    text_res = response.text or ""
                    _write_cache(cache_path, text_res)
                    return text_res

                except APIError as exc:
                    err_str = str(exc)
                    last_error = exc

                    # Hard stop on unknown model — no point retrying
                    if "models/" in err_str and (
                        "not found" in err_str.lower() or "not supported" in err_str.lower()
                    ):
                        raise ValueError(
                            f"Gemini model '{active_model}' was not found or is not supported. "
                            f"Please update GEMINI_MODEL in backend/.env to a supported model (e.g. gemini-2.5-flash)."
                        ) from exc

                    if _is_quota_error(err_str):
                        # Switch to fallback model after first failure on primary
                        if not fallback_activated and active_model != self._fallback_model:
                            fallback_activated = True
                            active_model = self._fallback_model
                            if telemetry:
                                telemetry.record_fallback(
                                    component="gemini_llm",
                                    from_provider=self._model_name,
                                    to_provider=self._fallback_model,
                                    reason=f"Quota error on {self._model_name}; switched to {self._fallback_model}",
                                )
                            logger.warning(
                                "generate_text: switched to fallback model %s after quota error on %s (attempt %d)",
                                self._fallback_model, self._model_name, attempt,
                            )

                        if attempt < max_retries:
                            backoff = _compute_backoff(attempt, err_str)
                            if telemetry:
                                telemetry.record_retry(
                                    component="gemini_llm",
                                    attempt=attempt,
                                    reason=f"Quota/unavailable ({err_str[:80]}); backoff {backoff}s",
                                    backoff_seconds=backoff,
                                )
                            logger.debug("generate_text: backoff %.1fs (attempt %d/%d)", backoff, attempt, max_retries)
                            await asyncio.sleep(backoff)
                            continue

                        # Budget exhausted
                        if telemetry:
                            telemetry.record_failure(
                                "gemini_llm",
                                error=f"QuotaExhausted after {max_retries} attempts: {err_str[:120]}",
                                fatal=True,
                            )
                        raise QuotaExhaustedError(
                            f"Gemini quota exhausted after {max_retries} attempts "
                            f"(primary={self._model_name}, fallback={self._fallback_model}): {err_str[:200]}"
                        ) from exc

                    # Non-quota API error — record and re-raise immediately
                    if telemetry:
                        telemetry.record_failure("gemini_llm", error=err_str, fatal=True)
                    raise

        raise QuotaExhaustedError(f"generate_text: retry budget exhausted. Last error: {last_error}")

    async def generate_json(
        self,
        prompt: str,
        schema: Type[T],
        system_instruction: Optional[str] = None,
        telemetry: Optional[Telemetry] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> T:
        """Generate structured JSON adhering to a Pydantic schema with defensive cache, repair, and rate limits.

        Raises:
            QuotaExhaustedError: when all retry budget is spent on quota errors.
            RuntimeError: when JSON repair also fails.
        """
        # Check disk cache first
        schema_json_str = json.dumps(schema.model_json_schema(), sort_keys=True)
        cache_path = _get_cache_path(self._model_name, prompt, system_instruction, schema_json_str)
        cached = _read_cache(cache_path)
        if cached is not None:
            try:
                parsed_cached = schema.model_validate_json(cached)
                if telemetry:
                    telemetry.increment("llm_cache_hits")
                return parsed_cached
            except ValidationError:
                pass  # stale or corrupted cache, proceed to live call

        client = self._get_client()
        active_model = self._model_name
        fallback_activated = False

        # Inline all $ref definitions so Gemini SDK t_schema never sees dangling refs.
        def _inline_refs(d: Any, defs: Dict[str, Any]) -> Any:
            if isinstance(d, dict):
                if "$ref" in d:
                    ref_name = d["$ref"].split("/")[-1]
                    resolved = _inline_refs(defs.get(ref_name, {}), defs)
                    return resolved
                return {k: _inline_refs(v, defs) for k, v in d.items()
                        if k not in ("additionalProperties", "$defs", "definitions", "$schema", "title")}
            elif isinstance(d, list):
                return [_inline_refs(x, defs) for x in d]
            return d

        raw_schema = schema.model_json_schema()
        defs = raw_schema.get("$defs", raw_schema.get("definitions", {}))
        cleaned_schema = _inline_refs(raw_schema, defs)

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=cleaned_schema,
        )
        if system_instruction:
            config.system_instruction = system_instruction

        last_error: Optional[Exception] = None
        raw_text: Optional[str] = None

        async with _llm_semaphore:
            for attempt in range(1, max_retries + 1):
                await _rate_limiter.acquire()
                try:
                    if telemetry:
                        telemetry.increment("llm_calls")
                    response = await asyncio.to_thread(
                        client.models.generate_content,
                        model=active_model,
                        contents=prompt,
                        config=config,
                    )

                    if telemetry and response.usage_metadata:
                        telemetry.increment(
                            "llm_prompt_tokens",
                            getattr(response.usage_metadata, "prompt_token_count", 0) or 0,
                        )
                        telemetry.increment(
                            "llm_response_tokens",
                            getattr(response.usage_metadata, "candidates_token_count", 0) or 0,
                        )

                    raw_text = response.text or "{}"
                    result = schema.model_validate_json(raw_text)
                    _write_cache(cache_path, raw_text)
                    return result

                except APIError as exc:
                    err_str = str(exc)
                    last_error = exc

                    # Hard stop on unknown model
                    if "models/" in err_str and (
                        "not found" in err_str.lower() or "not supported" in err_str.lower()
                    ):
                        raise ValueError(
                            f"Gemini model '{active_model}' was not found or is not supported. "
                            f"Please update GEMINI_MODEL in backend/.env to a supported model (e.g. gemini-2.5-flash)."
                        ) from exc

                    if _is_quota_error(err_str):
                        # Switch to fallback after first quota failure on primary
                        if not fallback_activated and active_model != self._fallback_model:
                            fallback_activated = True
                            active_model = self._fallback_model
                            # Reconfigure schema for new model
                            config = types.GenerateContentConfig(
                                response_mime_type="application/json",
                                response_schema=cleaned_schema,
                            )
                            if system_instruction:
                                config.system_instruction = system_instruction
                            if telemetry:
                                telemetry.record_fallback(
                                    component="gemini_llm_json",
                                    from_provider=self._model_name,
                                    to_provider=self._fallback_model,
                                    reason=f"Quota error on {self._model_name}; switched to {self._fallback_model}",
                                )
                            logger.warning(
                                "generate_json: switched to fallback model %s (attempt %d)",
                                self._fallback_model, attempt,
                            )

                        if attempt < max_retries:
                            backoff = _compute_backoff(attempt, err_str)
                            if telemetry:
                                telemetry.record_retry(
                                    component="gemini_llm_json",
                                    attempt=attempt,
                                    reason=f"Quota/unavailable ({err_str[:80]}); backoff {backoff}s",
                                    backoff_seconds=backoff,
                                )
                            logger.debug("generate_json: backoff %.1fs (attempt %d/%d)", backoff, attempt, max_retries)
                            await asyncio.sleep(backoff)
                            continue

                        # Budget exhausted
                        if telemetry:
                            telemetry.record_failure(
                                "gemini_llm_json",
                                error=f"QuotaExhausted after {max_retries} attempts: {err_str[:120]}",
                                fatal=True,
                            )
                        raise QuotaExhaustedError(
                            f"Gemini quota exhausted after {max_retries} attempts "
                            f"(primary={self._model_name}, fallback={self._fallback_model}): {err_str[:200]}"
                        ) from exc

                    # Non-quota API error
                    last_error = exc
                    break

                except (ValidationError, json.JSONDecodeError) as exc:
                    logger.warning("Structured output validation failed on attempt %d: %s", attempt, exc)
                    last_error = exc
                    break

        # Defensive JSON Repair Loop (only reached if LLM returned something but validation failed)
        if raw_text:
            try:
                cleaned_text = raw_text.strip()
                match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned_text)
                if match:
                    cleaned_text = match.group(1).strip()
                repaired = schema.model_validate_json(cleaned_text)
                _write_cache(cache_path, cleaned_text)
                return repaired
            except Exception:
                pass

            # One-shot LLM repair (only if we got a response but it failed validation)
            repair_prompt = (
                f"The following JSON response failed validation for the requested schema.\n"
                f"Please fix syntax or structure errors and output strictly valid JSON:\n\n"
                f"{raw_text}\n\nSchema Requirements:\n{schema.model_json_schema()}"
            )
            try:
                if telemetry:
                    telemetry.record_retry(
                        component="gemini_json_repair",
                        attempt=1,
                        reason="Schema validation failed; invoking one-shot repair",
                    )
                repair_config = types.GenerateContentConfig(response_mime_type="application/json")
                repair_res = await asyncio.to_thread(
                    client.models.generate_content,
                    model=active_model,
                    contents=repair_prompt,
                    config=repair_config,
                )
                repaired_text = repair_res.text or "{}"
                result = schema.model_validate_json(repaired_text)
                _write_cache(cache_path, repaired_text)
                return result
            except Exception as repair_exc:
                logger.error("JSON repair attempt failed: %s", repair_exc)

        if telemetry:
            telemetry.record_failure("gemini_llm_json", error=str(last_error), fatal=True)
        raise RuntimeError(f"Failed to generate structured JSON: {last_error}")


llm_client = GeminiClient()
