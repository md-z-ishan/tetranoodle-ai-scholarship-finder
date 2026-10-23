/**
 * Candidates Table Component
 * Features:
 * - Real filtering by Status, Country, and Keyword
 * - Column sorting (Name, Tier, Status, Confidence, Deadline)
 * - Explicit real rejection reason display
 * - Dynamic country filter population
 */

import { state } from '../state.js';
import { getCountryFlag } from './recommendation.js';

const tbody = document.getElementById('candidates-tbody');
const filterStatusEl = document.getElementById('filter-status');
const filterCountryEl = document.getElementById('filter-country');
const searchEl = document.getElementById('filter-candidate-search');
const tableHeader = document.querySelector('#candidates-table thead');

export function initCandidatesTable() {
  if (filterStatusEl) {
    filterStatusEl.addEventListener('change', (e) => {
      state.candidateFilter.status = e.target.value;
      renderCandidates();
    });
  }

  if (filterCountryEl) {
    filterCountryEl.addEventListener('change', (e) => {
      state.candidateFilter.country = e.target.value;
      renderCandidates();
    });
  }

  if (searchEl) {
    searchEl.addEventListener('input', (e) => {
      state.candidateFilter.search = e.target.value.toLowerCase().trim();
      renderCandidates();
    });
  }

  if (tableHeader) {
    tableHeader.addEventListener('click', (e) => {
      const th = e.target.closest('th[data-sort]');
      if (!th) return;
      const field = th.dataset.sort;
      if (state.candidateSort.field === field) {
        state.candidateSort.asc = !state.candidateSort.asc;
      } else {
        state.candidateSort.field = field;
        state.candidateSort.asc = true;
      }
      renderCandidates();
    });
  }

  state.subscribe((eventType) => {
    if (eventType === 'RESULT_READY' || eventType === 'RUN_START' || eventType === 'RESET_ALL') {
      populateCountryFilter();
      renderCandidates();
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

function populateCountryFilter() {
  if (!filterCountryEl) return;
  const items = getAllCandidates();
  const countries = new Set();

  for (const c of items) {
    if (c.country && c.country.trim()) {
      countries.add(c.country.trim());
    }
  }

  const currentVal = filterCountryEl.value;
  filterCountryEl.innerHTML = '<option value="all">All Countries</option>';

  const sorted = Array.from(countries).sort();
  for (const country of sorted) {
    const opt = document.createElement('option');
    opt.value = country;
    opt.textContent = `${getCountryFlag(country)} ${country}`;
    filterCountryEl.appendChild(opt);
  }

  if (countries.has(currentVal)) {
    filterCountryEl.value = currentVal;
  }
}

function getAllCandidates() {
  const res = state.result;
  if (!res) return [];

  // Combine verified list with raw candidates if any candidates were left unverified
  const verified = res.verified || [];
  const verifiedUrls = new Set(verified.map(v => v.url || v.name));

  const all = [...verified];
  if (res.candidates) {
    for (const cand of res.candidates) {
      if (!verifiedUrls.has(cand.url || cand.name)) {
        all.push({
          ...cand,
          status: 'unverified',
          source_tier: 'unknown',
          confidence: 0.2,
          rejection_reason: 'Scout discovered; pending full page verification.',
        });
      }
    }
  }

  return all;
}

export function renderCandidates() {
  if (!tbody) return;
  const items = getAllCandidates();

  if (items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">No candidate records available yet.</td></tr>';
    return;
  }

  // Filter
  const statusFilter = state.candidateFilter.status;
  const countryFilter = state.candidateFilter.country;
  const searchFilter = state.candidateFilter.search;

  let filtered = items.filter(item => {
    const itemStatus = (item.status || 'unverified').toLowerCase();
    if (statusFilter !== 'all' && itemStatus !== statusFilter) return false;

    if (countryFilter !== 'all' && (item.country || '').trim() !== countryFilter) return false;

    if (searchFilter) {
      const haystack = `${item.name || ''} ${item.organization || ''} ${item.country || ''}`.toLowerCase();
      if (!haystack.includes(searchFilter)) return false;
    }

    return true;
  });

  // Sort
  const sortField = state.candidateSort.field;
  const isAsc = state.candidateSort.asc;
  filtered.sort((a, b) => {
    let valA = a[sortField] || '';
    let valB = b[sortField] || '';

    if (sortField === 'confidence') {
      valA = a.confidence || 0;
      valB = b.confidence || 0;
      return isAsc ? valA - valB : valB - valA;
    }

    valA = String(valA).toLowerCase();
    valB = String(valB).toLowerCase();
    if (valA < valB) return isAsc ? -1 : 1;
    if (valA > valB) return isAsc ? 1 : -1;
    return 0;
  });

  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">No candidates match your current filter selection.</td></tr>';
    return;
  }

  tbody.innerHTML = filtered.map(c => {
    const status = (c.status || 'unverified').toLowerCase();
    const tier = (c.source_tier || 'unknown').toLowerCase();
    const conf = Math.round((c.confidence || 0) * 100);
    const deadline = c.deadline || 'Not found';
    const funding = c.funding_summary || c.funding_type || 'Unknown';
    const country = c.country || 'Global';
    const flag = getCountryFlag(country);

    let reasonText = '';
    if (status === 'rejected') {
      reasonText = `<span style="color:var(--status-error); font-weight:600;">🚫 ${escapeHtml(c.rejection_reason || 'Ineligible by criteria')}</span>`;
    } else if (c.evidence_snippet) {
      reasonText = `<span style="font-style:italic;">"${escapeHtml(c.evidence_snippet.slice(0, 120))}…"</span>`;
    } else {
      reasonText = `<span class="text-muted">${escapeHtml(c.source_snippet || 'No verbatim evidence snippet recorded.')}</span>`;
    }

    return `
      <tr>
        <td>
          <div style="font-weight:700; color:var(--text-primary); margin-bottom:2px;">
            ${c.url ? `
              <a href="${escapeHtml(c.url)}" target="_blank" rel="noopener noreferrer" style="color:var(--text-primary); text-decoration:none;">
                ${escapeHtml(c.name || 'Unnamed')}
              </a>
            ` : escapeHtml(c.name || 'Unnamed')}
          </div>
          <div class="text-xs text-muted">${flag} ${escapeHtml(c.organization || 'Official Institution')}</div>
        </td>
        <td><span class="tier-badge ${escapeHtml(tier)}">${escapeHtml(tier)}</span></td>
        <td><span class="status-tag ${escapeHtml(status)}">${escapeHtml(status)}</span></td>
        <td><span style="font-family:var(--font-mono); font-weight:700;">${conf}%</span></td>
        <td><span class="text-xs">${escapeHtml(deadline)}</span></td>
        <td><span class="text-xs">${escapeHtml(funding)}</span></td>
        <td><div class="text-xs" style="max-width:320px;">${reasonText}</div></td>
      </tr>
    `;
  }).join('');
}
