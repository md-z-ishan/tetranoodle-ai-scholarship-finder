/**
 * Live Event Timeline Feed
 * Features:
 * - Color-coded event levels
 * - Auto-scroll with pause-on-hover
 * - Filtering by agent
 * - Zero raw innerHTML injection (DOM safety)
 */

import { state } from '../state.js';

const feedEl = document.getElementById('timeline-feed');
const countEl = document.getElementById('event-count');
const filterEl = document.getElementById('select-agent-filter');
const clearBtn = document.getElementById('btn-clear-timeline');

let isHovered = false;

export function initTimeline() {
  if (feedEl) {
    feedEl.addEventListener('mouseenter', () => { isHovered = true; });
    feedEl.addEventListener('mouseleave', () => { isHovered = false; });
  }

  if (filterEl) {
    filterEl.addEventListener('change', (e) => {
      state.agentFilter = e.target.value;
      renderTimeline();
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      state.events = [];
      state.seenEventKeys.clear();
      renderTimeline();
    });
  }

  state.subscribe((eventType, data) => {
    if (eventType === 'EVENT_ADDED') {
      appendTimelineEvent(data);
    } else if (eventType === 'RUN_START' || eventType === 'RESET_ALL') {
      renderTimeline();
    }
  });

  renderTimeline();
}

function formatTime(timestamp) {
  if (!timestamp) return '--:--:--';
  try {
    const d = new Date(timestamp);
    if (isNaN(d.getTime())) return '--:--:--';
    return d.toTimeString().split(' ')[0];
  } catch (_) {
    return '--:--:--';
  }
}

function getEventLevelClass(event) {
  const t = (event.event_type || '').toLowerCase();
  if (t === 'error') return 'error';
  if (t === 'warning' || t === 'warn') return 'warn';
  if (t === 'finish' || t === 'done' || t === 'success') return 'success';
  return 'info';
}

function createTimelineItem(event) {
  const row = document.createElement('div');
  const levelClass = getEventLevelClass(event);
  row.className = `timeline-item ${levelClass}`;
  row.dataset.agent = event.agent || '';

  const timeSpan = document.createElement('span');
  timeSpan.className = 'timeline-time';
  timeSpan.textContent = formatTime(event.timestamp);

  const agentSpan = document.createElement('span');
  agentSpan.className = 'timeline-agent';
  agentSpan.textContent = event.agent || 'Swarm';

  const msgSpan = document.createElement('span');
  msgSpan.className = 'timeline-msg';
  msgSpan.textContent = event.message || '';

  row.appendChild(timeSpan);
  row.appendChild(agentSpan);
  row.appendChild(msgSpan);

  return row;
}

export function appendTimelineEvent(event) {
  if (!feedEl) return;

  // Update counter
  if (countEl) {
    countEl.textContent = `(${state.events.length} events)`;
  }

  // Remove empty hint if present
  const hint = feedEl.querySelector('.timeline-empty-hint');
  if (hint) {
    hint.remove();
  }

  // Filter check
  const filter = state.agentFilter;
  if (filter !== 'all' && event.agent !== filter) {
    return; // Don't append if filtered out
  }

  const row = createTimelineItem(event);
  feedEl.appendChild(row);

  // Auto-scroll if user is not hovering
  if (!isHovered) {
    feedEl.scrollTop = feedEl.scrollHeight;
  }
}

export function renderTimeline() {
  if (!feedEl) return;
  feedEl.innerHTML = '';

  const filter = state.agentFilter;
  const filtered = filter === 'all' 
    ? state.events 
    : state.events.filter(e => e.agent === filter);

  if (countEl) {
    countEl.textContent = `(${state.events.length} events)`;
  }

  if (filtered.length === 0) {
    const emptyHint = document.createElement('div');
    emptyHint.className = 'timeline-empty-hint';
    emptyHint.textContent = state.events.length === 0 
      ? 'Events stream in real-time as the swarm progresses…' 
      : 'No events match the selected agent filter.';
    feedEl.appendChild(emptyHint);
    return;
  }

  for (const event of filtered) {
    const row = createTimelineItem(event);
    feedEl.appendChild(row);
  }

  if (!isHovered) {
    feedEl.scrollTop = feedEl.scrollHeight;
  }
}
