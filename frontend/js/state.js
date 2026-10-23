/**
 * Central State Store for Scholarship Swarm
 * Provides reactive event subscription and single-source-of-truth state.
 */

class SwarmState {
  constructor() {
    this.subscribers = new Set();
    this.resetAll();
  }

  resetAll() {
    this.currentRunId = null;
    this.status = 'idle'; // 'idle' | 'running' | 'completed' | 'error'
    this.runStartTime = null;
    this.runElapsedSeconds = 0;
    this.events = [];
    this.seenEventKeys = new Set();

    this.agentLanes = {
      'Orchestrator': { status: 'idle', message: 'Awaiting profile launch…', duration_s: null },
      'Profile Analyst': { status: 'idle', message: 'Waiting for resume upload…', duration_s: null },
      'Scout': { status: 'idle', message: 'Queries standby…', duration_s: null },
      'Verifier': { status: 'idle', message: 'Tier resolution standby…', duration_s: null },
      'Ranker': { status: 'idle', message: 'Scoring models standby…', duration_s: null },
      'Writer': { status: 'idle', message: 'Awaiting top recommendation…', duration_s: null },
      'Critic': { status: 'idle', message: 'Audit rules armed…', duration_s: null },
    };

    this.activeHandoff = 'Pipeline Ready';
    this.revisionCount = 0;
    this.progressPercent = 0;
    this.result = null;
    this.activeTab = 'tab-recommendations';
    this.agentFilter = 'all';
    this.candidateFilter = { status: 'all', country: 'all', search: '' };
    this.candidateSort = { field: 'name', asc: true };

    this.systemStatus = {
      gemini_configured: false,
      tavily_configured: false,
      github_configured: false,
      gemini_model: 'gemini-2.5-flash',
      gemini_rpm: 4,
    };

    this.notify('RESET_ALL', null);
  }

  resetForNewRun(runId) {
    this.currentRunId = runId;
    this.status = 'running';
    this.runStartTime = Date.now();
    this.runElapsedSeconds = 0;
    this.events = [];
    this.seenEventKeys.clear();

    for (const agent of Object.keys(this.agentLanes)) {
      this.agentLanes[agent] = { status: 'idle', message: 'Queued…', duration_s: null };
    }

    this.activeHandoff = 'Starting Swarm…';
    this.revisionCount = 0;
    this.progressPercent = 5;
    this.result = null;

    this.notify('RUN_START', { runId });
  }

  subscribe(callback) {
    this.subscribers.add(callback);
    return () => this.subscribers.delete(callback);
  }

  notify(eventType, payload) {
    for (const callback of this.subscribers) {
      try {
        callback(eventType, payload, this);
      } catch (err) {
        console.error(`Error in state subscriber for ${eventType}:`, err);
      }
    }
  }

  updateSystemStatus(statusObj) {
    this.systemStatus = { ...this.systemStatus, ...statusObj };
    this.notify('SYSTEM_STATUS_UPDATED', this.systemStatus);
  }

  addEvent(event) {
    // Unique key to prevent duplicates during SSE reconnections or replays
    const key = `${event.timestamp || ''}_${event.agent || ''}_${event.event_type || ''}_${event.message || ''}`;
    if (this.seenEventKeys.has(key)) {
      return false; // duplicate
    }
    this.seenEventKeys.add(key);
    this.events.push(event);

    // Update agent lane state
    const agentName = event.agent;
    if (agentName && this.agentLanes[agentName]) {
      const payload = event.payload || {};
      const status = payload.status || (event.event_type === 'error' ? 'error' : event.event_type === 'warning' ? 'warn' : 'working');
      
      this.agentLanes[agentName] = {
        ...this.agentLanes[agentName],
        status: status === 'finished' || status === 'done' ? 'done' : status,
        message: event.message || this.agentLanes[agentName].message,
        duration_s: payload.duration_s !== undefined ? payload.duration_s : this.agentLanes[agentName].duration_s,
      };

      // Detect revision loops
      if (payload.revision !== undefined && payload.revision > 0) {
        this.revisionCount = payload.revision;
      }
      if (payload.revision_count !== undefined && payload.revision_count > 0) {
        this.revisionCount = payload.revision_count;
      }

      // Estimate progress
      this._updateProgressFromAgent(agentName, status);
    }

    // Update active handoff header
    if (event.message) {
      this.activeHandoff = `${event.agent || 'Swarm'}: ${event.message}`;
    }

    this.notify('EVENT_ADDED', event);
    return true;
  }

  _updateProgressFromAgent(agentName, status) {
    const weights = {
      'Orchestrator': 10,
      'Profile Analyst': 25,
      'Scout': 45,
      'Verifier': 75,
      'Ranker': 85,
      'Writer': 92,
      'Critic': 98,
    };
    if (weights[agentName]) {
      const target = status === 'done' || status === 'finished' ? weights[agentName] : weights[agentName] - 5;
      if (target > this.progressPercent) {
        this.progressPercent = target;
        this.notify('PROGRESS_UPDATED', this.progressPercent);
      }
    }
  }

  setResult(resultData) {
    this.result = resultData;
    this.status = 'completed';
    this.progressPercent = 100;
    this.activeHandoff = 'All Agents Complete';

    // Mark lanes as done if not errored
    for (const [agent, lane] of Object.entries(this.agentLanes)) {
      if (lane.status !== 'error') {
        this.agentLanes[agent].status = 'done';
      }
    }

    this.notify('RESULT_READY', resultData);
  }

  setError(errorMessage) {
    this.status = 'error';
    this.activeHandoff = `Failed: ${errorMessage}`;
    this.notify('RUN_ERROR', errorMessage);
  }
}

export const state = new SwarmState();
