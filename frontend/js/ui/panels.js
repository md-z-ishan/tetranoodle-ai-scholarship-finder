/**
 * Audit Panels & Health Telemetry Component
 * Manages:
 * 1. "What We Could Not Verify" list
 * 2. "What We Need From You" form & live rerun action
 * 3. Run Health telemetry counters & agent latency charts
 */

import { state } from '../state.js';
import { rerunSwarm, streamRunEvents, fetchRunDetails } from '../api.js';
import { showToast } from './toast.js';

const unverifiedContainer = document.getElementById('unverified-panel-container');
const unverifiedList = document.getElementById('unverified-reasons-list');
const needsContainer = document.getElementById('needs-panel-container');
const needsInputs = document.getElementById('needs-inputs-container');
const rerunBtn = document.getElementById('btn-rerun-needs');

export function initPanels() {
  if (rerunBtn) {
    rerunBtn.addEventListener('click', handleRerun);
  }

  state.subscribe((eventType) => {
    if (eventType === 'RESULT_READY' || eventType === 'RUN_START' || eventType === 'RESET_ALL') {
      renderAuditPanels();
      renderHealthMetrics();
    }
  });
}

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function renderAuditPanels() {
  const res = state.result;
  if (!res || (!res.what_we_could_not_verify?.length && !res.needs_from_student?.length)) {
    if (unverifiedContainer) unverifiedContainer.classList.add('hidden');
    return;
  }

  if (unverifiedContainer) unverifiedContainer.classList.remove('hidden');

  // 1. What We Could Not Verify
  if (unverifiedList) {
    const unverified = res.what_we_could_not_verify || [];
    if (unverified.length > 0) {
      unverifiedList.innerHTML = unverified.map(item => `
        <li>${escapeHtml(item)}</li>
      `).join('');
    } else {
      unverifiedList.innerHTML = '<li class="text-muted">All primary institutional requirements were verified successfully.</li>';
    }
  }

  // 2. What We Need From You
  if (needsInputs) {
    const needs = res.needs_from_student || [];
    if (needs.length > 0) {
      needsInputs.innerHTML = needs.map((item, idx) => {
        const fieldName = `need_input_${idx}`;
        return `
          <div class="form-group mt-2">
            <label class="form-label" for="${fieldName}" style="font-size:11px;">
              ${escapeHtml(item)}
            </label>
            <input type="text" id="${fieldName}" name="${fieldName}" data-need="${escapeHtml(item)}" class="form-control select-xs" placeholder="Provide clarification…">
          </div>
        `;
      }).join('');
      if (needsContainer) needsContainer.classList.remove('hidden');
    } else {
      needsInputs.innerHTML = '<p class="text-xs text-muted">All necessary candidate profile parameters were extracted.</p>';
    }
  }
}

async function handleRerun() {
  if (!state.currentRunId) {
    showToast('No active or previous run available to re-run.', 'warn');
    return;
  }

  const formData = new FormData();
  
  // Collect clarifications from inputs
  const inputEls = needsInputs ? needsInputs.querySelectorAll('input[data-need]') : [];
  const notes = [];
  inputEls.forEach(inp => {
    const val = inp.value.trim();
    if (val) {
      notes.push(`${inp.dataset.need}: ${val}`);
      // If nationality is provided, pass explicitly
      if (inp.dataset.need.toLowerCase().includes('nationality')) {
        formData.append('nationality', val);
      }
      if (inp.dataset.need.toLowerCase().includes('degree')) {
        formData.append('degree_level', val);
      }
      if (inp.dataset.need.toLowerCase().includes('country')) {
        formData.append('target_countries', val);
      }
    }
  });

  if (notes.length > 0) {
    formData.append('budget_note', notes.join(' | '));
  }

  try {
    rerunBtn.disabled = true;
    rerunBtn.textContent = 'Launching Rerun…';
    showToast('Submitting clarifications and restarting swarm…', 'info');

    const res = await rerunSwarm(state.currentRunId, formData);
    const newRunId = res.run_id;

    state.resetForNewRun(newRunId);
    showToast(`Rerun started (${newRunId})`, 'success');

    // Subscribe to SSE stream
    streamRunEvents(newRunId, {
      onEvent: (event) => state.addEvent(event),
      onComplete: async () => {
        showToast('Rerun completed!', 'success');
        const details = await fetchRunDetails(newRunId);
        if (details.result) {
          state.setResult(details.result);
        }
      },
      onError: (err) => {
        showToast(`Rerun stream error: ${err.message}`, 'error');
      },
    });

  } catch (err) {
    showToast(`Failed to rerun swarm: ${err.message}`, 'error');
  } finally {
    rerunBtn.disabled = false;
    rerunBtn.textContent = 'Re-run Swarm With Answers';
  }
}

