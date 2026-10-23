/**
 * Inputs Panel Component
 * Features:
 * - Drag-and-drop zones for Resume (required) and Marksheet (optional)
 * - Strict client-side PDF type & 10MB size validation
 * - Dynamic country chip selector with emoji flags
 * - Clean "New Student" reset for live-demo adaptation
 * - Full multipart/form-data assembly and swarm launch
 */

import { state } from '../state.js';
import { launchSwarmRun, streamRunEvents, fetchRunDetails } from '../api.js';
import { getCountryFlag } from './recommendation.js';
import { showToast } from './toast.js';

const MAX_PDF_BYTES = 10 * 1024 * 1024; // 10 MB

// DOM Elements
const form = document.getElementById('form-run');
const btnRun = document.getElementById('btn-run-swarm');
const btnReset = document.getElementById('btn-reset-form');
const errorMsgEl = document.getElementById('form-error-msg');

// Resume Elements
const dropResume = document.getElementById('drop-resume');
const inputResume = document.getElementById('file-resume');
const idleResume = document.getElementById('drop-resume-idle');
const selectedResume = document.getElementById('drop-resume-selected');
const nameResume = document.getElementById('resume-filename');
const sizeResume = document.getElementById('resume-filesize');
const btnRemoveResume = document.getElementById('btn-remove-resume');

// Marksheet Elements
const dropMarksheet = document.getElementById('drop-marksheet');
const inputMarksheet = document.getElementById('file-marksheet');
const idleMarksheet = document.getElementById('drop-marksheet-idle');
const selectedMarksheet = document.getElementById('drop-marksheet-selected');
const nameMarksheet = document.getElementById('marksheet-filename');
const sizeMarksheet = document.getElementById('marksheet-filesize');
const btnRemoveMarksheet = document.getElementById('btn-remove-marksheet');

// Chips Elements
const chipsList = document.getElementById('chips-list');
const inputCountry = document.getElementById('input-country');

// Internal files & chips state
let resumeFile = null;
let marksheetFile = null;
const targetCountries = [];

export function initInputs() {
  setupDropzone(dropResume, inputResume, handleResumeSelected);
  setupDropzone(dropMarksheet, inputMarksheet, handleMarksheetSelected);

  if (btnRemoveResume) {
    btnRemoveResume.addEventListener('click', (e) => {
      e.stopPropagation();
      clearResume();
    });
  }

  if (btnRemoveMarksheet) {
    btnRemoveMarksheet.addEventListener('click', (e) => {
      e.stopPropagation();
      clearMarksheet();
    });
  }

  setupCountryChips();

  if (btnReset) {
    btnReset.addEventListener('click', () => {
      resetStudentForm();
      showToast('Form cleared. Ready for next student profile.', 'info');
    });
  }

  if (form) {
    form.addEventListener('submit', handleFormSubmit);
  }

  // Sample defaults helper: if targetCountries is empty, add Rahul's target countries
  if (targetCountries.length === 0) {
    addCountryChip('Singapore');
    addCountryChip('Canada');
    addCountryChip('Germany');
  }
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function setupDropzone(dropEl, inputEl, onFileSelected) {
  if (!dropEl || !inputEl) return;

  dropEl.addEventListener('click', () => inputEl.click());

  inputEl.addEventListener('change', () => {
    if (inputEl.files && inputEl.files[0]) {
      onFileSelected(inputEl.files[0]);
    }
  });

  ['dragenter', 'dragover'].forEach(evt => {
    dropEl.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropEl.classList.add('drag-over');
    });
  });

  ['dragleave', 'drop'].forEach(evt => {
    dropEl.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropEl.classList.remove('drag-over');
    });
  });

  dropEl.addEventListener('drop', (e) => {
    const files = e.dataTransfer.files;
    if (files && files[0]) {
      onFileSelected(files[0]);
    }
  });
}

function handleResumeSelected(file) {
  hideError();
  if (!validatePdf(file, 'Resume')) return;

  resumeFile = file;
  if (nameResume) nameResume.textContent = file.name;
  if (sizeResume) sizeResume.textContent = formatBytes(file.size);
  if (idleResume) idleResume.classList.add('hidden');
  if (selectedResume) selectedResume.classList.remove('hidden');

  updateRunButton();
}

function handleMarksheetSelected(file) {
  hideError();
  if (!validatePdf(file, 'Marksheet')) return;

  marksheetFile = file;
  if (nameMarksheet) nameMarksheet.textContent = file.name;
  if (sizeMarksheet) sizeMarksheet.textContent = formatBytes(file.size);
  if (idleMarksheet) idleMarksheet.classList.add('hidden');
  if (selectedMarksheet) selectedMarksheet.classList.remove('hidden');
}

function validatePdf(file, label) {
  if (!file.name.toLowerCase().endsWith('.pdf') && file.type !== 'application/pdf') {
    showError(`${label} must be a valid PDF document.`);
    return false;
  }
  if (file.size > MAX_PDF_BYTES) {
    showError(`${label} exceeds the 10 MB maximum size limit.`);
    return false;
  }
  return true;
}

function clearResume() {
  resumeFile = null;
  if (inputResume) inputResume.value = '';
  if (idleResume) idleResume.classList.remove('hidden');
  if (selectedResume) selectedResume.classList.add('hidden');
  updateRunButton();
}

