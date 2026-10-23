/**
 * Live Agent Lanes & Handoff Visualizer
 * Manages the 7 agent lane cards, handoff connectors, and Writer-Critic revision badge.
 */

import { state } from '../state.js';

const laneMap = {
  'Orchestrator': { elementId: 'lane-orchestrator', flowId: 'flow-1' },
  'Profile Analyst': { elementId: 'lane-profile_analyst', flowId: 'flow-2' },
  'Scout': { elementId: 'lane-scout', flowId: 'flow-3' },
  'Verifier': { elementId: 'lane-verifier', flowId: 'flow-4' },
  'Ranker': { elementId: 'lane-ranker', flowId: 'flow-5' },
  'Writer': { elementId: 'lane-writer', flowId: 'flow-loop' },
  'Critic': { elementId: 'lane-critic', flowId: null },
};

const handoffLabel = document.getElementById('active-handoff-label');
const revisionBadge = document.getElementById('badge-revision-loop');

export function initLanes() {
  state.subscribe((eventType) => {
    if (eventType === 'EVENT_ADDED' || eventType === 'RUN_START' || eventType === 'RESULT_READY' || eventType === 'RESET_ALL') {
      renderLanes();
    }
  });
  renderLanes();
}

export function renderLanes() {
  if (handoffLabel) {
    handoffLabel.textContent = state.activeHandoff;
  }

  // Update revision loop badge
  if (revisionBadge) {
    if (state.revisionCount > 0) {
      revisionBadge.classList.remove('hidden');
      revisionBadge.textContent = `Revision: Round ${state.revisionCount}`;
    } else {
      revisionBadge.classList.add('hidden');
    }
  }

  // Update each agent lane card
  for (const [agentName, laneData] of Object.entries(state.agentLanes)) {
    const config = laneMap[agentName];
    if (!config) continue;

    const laneCard = document.getElementById(config.elementId);
    if (!laneCard) continue;

    // Badge status
    const statusBadge = laneCard.querySelector('.lane-status-badge');
    if (statusBadge) {
      statusBadge.className = `lane-status-badge ${laneData.status}`;
      statusBadge.textContent = laneData.status;
    }

    // Message
    const msgEl = laneCard.querySelector('.lane-msg');
    if (msgEl) {
      msgEl.textContent = laneData.message || 'Standby…';
      msgEl.title = laneData.message || '';
    }

    // Duration time
    const timeEl = laneCard.querySelector('.lane-time');
    if (timeEl) {
      if (laneData.duration_s !== null && laneData.duration_s !== undefined) {
        timeEl.textContent = `${Number(laneData.duration_s).toFixed(1)}s`;
      } else if (laneData.status === 'working') {
        timeEl.textContent = 'active…';
      } else if (laneData.status === 'idle') {
        timeEl.textContent = '--';
      }
    }

    // Lane border accent when working
    if (laneData.status === 'working') {
      laneCard.classList.add('working');
    } else {
      laneCard.classList.remove('working');
    }

    // Update connector
    if (config.flowId) {
      const flowEl = document.getElementById(config.flowId);
      if (flowEl) {
        if (laneData.status === 'done' || laneData.status === 'working') {
          flowEl.classList.add('active');
        } else {
          flowEl.classList.remove('active');
        }
      }
    }
  }
}