export function renderHealthMetrics() {
  const res = state.result;
  const health = (res && res.run_health) ? res.run_health : {};
  const counters = health.counters || {};
  const timings = (res && res.timings) ? res.timings : (health.timings || {});

  // Health Counters
  const elLlm = document.getElementById('metric-llm-calls');
  const elHits = document.getElementById('metric-cache-hits');
  const elFallbacks = document.getElementById('metric-model-fallbacks');
  const elRetries = document.getElementById('metric-retries');

  if (elLlm) elLlm.textContent = counters.llm_calls || 0;
  if (elHits) elHits.textContent = counters.llm_cache_hits || 0;
  if (elFallbacks) elFallbacks.textContent = health.fallbacks_count || 0;
  if (elRetries) elRetries.textContent = health.retries_count || 0;

  // Fetch Distribution
  const elDirect = document.getElementById('fetch-count-direct');
  const elRetry = document.getElementById('fetch-count-retry');
  const elTavily = document.getElementById('fetch-count-tavily');

  let directCount = 0;
  let retryCount = 0;
  let tavilyCount = 0;

  if (res && res.verified) {
    for (const v of res.verified) {
      if (v.fetched_via === 'direct') directCount++;
      else if (v.fetched_via === 'headers_retry') retryCount++;
      else if (v.fetched_via === 'tavily_extract') tavilyCount++;
    }
  }

  if (elDirect) elDirect.textContent = directCount;
  if (elRetry) elRetry.textContent = retryCount;
  if (elTavily) elTavily.textContent = tavilyCount;

  // Timings Breakdown Chart
  const timingsContainer = document.getElementById('timings-bars-container');
  if (timingsContainer) {
    const entries = Object.entries(timings);
    if (entries.length === 0) {
      timingsContainer.innerHTML = '<div class="text-muted text-xs">Run a search to display agent execution timing bars.</div>';
    } else {
      const maxTime = Math.max(...entries.map(([, t]) => Number(t)), 1.0);
      timingsContainer.innerHTML = entries.map(([agent, t]) => {
        const timeVal = Number(t).toFixed(1);
        const pct = Math.min(100, Math.round((timeVal / maxTime) * 100));
        return `
          <div class="timings-bar-row">
            <span class="text-muted" style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${escapeHtml(agent)}">${escapeHtml(agent)}</span>
            <div class="breakdown-bar">
              <div class="breakdown-bar-fill" style="width:${pct}%;"></div>
            </div>
            <span style="font-family:var(--font-mono); font-weight:700; text-align:right;">${timeVal}s</span>
          </div>
        `;
      }).join('');
    }
  }

  // Warnings & Audits list
  const warningsList = document.getElementById('telemetry-warnings-list');
  if (warningsList) {
    const warnings = (res && res.warnings) ? res.warnings : [];
    if (warnings.length > 0) {
      warningsList.innerHTML = warnings.map(w => `
        <li style="color:var(--status-warn); font-size:11px;">⚠️ ${escapeHtml(w)}</li>
      `).join('');
    } else {
      warningsList.innerHTML = '<li class="text-muted text-xs">No warnings recorded. All components operated nominal.</li>';
    }
  }
}