function clearMarksheet() {
  marksheetFile = null;
  if (inputMarksheet) inputMarksheet.value = '';
  if (idleMarksheet) idleMarksheet.classList.remove('hidden');
  if (selectedMarksheet) selectedMarksheet.classList.add('hidden');
}

function updateRunButton() {
  if (btnRun) {
    btnRun.disabled = !resumeFile || state.status === 'running';
  }
}

// ---------------------------------------------------------------------------
// Country Chips
// ---------------------------------------------------------------------------
function setupCountryChips() {
  if (!inputCountry) return;

  inputCountry.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      const val = inputCountry.value.trim().replace(/^,+|,+$/g, '');
      if (val) {
        addCountryChip(val);
        inputCountry.value = '';
      }
    } else if (e.key === 'Backspace' && !inputCountry.value && targetCountries.length > 0) {
      removeCountryChip(targetCountries[targetCountries.length - 1]);
    }
  });
}

function addCountryChip(country) {
  const trimmed = country.trim();
  if (!trimmed || targetCountries.includes(trimmed)) return;

  targetCountries.push(trimmed);
  renderCountryChips();
}

function removeCountryChip(country) {
  const idx = targetCountries.indexOf(country);
  if (idx !== -1) {
    targetCountries.splice(idx, 1);
    renderCountryChips();
  }
}

function renderCountryChips() {
  if (!chipsList) return;
  chipsList.innerHTML = '';

  targetCountries.forEach(country => {
    const flag = getCountryFlag(country);
    const chip = document.createElement('div');
    chip.className = 'chip';

    const textSpan = document.createElement('span');
    textSpan.textContent = `${flag} ${country}`;

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'chip-remove';
    removeBtn.textContent = '✕';
    removeBtn.onclick = () => removeCountryChip(country);

    chip.appendChild(textSpan);
    chip.appendChild(removeBtn);
    chipsList.appendChild(chip);
  });
}

// ---------------------------------------------------------------------------
// Reset
// ---------------------------------------------------------------------------
export function resetStudentForm() {
  clearResume();
  clearMarksheet();

  targetCountries.length = 0;
  renderCountryChips();

  if (form) form.reset();
  hideError();
  state.resetAll();
  updateRunButton();
}

function showError(msg) {
  if (errorMsgEl) {
    errorMsgEl.textContent = msg;
    errorMsgEl.classList.remove('hidden');
  }
  showToast(msg, 'error');
}

function hideError() {
  if (errorMsgEl) {
    errorMsgEl.textContent = '';
    errorMsgEl.classList.add('hidden');
  }
}

// ---------------------------------------------------------------------------
// Form Submit -> Swarm Launch
// ---------------------------------------------------------------------------
async function handleFormSubmit(e) {
  e.preventDefault();
  hideError();

  if (!resumeFile) {
    showError('Please upload a Resume PDF before launching the swarm.');
    return;
  }

  const formData = new FormData();
  formData.append('resume', resumeFile);

  if (marksheetFile) {
    formData.append('marksheet', marksheetFile);
  }

  // Country Chips
  if (targetCountries.length > 0) {
    formData.append('target_countries', targetCountries.join(', '));
  }

  // Form Fields
  const degreeLevel = document.getElementById('select-degree-level')?.value;
  if (degreeLevel) formData.append('degree_level', degreeLevel);

  const nationality = document.getElementById('input-nationality')?.value.trim();
  if (nationality) formData.append('nationality', nationality);

  const githubUrl = document.getElementById('input-github')?.value.trim();
  if (githubUrl) formData.append('github_url', githubUrl);

  const linkedinUrl = document.getElementById('input-linkedin')?.value.trim();
  if (linkedinUrl) formData.append('linkedin_url', linkedinUrl);

  const budgetNote = document.getElementById('input-budget')?.value.trim();
  if (budgetNote) formData.append('budget_note', budgetNote);

  try {
    setLoadingState(true);
    showToast('Launching agent swarm…', 'info');

    const res = await launchSwarmRun(formData);
    const runId = res.run_id;

    state.resetForNewRun(runId);
    showToast(`Swarm running (${runId.slice(0, 12)})`, 'success');

    // Subscribe to SSE stream
    streamRunEvents(runId, {
      onEvent: (event) => {
        state.addEvent(event);
      },
      onComplete: async () => {
        setLoadingState(false);
        showToast('Swarm completed all stages!', 'success');
        try {
          const finalDetails = await fetchRunDetails(runId);
          if (finalDetails.result) {
            state.setResult(finalDetails.result);
          }
        } catch (fetchErr) {
          console.error('Failed fetching final run details:', fetchErr);
        }
      },
      onError: (err) => {
        setLoadingState(false);
        state.setError(err.message);
        showToast(`Stream warning: ${err.message}`, 'warn');
      },
    });

  } catch (err) {
    setLoadingState(false);
    showError(err.message || 'Failed to start swarm.');
  }
}

function setLoadingState(isLoading) {
  if (!btnRun) return;

  btnRun.disabled = isLoading;
  const spinner = btnRun.querySelector('.btn-spinner');
  const textSpan = btnRun.querySelector('.btn-text-content');

  if (isLoading) {
    if (spinner) spinner.classList.remove('hidden');
    if (textSpan) textSpan.innerHTML = '<span>Agents Working…</span>';
  } else {
    if (spinner) spinner.classList.add('hidden');
    if (textSpan) textSpan.innerHTML = '<span class="btn-icon-pulse">⚡</span><span>Launch Swarm</span>';
    updateRunButton();
  }
}
