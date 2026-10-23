/**
 * API Client & SSE Event Stream Manager
 * Features:
 * - Robust error handling
 * - Named SSE event listener binding (sse-starlette compatibility)
 * - Auto-reconnection with replay support
 * - Clean teardown
 */

const API_BASE = '/api';

export async function fetchSystemStatus() {
  const resp = await fetch(`${API_BASE}/status`);
  if (!resp.ok) {
    throw new Error(`Failed to check system status: ${resp.status} ${resp.statusText}`);
  }
  return await resp.json();
}

export async function launchSwarmRun(formData) {
  const resp = await fetch(`${API_BASE}/runs`, {
    method: 'POST',
    body: formData,
  });
  if (!resp.ok) {
    let errDetail = `${resp.status} ${resp.statusText}`;
    try {
      const errJson = await resp.json();
      if (errJson.detail) errDetail = errJson.detail;
    } catch (_) {}
    throw new Error(errDetail);
  }
  return await resp.json();
}

export async function rerunSwarm(runId, formData) {
  const resp = await fetch(`${API_BASE}/runs/${encodeURIComponent(runId)}/rerun`, {
    method: 'POST',
    body: formData,
  });
  if (!resp.ok) {
    let errDetail = `${resp.status} ${resp.statusText}`;
    try {
      const errJson = await resp.json();
      if (errJson.detail) errDetail = errJson.detail;
    } catch (_) {}
    throw new Error(errDetail);
  }
  return await resp.json();
}

export async function fetchRunDetails(runId) {
  const resp = await fetch(`${API_BASE}/runs/${encodeURIComponent(runId)}`);
  if (!resp.ok) {
    throw new Error(`Failed to fetch run details for ${runId}: ${resp.status}`);
  }
  return await resp.json();
}

export async function fetchRunsHistory() {
  const resp = await fetch(`${API_BASE}/runs`);
  if (!resp.ok) {
    throw new Error(`Failed to fetch runs history: ${resp.status}`);
  }
  return await resp.json();
}

export function getReportDownloadUrl(runId) {
  return `${API_BASE}/runs/${encodeURIComponent(runId)}/report.md`;
}

export function getTraceDownloadUrl(runId) {
  return `${API_BASE}/runs/${encodeURIComponent(runId)}/trace.json`;
}

/**
 * Connect to SSE event stream with support for named events and auto-reconnect.
 */
export function streamRunEvents(runId, { onEvent, onComplete, onError }) {
  let eventSource = null;
  let isClosed = false;
  let reconnectAttempts = 0;
  const maxReconnectAttempts = 5;

  function connect() {
    if (isClosed) return;

    const sseUrl = `${API_BASE}/runs/${encodeURIComponent(runId)}/events`;
    eventSource = new EventSource(sseUrl);

    // List of known event types emitted by FastAPI/sse-starlette
    const eventTypes = [
      'message',
      'agent_event',
      'start',
      'progress',
      'finish',
      'error',
      'warning',
      'handoff',
      'info',
      'complete',
    ];

    function handleEventPayload(e, eventType) {
      if (isClosed) return;
      reconnectAttempts = 0; // reset backoff on successful packet

      if (eventType === 'complete') {
        cleanup();
        if (onComplete) onComplete({ run_id: runId });
        return;
      }

      try {
        const parsed = JSON.parse(e.data);
        if (parsed.event_type === 'complete' || (parsed.payload && parsed.payload.event === 'complete')) {
          cleanup();
          if (onComplete) onComplete({ run_id: runId, result: parsed });
          return;
        }
        if (onEvent) onEvent(parsed);
      } catch (err) {
        console.warn('Non-JSON event payload:', e.data);
      }
    }

    eventTypes.forEach((type) => {
      eventSource.addEventListener(type, (e) => handleEventPayload(e, type));
    });
    eventSource.onmessage = (e) => handleEventPayload(e, 'message');

    eventSource.onopen = () => {
      reconnectAttempts = 0;
    };

    eventSource.onerror = (err) => {
      if (isClosed) return;

      if (eventSource.readyState === EventSource.CLOSED) {
        if (reconnectAttempts < maxReconnectAttempts) {
          reconnectAttempts++;
          const backoffMs = Math.min(1000 * Math.pow(1.5, reconnectAttempts), 5000);
          console.warn(`SSE stream disconnected. Reconnecting in ${backoffMs}ms (attempt ${reconnectAttempts})…`);
          eventSource.close();
          setTimeout(connect, backoffMs);
        } else {
          cleanup();
          if (onError) onError(new Error('Event stream lost connection. Max retries exceeded.'));
        }
      }
    };
  }

  function cleanup() {
    isClosed = true;
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  connect();

  return {
    close: cleanup,
  };
}
