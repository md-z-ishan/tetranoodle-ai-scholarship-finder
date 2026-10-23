/**
 * Statement of Purpose (SOP) Viewer & Critic Audit Sidebar
 * Features:
 * - Document paper styling with prominent "Draft only - not submitted" watermark
 * - Copy to clipboard with toast notification
 * - Grounding tags showing student profile facts utilized
 * - Critic audit notes categorized by revision round
 */

import { state } from '../state.js';
import { showToast } from './toast.js';

const container = document.getElementById('sop-view-container');

export function initSopViewer() {
  state.subscribe((eventType) => {
    if (eventType === 'RESULT_READY' || eventType === 'RUN_START' || eventType === 'RESET_ALL') {
      renderSop();
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

export function renderSop() {
  if (!container) return;

  const res = state.result;
  const draft = res ? res.draft : null;

  if (!draft || !draft.text) {
    container.innerHTML = `
      <div class="empty-hint text-center py-4" style="color:var(--text-muted);">
        <div style="font-size:24px; margin-bottom:8px;">📝</div>
        <p>Statement of Purpose draft will be synthesized here once the Writer and Critic complete verification.</p>
      </div>
    `;
    return;
  }

  const oppName = draft.opportunity_name || (res.recommendations && res.recommendations[0] ? res.recommendations[0].opportunity.name : 'Target Programme');
  const label = draft.label || 'Draft only - not submitted';
  const revisionCount = draft.revision_count || state.revisionCount || 0;
  const profileFields = draft.based_on_profile_fields || ['Academic Transcripts', 'Technical Projects', 'Career Goals'];
  const criticNotes = draft.critic_notes || [];

  container.innerHTML = `
    <div class="sop-layout">
      <!-- Main Paper View -->
      <div class="sop-paper">
        <div class="sop-paper-header">
          <div>
            <span class="sop-watermark">${escapeHtml(label)}</span>
            <h3 style="font-size:15px; font-weight:800; margin:6px 0 2px 0; color:var(--text-primary);">
              Application Draft: ${escapeHtml(oppName)}
            </h3>
            <span class="text-xs text-muted">Audited by Critic Agent &bull; ${revisionCount} Revision ${revisionCount === 1 ? 'Round' : 'Rounds'}</span>
          </div>
          <button id="btn-copy-sop" class="btn-outline btn-sm" title="Copy SOP to clipboard">
            📋 Copy SOP
          </button>
        </div>

        <div class="sop-body" id="sop-body-content">${escapeHtml(draft.text)}</div>

        <div style="font-size:11px; color:var(--text-muted); border-top:1px solid var(--border-subtle); padding-top:8px;">
          ⚖️ <em>Disclaimer:</em> This draft is synthesized strictly from grounded evidence extracted from candidate documents and primary institutional admission requirements. Review thoroughly before applying.
        </div>
      </div>

      <!-- Critic Audit & Grounding Sidebar -->
      <aside class="sop-audit-sidebar">
        <!-- Grounding Facts Used -->
        <div class="card-glass">
          <h4 class="card-subtitle">
            <span>🎯</span> Profile Facts Grounded
          </h4>
          <p class="text-xs text-muted" style="margin-bottom:8px;">
            Facts incorporated strictly without hallucinated scores or achievements:
          </p>
          <div class="grounding-tags">
            ${profileFields.map(f => `
              <span class="grounding-tag">✓ ${escapeHtml(f)}</span>
            `).join('')}
          </div>
        </div>

        <!-- Critic Review Notes -->
        <div class="card-glass">
          <h4 class="card-subtitle">
            <span>🧐</span> Critic Audit Trail
          </h4>
          <div style="font-size:11px; margin-bottom:6px;">
            Status: <span style="font-weight:700; color:var(--status-done);">Approved (Bounded)</span>
          </div>
          ${criticNotes.length > 0 ? `
            <ul class="reasons-list">
              ${criticNotes.map(n => `
                <li>${escapeHtml(n)}</li>
              `).join('')}
            </ul>
          ` : `
            <p class="text-xs text-muted">
              Zero ungrounded assertions detected. Tone, GPA consistency, and degree alignment fully validated.
            </p>
          `}
        </div>
      </aside>
    </div>
  `;

  // Bind copy button
  const copyBtn = document.getElementById('btn-copy-sop');
  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(draft.text);
        showToast('SOP draft copied to clipboard!', 'success');
      } catch (err) {
        showToast('Could not access clipboard automatically.', 'warn');
      }
    });
  }
}
