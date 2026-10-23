/**
 * Main Application Orchestrator for Scholarship Swarm
 * TetraNoodle AI Hackathon 2026
 *
 * Coordinates themes, system readiness probes, timers, and component lifecycles.
 */

import { state } from './state.js';
import { fetchSystemStatus } from './api.js';
import { initInputs } from './ui/inputs.js';
import { initLanes } from './ui/lanes.js';
import { initTimeline } from './ui/timeline.js';
import { initResultsPanel } from './ui/results.js';
import { initCandidatesTable } from './ui/candidates.js';
import { initSopViewer } from './ui/sop.js';
import { initPanels } from './ui/panels.js';
import { initHistoryDrawer } from './ui/history.js';
import { showToast } from './ui/toast.js';

// DOM Elements
const htmlEl = document.documentElement;
const themeToggleBtn = document.getElementById('theme-toggle');
const iconSun = document.getElementById('icon-sun');
const iconMoon = document.getElementById('icon-moon');

const statusPill = document.getElementById('swarm-status-pill');
const statusLabel = document.getElementById('swarm-status-label');
const statusTimer = document.getElementById('swarm-timer');
const progressBar = document.getElementById('global-progress-bar');

const systemBanner = document.getElementById('system-banner');
const systemBannerText = document.getElementById('system-banner-text');
const bannerDismissBtn = document.getElementById('banner-dismiss');

let timerInterval = null;

// ---------------------------------------------------------------------------
// Bootstrap on DOM Ready
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {
  initTheme();
  initBanner();

  // Initialize UI subsystems
  initInputs();
  initLanes();
  initTimeline();
  initResultsPanel();
  initCandidatesTable();
  initSopViewer();
  initPanels();
  initHistoryDrawer();

  // Bind State updates to global header and progress
  state.subscribe((eventType, data) => {
    handleStateChange(eventType, data);
  });

  // Check system readiness probe
  await checkReadiness();
});

// ---------------------------------------------------------------------------
// Theme Management
// ---------------------------------------------------------------------------
function initTheme() {
  const savedTheme = localStorage.getItem('swarm_theme') || 'dark';
  applyTheme(savedTheme);

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener('click', () => {
      const current = htmlEl.getAttribute('data-theme') || 'dark';
      const next = current === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      localStorage.setItem('swarm_theme', next);
    });
  }
}

function applyTheme(theme) {
  htmlEl.setAttribute('data-theme', theme);
  if (theme === 'dark') {
    if (iconSun) iconSun.classList.add('hidden');
    if (iconMoon) iconMoon.classList.remove('hidden');
  } else {
    if (iconSun) iconSun.classList.remove('hidden');
    if (iconMoon) iconMoon.classList.add('hidden');
  }
}

// ---------------------------------------------------------------------------
// System Readiness & Banner
// ---------------------------------------------------------------------------
function initBanner() {
  if (bannerDismissBtn && systemBanner) {
    bannerDismissBtn.addEventListener('click', () => {
      systemBanner.classList.add('hidden');
    });
  }
}

async function checkReadiness() {
  try {
    const data = await fetchSystemStatus();
    state.updateSystemStatus(data);

    updateKeyIndicators(data);

    const missing = [];
    if (!data.gemini_configured) missing.push('GEMINI_API_KEY');
    if (!data.tavily_configured) missing.push('TAVILY_API_KEY');

    if (missing.length > 0 && systemBanner && systemBannerText) {
      systemBannerText.textContent = `Missing API keys in backend/.env: ${missing.join(', ')}. Live agent search and LLM extraction will be degraded.`;
      systemBanner.classList.remove('hidden');
    }
  } catch (err) {
    console.warn('System status check failed:', err);
    if (systemBanner && systemBannerText) {
      systemBannerText.textContent = 'Backend server unreachable. Make sure the FastAPI backend is running on port 8000.';
      systemBanner.classList.remove('hidden');
    }
  }
}

function updateKeyIndicators(data) {
  const geminiEl = document.querySelector('.gemini-status');
  const tavilyEl = document.querySelector('.tavily-status');
  const githubEl = document.querySelector('.github-status');

  if (geminiEl) {
    geminiEl.classList.toggle('ready', Boolean(data.gemini_configured));
    geminiEl.title = data.gemini_configured ? `Gemini LLM (${data.gemini_model || '2.5-flash'}) Ready` : 'Gemini Key Missing';
  }
  if (tavilyEl) {
    tavilyEl.classList.toggle('ready', Boolean(data.tavily_configured));
    tavilyEl.title = data.tavily_configured ? 'Tavily Web Search Ready' : 'Tavily Key Missing';
  }
  if (githubEl) {
    githubEl.classList.toggle('ready', Boolean(data.github_configured));
    githubEl.title = data.github_configured ? 'GitHub API Authenticated' : 'GitHub Token Optional';
  }
}

// ---------------------------------------------------------------------------
// Swarm State & Timer Synchronization
// ---------------------------------------------------------------------------
function handleStateChange(eventType) {
  if (eventType === 'RUN_START') {
    startTimer();
    updateHeaderStatus('working', 'Agents Working…');
    updateProgress(10);
  } else if (eventType === 'RESULT_READY') {
    stopTimer();
    updateHeaderStatus('done', 'Swarm Complete');
    updateProgress(100);
  } else if (eventType === 'RUN_ERROR') {
    stopTimer();
    updateHeaderStatus('error', 'Run Stopped');
    updateProgress(0);
  } else if (eventType === 'RESET_ALL') {
    stopTimer();
    updateHeaderStatus('idle', 'Swarm Ready');
    updateProgress(0);
  } else if (eventType === 'PROGRESS_UPDATED') {
    updateProgress(state.progressPercent);
  }
}

function updateHeaderStatus(statusClass, labelText) {
  if (statusPill) {
    statusPill.className = `status-pill ${statusClass}`;
  }
  if (statusLabel) {
    statusLabel.textContent = labelText;
  }
}

function updateProgress(percent) {
  if (progressBar) {
    progressBar.style.width = `${Math.min(100, Math.max(0, percent))}%`;
  }
}

function startTimer() {
  if (timerInterval) clearInterval(timerInterval);
  if (statusTimer) {
    statusTimer.textContent = '00:00';
    statusTimer.classList.remove('hidden');
  }

  const start = Date.now();
  timerInterval = setInterval(() => {
    const elapsedSeconds = Math.floor((Date.now() - start) / 1000);
    state.runElapsedSeconds = elapsedSeconds;
    const mins = String(Math.floor(elapsedSeconds / 60)).padStart(2, '0');
    const secs = String(elapsedSeconds % 60).padStart(2, '0');
    if (statusTimer) {
      statusTimer.textContent = `${mins}:${secs}`;
    }
  }, 1000);
}

function stopTimer() {
  if (timerInterval) {
    clearInterval(timerInterval);
    timerInterval = null;
  }
}
