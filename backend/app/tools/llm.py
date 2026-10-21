"""Gemini LLM client via google-genai SDK.

Features:
- Structured output using Pydantic schemas (response_schema / json mode).
- Defensive one-shot JSON repair loop when output validation fails.
- Exponential backoff retry on 429 rate limit or 5xx transient server errors.
- Async semaphore concurrency limiter (default 3 concurrent requests).
- Token usage & call counter logging in run Telemetry.
- Clear, human-readable model error handling on unrecognized model names.
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, Optional, Type, TypeVar
from pydantic import BaseModel, ValidationError
from google import genai
from google.genai import types
from google.genai.errors import APIError
from app.config import settings
from app.tools.telemetry import Telemetry


logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Global semaphore to protect free-tier rate limits
DEFAULT_CONCURRENCY_LIMIT = 3
_llm_semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY_LIMIT)


class GeminiClient:
    """Production-grade LLM wrapper with structured outputs, repair, and rate-limit guardrails."""

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self._explicit_key = api_key
        self._model_name = model_name or settings.gemini_model
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
        max_retries: int = 3,
    ) -> str:
        """Generate unstructured markdown/text response with exponential backoff on errors."""
        client = self._get_client()
        model = self._model_name

        config = types.GenerateContentConfig()
        if system_instruction:
            config.system_instruction = system_instruction

        async with _llm_semaphore:
            for attempt in range(1, max_retries + 1):
                try:
                    if telemetry:
                        telemetry.increment("llm_calls")
                    # Run synchronous google-genai call in worker thread
                    response = await asyncio.to_thread(
                        client.models.generate_content,
                        model=model,
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

                    return response.text or ""

                except APIError as exc:
                    err_str = str(exc)
                    # Check for invalid model error
                    if "models/" in err_str and ("not found" in err_str.lower() or "not supported" in err_str.lower()):
                        raise ValueError(
                            f"Gemini model '{model}' was not found or is not supported. "
                            f"Please update GEMINI_MODEL in backend/.env to a supported model (e.g. gemini-2.5-flash)."
                        ) from exc

                    if attempt < max_retries and ("429" in err_str or "503" in err_str or "RESOURCE_EXHAUSTED" in err_str):
                        backoff = 2.0 ** attempt
                        if telemetry:
                            telemetry.record_retry(
                                component="gemini_llm",
                                attempt=attempt,
                                reason=f"Rate limit / transient error: {err_str[:120]}",
                                backoff_seconds=backoff,
                            )
                        await asyncio.sleep(backoff)
                        continue
                    if telemetry:
                        telemetry.record_failure("gemini_llm", error=err_str, fatal=True)
                    raise

                except Exception as exc:
                    if telemetry:
                        telemetry.record_failure("gemini_llm", error=str(exc), fatal=True)
                    raise

        return ""

    async def generate_json(
        self,
        prompt: str,
        schema: Type[T],
        system_instruction: Optional[str] = None,
        telemetry: Optional[Telemetry] = None,
        max_retries: int = 3,
    ) -> T:
        """Generate structured JSON adhering to a Pydantic schema with defensive repair."""
        client = self._get_client()
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

        model = self._model_name

        async with _llm_semaphore:

            for attempt in range(1, max_retries + 1):
                try:
                    if telemetry:
                        telemetry.increment("llm_calls")
                    response = await asyncio.to_thread(
                        client.models.generate_content,
                        model=model,
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
                    try:
                        # Attempt direct Pydantic validation
                        return schema.model_validate_json(raw_text)
                    except (ValidationError, json.JSONDecodeError) as parse_err:
                        logger.warning(
                            "LLM JSON validation failed on first pass: %s. Initiating repair...",
                            parse_err,
                        )
                        if telemetry:
                            telemetry.record_retry(
                                component="gemini_json_repair",
                                attempt=1,
                                reason=f"Schema validation error: {str(parse_err)[:150]}",
                            )

                        # ONE REPAIR ATTEMPT: Send bad output back with error message
                        repair_prompt = (
                            f"The following JSON output failed schema validation:\n"
                            f"```json\n{raw_text}\n```\n\n"
                            f"Error message:\n{str(parse_err)}\n\n"
                            f"Target JSON schema specification:\n"
                            f"{json.dumps(schema.model_json_schema(), indent=2)}\n\n"
                            f"Please return ONLY the corrected, strictly valid JSON conforming to the schema."
                        )
                        repaired_resp = await asyncio.to_thread(
                            client.models.generate_content,
                            model=model,
                            contents=repair_prompt,
                            config=config,
                        )
                        repaired_text = repaired_resp.text or "{}"
                        return schema.model_validate_json(repaired_text)

                except APIError as exc:
                    err_str = str(exc)
                    if "models/" in err_str and ("not found" in err_str.lower() or "not supported" in err_str.lower()):
                        raise ValueError(
                            f"Gemini model '{model}' was not found or is not supported. "
                            f"Please update GEMINI_MODEL in backend/.env to a supported model (e.g. gemini-2.5-flash)."
                        ) from exc

                    if attempt < max_retries and ("429" in err_str or "503" in err_str or "RESOURCE_EXHAUSTED" in err_str):
                        backoff = 2.0 ** attempt
                        if telemetry:
                            telemetry.record_retry(
                                component="gemini_llm_json",
                                attempt=attempt,
                                reason=f"Rate limit: {err_str[:120]}",
                                backoff_seconds=backoff,
                            )
                        await asyncio.sleep(backoff)
                        continue
                    if telemetry:
                        telemetry.record_failure("gemini_llm_json", error=err_str, fatal=True)
                    raise

                except Exception as exc:
                    if attempt == max_retries:
                        if telemetry:
                            telemetry.record_failure("gemini_llm_json", error=str(exc), fatal=True)
                        raise

        raise RuntimeError(f"Failed to generate structured JSON with model {model} after {max_retries} attempts.")


# Global default Gemini client
llm_client = GeminiClient()
