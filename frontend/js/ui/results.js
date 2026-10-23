/**
 * Results Panel Coordinator
 * Coordinates tabs, executive summary memo, top recommendation cards, and file downloads.
 */

import { state } from '../state.js';
import { getReportDownloadUrl, getTraceDownloadUrl } from '../api.js';
import { renderRecommendationCard } from './recommendation.js';
import { renderCandidates } from './candidates.js';
import { renderSop } from './sop.js';
import { renderAuditPanels, renderHealthMetrics } from './panels.js';
import { showToast } from './toast.js';

const emptyStateEl = document.getElementById('results-empty-state');
const memoContainer = document.getElementById('executive-memo-container');
const recsListEl = document.getElementById('recommendations-list');
const btnReport = document.getElementById('btn-download-report');
const btnTrace = document.getElementById('btn-download-trace');
const tabButtons = document.querySelectorAll('.nav-tab');
const tabPanes = document.querySelectorAll('.tab-pane');

export function initResultsPanel() {
  // Tab switching
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.dataset.tab;
      switchTab(targetId);
    });
  });

  // Downloads
  if (btnReport) {
    btnReport.addEventListener('click', () => {
      if (!state.currentRunId) {
        showToast('No active run available to download report.', 'warn');
        return;
      }
      window.open(getReportDownloadUrl(state.currentRunId), '_blank');
    });
  }

  if (btnTrace) {
    btnTrace.addEventListener('click', () => {
      if (!state.currentRunId) {
        showToast('No active run available to download trace.', 'warn');
        return;
      }
      window.open(getTraceDownloadUrl(state.currentRunId), '_blank');
    });
  }

  state.subscribe((eventType, data) => {
    if (eventType === 'RUN_START') {
      if (emptyStateEl) emptyStateEl.classList.add('hidden');
      if (memoContainer) memoContainer.classList.add('hidden');
      if (recsListEl) recsListEl.innerHTML = '<div class="text-center text-muted py-4"><span class="btn-spinner" style="display:inline-block; margin-right:8px;"></span> Agents are researching and verifying opportunities…</div>';
      if (btnReport) btnReport.classList.add('hidden');
      if (btnTrace) btnTrace.classList.add('hidden');
    } else if (eventType === 'RESULT_READY') {
      renderAllResults();
    } else if (eventType === 'RESET_ALL') {
      if (emptyStateEl) emptyStateEl.classList.remove('hidden');
      if (memoContainer) memoContainer.classList.add('hidden');
      if (recsListEl) recsListEl.innerHTML = '';
      if (btnReport) btnReport.classList.add('hidden');
      if (btnTrace) btnTrace.classList.add('hidden');
    }
  });
}

export function switchTab(tabId) {
  state.activeTab = tabId;

  tabButtons.forEach(b => {
    const isActive = b.dataset.tab === tabId;
    b.classList.toggle('active', isActive);
    b.setAttribute('aria-selected', isActive ? 'true' : 'false');
  });

  tabPanes.forEach(pane => {
    pane.classList.toggle('active', pane.id === tabId);
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

export function renderAllResults() {
  const res = state.result;
  if (!res) return;

  if (emptyStateEl) emptyStateEl.classList.add('hidden');

  // Show download buttons
  if (btnReport) btnReport.classList.remove('hidden');
  if (btnTrace) btnTrace.classList.remove('hidden');

  // 1. Executive Summary Memo Card
  if (memoContainer) {
    const profile = res.profile || {};
    const dateStr = new Date().toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
    });
    const studentName = profile.name || 'Candidate';
    const gpa = profile.cgpa ? `CGPA ${profile.cgpa}` : 'Transcripts Verified';
    const targetDegree = profile.target_degree_level || profile.degree_level || 'Postgraduate';

    memoContainer.classList.remove('hidden');
    memoContainer.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px; border-bottom:1px solid var(--border-subtle); padding-bottom:6px;">
        <span style="font-weight:800; font-size:13px; color:var(--accent-primary); text-transform:uppercase; letter-spacing:0.05em;">
          📑 Executive Swarm Briefing
        </span>
        <span class="text-xs text-muted">${escapeHtml(dateStr)} &bull; ${escapeHtml(studentName)} (${escapeHtml(gpa)}, ${escapeHtml(targetDegree)})</span>
      </div>
      <div style="font-size:12px; line-height:1.6; color:var(--text-primary); white-space:pre-wrap;">
        ${escapeHtml(res.executive_summary || 'Autonomous multi-agent research complete. Evaluated discovered candidates against institutional requirements.')}
      </div>
    `;
  }

  // 2. Top Recommendations List
  if (recsListEl) {
    recsListEl.innerHTML = '';
    const recs = res.recommendations || [];

    if (recs.length === 0) {
      recsListEl.innerHTML = `
        <div class="card-glass text-center py-4" style="color:var(--text-muted);">
          <div style="font-size:24px; margin-bottom:8px;">🔍</div>
          <p>No candidates fully met all strict institutional eligibility and deadline requirements for Top 5 recommendation.</p>
          <p class="text-xs">Review the "All Candidates" tab to inspect verified, partial, and rejected opportunities.</p>
        </div>
      `;
    } else {
      for (const rec of recs) {
        const card = renderRecommendationCard(rec);
        recsListEl.appendChild(card);
      }
    }
  }

  // 3. Render child components
  renderCandidates();
  renderSop();
  renderAuditPanels();
  renderHealthMetrics();
}
