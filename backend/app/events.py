"""Event model and in-memory asynchronous EventBus for agent swarm telemetry.

Enables real-time SSE streaming from LangGraph orchestrator to the frontend UI
with zero lag, typed event schemas, and run-level history buffer for replays.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


EventLevel = Literal["info", "warn", "error", "success"]


class AgentEvent(BaseModel):
    """Structured telemetry event emitted by swarm agents during execution."""

    run_id: str
    agent: str
    message: str
    level: EventLevel = "info"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: Optional[Dict[str, Any]] = None


class EventBus:
    """Asynchronous pub/sub event bus with per-run subscriptions and history buffers."""

    def __init__(self) -> None:
        # run_id -> list of active subscriber Queues
        self._subscribers: Dict[str, List[asyncio.Queue[Optional[AgentEvent]]]] = {}
        # run_id -> sequential history of all published events (for reconnection & replay)
        self._history: Dict[str, List[AgentEvent]] = {}
        self._closed_runs: set[str] = set()
        self._lock = asyncio.Lock()

    async def publish(self, event: AgentEvent) -> None:
        """Publish an event to all active listeners and record it in run history."""
        async with self._lock:
            # Append to history
            if event.run_id not in self._history:
                self._history[event.run_id] = []
            self._history[event.run_id].append(event)

            # Broadcast to subscribers
            subscribers = self._subscribers.get(event.run_id, [])
            for queue in subscribers:
                await queue.put(event)

    async def subscribe(
        self, run_id: str, replay_history: bool = True
    ) -> AsyncIterator[AgentEvent]:
        """Subscribe to live stream for a run_id, yielding events as they happen."""
        queue: asyncio.Queue[Optional[AgentEvent]] = asyncio.Queue()

        async with self._lock:
            if run_id not in self._subscribers:
                self._subscribers[run_id] = []
            self._subscribers[run_id].append(queue)

            # Optionally replay past history for reconnecting clients
            if replay_history and run_id in self._history:
                for past_event in self._history[run_id]:
                    await queue.put(past_event)

            # If run has already ended, enqueue sentinel None so iterator terminates cleanly
            if run_id in self._closed_runs:
                await queue.put(None)

        try:
            while True:
                event = await queue.get()
                if event is None:
                    # Sentinel value signaling end of stream
                    break
                yield event
        finally:
            async with self._lock:
                if run_id in self._subscribers and queue in self._subscribers[run_id]:
                    self._subscribers[run_id].remove(queue)
                    if not self._subscribers[run_id]:
                        self._subscribers.pop(run_id, None)

    async def close_run(self, run_id: str) -> None:
        """Signal all subscribers for a run_id that the event stream has ended."""
        async with self._lock:
            self._closed_runs.add(run_id)
            subscribers = self._subscribers.get(run_id, [])
            for queue in subscribers:
                await queue.put(None)

    def get_history(self, run_id: str) -> List[AgentEvent]:
        """Return full history buffer for a given run_id."""
        return list(self._history.get(run_id, []))

    def replay(self, run_id: str) -> List[AgentEvent]:
        """Return full history buffer for a given run_id (alias for replay)."""
        return self.get_history(run_id)


# Global event bus singleton instance
event_bus = EventBus()
