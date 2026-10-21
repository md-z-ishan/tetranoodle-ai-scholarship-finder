"""Run telemetry and observability collector for agent swarm runs.

Captures real retries, fallbacks, timeouts, and execution timings so the
UI 'Run Health' panel and exportable trace reflect real system behaviors.
"""

import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
from app.events import AgentEvent


class Telemetry:
    """Collects runtime metrics, failures, fallbacks, and timings for a specific swarm run."""

    def __init__(self, run_id: str = "default-run") -> None:
        self.run_id = run_id
        self.retries: List[Dict[str, Any]] = []
        self.fallbacks: List[Dict[str, Any]] = []
        self.failures: List[Dict[str, Any]] = []
        self.timings: Dict[str, float] = {}
        self.events_log: List[Dict[str, Any]] = []
        self.counters: Dict[str, int] = {
            "retries_count": 0,
            "fallbacks_count": 0,
            "failures_count": 0,
            "llm_calls": 0,
            "llm_prompt_tokens": 0,
            "llm_response_tokens": 0,
            "pages_fetched": 0,
            "search_queries": 0,
        }

    def record_retry(
        self,
        component: str,
        attempt: int,
        reason: str,
        target: Optional[str] = None,
        backoff_seconds: float = 0.0,
    ) -> None:
        """Record an operation retry event with context."""
        entry = {
            "type": "retry",
            "component": component,
            "attempt": attempt,
            "reason": str(reason),
            "target": target,
            "backoff_seconds": backoff_seconds,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.retries.append(entry)
        self.events_log.append(entry)
        self.counters["retries_count"] += 1

    def record_fallback(
        self,
        component: str,
        from_provider: str,
        to_provider: str,
        reason: str,
    ) -> None:
        """Record an automatic fallback to a secondary tool or model."""
        entry = {
            "type": "fallback",
            "component": component,
            "from_provider": from_provider,
            "to_provider": to_provider,
            "reason": str(reason),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.fallbacks.append(entry)
        self.events_log.append(entry)
        self.counters["fallbacks_count"] += 1

    def record_failure(
        self,
        component: str,
        error: str,
        target: Optional[str] = None,
        fatal: bool = False,
    ) -> None:
        """Record a non-fatal or fatal failure encountered by a tool."""
        entry = {
            "type": "failure",
            "component": component,
            "error": str(error),
            "target": target,
            "fatal": fatal,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.failures.append(entry)
        self.events_log.append(entry)
        self.counters["failures_count"] += 1

    def increment(self, counter_name: str, value: int = 1) -> None:
        """Increment a numeric telemetry counter."""
        self.counters[counter_name] = self.counters.get(counter_name, 0) + value

    @contextmanager
    def record_timing(self, operation_name: str):
        """Context manager measuring execution duration in seconds."""
        start = time.perf_counter()
        try:
            yield
        finally:
            duration = round(time.perf_counter() - start, 3)
            # Accumulate or record operation duration
            self.timings[operation_name] = round(
                self.timings.get(operation_name, 0.0) + duration, 3
            )

    def summary(self) -> Dict[str, Any]:
        """Generate consolidated health summary of counts, timings, and chronolog."""
        return {
            "run_id": self.run_id,
            "counters": dict(self.counters),
            "timings": dict(self.timings),
            "retries_count": len(self.retries),
            "fallbacks_count": len(self.fallbacks),
            "failures_count": len(self.failures),
            "retries": list(self.retries),
            "fallbacks": list(self.fallbacks),
            "failures": list(self.failures),
            "events_log": list(self.events_log),
        }


# Type alias for optional async event emitter: Callable[[AgentEvent], Coroutine[Any, Any, None]]
EmitCallback = Optional[Callable[[AgentEvent], Coroutine[Any, Any, None]]]
