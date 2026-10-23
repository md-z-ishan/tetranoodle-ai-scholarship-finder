/**
 * History Drawer Modal Component
 * Allows live review and inspection of prior swarm executions from disk.
 */

import { state } from '../state.js';
import { fetchRunsHistory, fetchRunDetails, streamRunEvents } from '../api.js';
import { showToast } from './toast.js';

const drawer = document.getElementById('history-drawer');
const backdrop = document.getElementById('drawer-backdrop');
const openBtn = document.getElementById('btn-open-history');
const closeBtn = document.getElementById('btn-close-history');
const runsList = document.getElementById('history-runs-list');
const countBadge = document.getElementById('history-count-badge');

export function initHistoryDrawer() {
  if (openBtn) {
    openBtn.addEventListener('click', () => {
      openDrawer();
    });
  }

  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      closeDrawer();
    });
  }

  if (backdrop) {
    backdrop.addEventListener('click', () => {
      closeDrawer();
    });
  }

  // Refresh history count on load
  refreshHistoryList();
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

export function openDrawer() {
  if (drawer) drawer.classList.remove('hidden');
  if (backdrop) backdrop.classList.remove('hidden');
  refreshHistoryList();
}

export function closeDrawer() {
  if (drawer) drawer.classList.add('hidden');
  if (backdrop) backdrop.classList.add('hidden');
}

export async function refreshHistoryList() {
  if (!runsList) return;

  try {
    const data = await fetchRunsHistory();
    const runs = data.runs || [];

    if (countBadge) {
      if (runs.length > 0) {
        countBadge.textContent = runs.length;
        countBadge.classList.remove('hidden');
      } else {
        countBadge.classList.add('hidden');
      }
    }

    if (runs.length === 0) {
      runsList.innerHTML = '<div class="text-center text-muted text-sm py-4">No previous runs saved yet.</div>';
      return;
    }

    runsList.innerHTML = runs.map(run => {
      const runId = run.run_id || 'unknown';
      const studentName = run.student_name || 'Student Candidate';
      const status = (run.status || 'completed').toLowerCase();
      const dateStr = run.created_at ? new Date(run.created_at).toLocaleString() : 'Recent';
      const isCurrent = state.currentRunId === runId;

      return `
        <div class="history-run-item ${isCurrent ? 'history-item-active' : ''}" data-run-id="${escapeHtml(runId)}" role="button" tabindex="0">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
            <span style="font-weight:700; font-size:13px; color:var(--text-primary);">${escapeHtml(studentName)}</span>
            <span class="status-tag ${escapeHtml(status)}">${escapeHtml(status)}</span>
          </div>
          <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--text-muted);">
            <span>${escapeHtml(dateStr)}</span>
            <span style="font-family:var(--font-mono);">${escapeHtml(runId.slice(0, 14))}</span>
          </div>
        </div>
      `;
    }).join('');

    // Bind click listeners on items
    runsList.querySelectorAll('.history-run-item').forEach(el => {
      el.addEventListener('click', async () => {
        const id = el.dataset.runId;
        await loadPastRun(id);
      });
    });

  } catch (err) {
    console.warn('Failed listing runs history:', err);
  }
}

async function loadPastRun(runId) {
  try {
    showToast(`Loading run ${runId}…`, 'info');
    const details = await fetchRunDetails(runId);

    if (details.result) {
      state.currentRunId = runId;
      state.setResult(details.result);
      showToast('Loaded past run results successfully.', 'success');
      closeDrawer();
    } else if (details.status === 'running') {
      state.resetForNewRun(runId);
      showToast('Run is still executing. Reconnecting to live feed…', 'info');
      closeDrawer();

      // Connect SSE
      streamRunEvents(runId, {
        onEvent: (event) => state.addEvent(event),
        onComplete: async () => {
          showToast('Run completed!', 'success');
          const finalDetails = await fetchRunDetails(runId);
          if (finalDetails.result) {
            state.setResult(finalDetails.result);
          }
        },
        onError: (err) => {
          showToast(`Event feed error: ${err.message}`, 'error');
        },
      });
    } else {
      showToast(`Run ${runId} has no completed results saved.`, 'warn');
    }
  } catch (err) {
    showToast(`Could not load run: ${err.message}`, 'error');
  }
}
