
// static/js/mission_control.js

// Using the socket instance from main.js if available, or assume global 'socket'
// Ensure this script is loaded AFTER main.js

const missionControl = {
    board: document.getElementById('mission-control-board'),
    statusPanel: document.getElementById('mission-control-status'),
    healthPanel: document.getElementById('mission-control-health'),
    reflectionPanel: document.getElementById('mission-control-reflection'),
    cadencePanel: document.getElementById('mission-control-cadence'),
    runSpinePanel: document.getElementById('mission-control-run-spine'),
    actionAuditPanel: document.getElementById('mission-control-action-audit'),
    scoreboardPanel: document.getElementById('mission-control-scoreboard'),
    toolLifecyclePanel: document.getElementById('mission-control-tool-lifecycle'),
    refreshBtn: document.getElementById('refresh-tasks-btn'),
    statusPollIntervalMs: 8000,
    staleAfterMs: 20000,
    statusPollTimer: null,
    staleCheckTimer: null,
    lastStatusAtMs: null,
    snapshotSchemaVersion: null,
    actionAuditFilter: 'all',

    init: function () {
        console.log("Mission Control Initialized");
        this.fetchTasks();
        this.fetchStatusSnapshot();
        this.fetchHealthAudit();
        this.fetchBackgroundCadence();
        this.fetchReflectionSuggestions();
        this.fetchSLOMetrics();
        this.fetchRunSpine();
        this.fetchActionAudit();
        this.fetchExperimentScoreboard();
        this.fetchToolLifecycle();
        this.startStatusPolling();

        if (this.refreshBtn) {
            this.refreshBtn.addEventListener('click', () => {
                this.fetchTasks();
                this.fetchStatusSnapshot();
                this.fetchHealthAudit();
                this.fetchBackgroundCadence();
                this.fetchReflectionSuggestions();
                this.fetchSLOMetrics();
                this.fetchRunSpine();
                this.fetchActionAudit();
                this.fetchExperimentScoreboard();
                this.fetchToolLifecycle();
            });
        }

        // Initialize background kill switches
        this.initBackgroundSwitches();

        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                this.stopStatusPolling();
            } else {
                this.startStatusPolling();
                this.fetchStatusSnapshot();
                this.fetchHealthAudit();
                this.fetchBackgroundCadence();
                this.fetchReflectionSuggestions();
                this.fetchSLOMetrics();
                this.fetchRunSpine();
                this.fetchActionAudit();
                this.fetchExperimentScoreboard();
                this.fetchToolLifecycle();
            }
        });

        // Listen for socket events
        if (typeof socket !== 'undefined') {
            socket.on('task_update', (taskData) => {
                this.handleTaskUpdate(taskData);
                this.fetchStatusSnapshot();
                this.fetchHealthAudit();
                this.fetchBackgroundCadence();
                this.fetchReflectionSuggestions();
                this.fetchSLOMetrics();
                this.fetchRunSpine();
                this.fetchActionAudit();
                this.fetchExperimentScoreboard();
                this.fetchToolLifecycle();
            });
        }
    },


    initBackgroundSwitches: async function() {
        const d_switch = document.getElementById('switch-allow-dreamer');
        const m_switch = document.getElementById('switch-allow-memory');
        const a_switch = document.getElementById('switch-allow-autofix');

        if (!d_switch || !m_switch || !a_switch) return;

        try {
            const res = await fetch('/api/config');
            const config = await res.json();

            d_switch.checked = config.ALLOW_DREAMER !== false;
            m_switch.checked = config.ALLOW_MEMORY_LEARNING !== false;
            a_switch.checked = config.ALLOW_AUTO_FIXING !== false;

        } catch(e) { console.error("Could not init switches", e); }

        const toggleConfig = async (key, val) => {
            try {
                await fetch('/api/config', { 
                    method: 'POST', 
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ [key]: val }) 
                });
            } catch(e) { console.error("Toggle config error", e); }
        };

        d_switch.addEventListener('change', () => toggleConfig('ALLOW_DREAMER', d_switch.checked));
        m_switch.addEventListener('change', () => toggleConfig('ALLOW_MEMORY_LEARNING', m_switch.checked));
        a_switch.addEventListener('change', () => toggleConfig('ALLOW_AUTO_FIXING', a_switch.checked));
    },

    isMissionControlActive: function () {
        const view = document.getElementById('view-mission-control');
        return !!(view && view.classList.contains('active'));
    },

    startStatusPolling: function () {
        this.stopStatusPolling();

        this.statusPollTimer = setInterval(() => {
            if (document.hidden || !this.isMissionControlActive()) return;
            this.fetchStatusSnapshot();
            this.fetchHealthAudit();
            this.fetchBackgroundCadence();
            this.fetchReflectionSuggestions();
            this.fetchSLOMetrics();
            this.fetchRunSpine();
            this.fetchActionAudit();
            this.fetchExperimentScoreboard();
        }, this.statusPollIntervalMs);

        this.staleCheckTimer = setInterval(() => {
            this.updateStaleState();
        }, 1000);
    },

    stopStatusPolling: function () {
        if (this.statusPollTimer) {
            clearInterval(this.statusPollTimer);
            this.statusPollTimer = null;
        }
        if (this.staleCheckTimer) {
            clearInterval(this.staleCheckTimer);
            this.staleCheckTimer = null;
        }
    },

    updateStaleState: function () {
        if (!this.statusPanel) return;

        const now = Date.now();
        const isStale = !this.lastStatusAtMs || (now - this.lastStatusAtMs > this.staleAfterMs);
        this.statusPanel.classList.toggle('stale', isStale);

        const freshness = this.statusPanel.querySelector('.mission-status-freshness');
        if (!freshness) return;

        if (!this.lastStatusAtMs) {
            freshness.textContent = 'Awaiting first snapshot…';
            return;
        }

        const secondsAgo = Math.max(0, Math.floor((now - this.lastStatusAtMs) / 1000));
        freshness.textContent = isStale
            ? `Stale · last updated ${secondsAgo}s ago`
            : `Live · updated ${secondsAgo}s ago`;
    },


    fetchHealthAudit: async function () {
        if (!this.healthPanel) return;

        try {
            const response = await fetch('/api/status/health-audit');
            const data = await response.json();
            if (!data.success) {
                this.healthPanel.innerHTML = `<div class="error">Health audit unavailable: ${this.escapeHtml(data.error || 'unknown error')}</div>`;
                return;
            }

            const health = data.health || {};
            const checks = Array.isArray(health.checks) ? health.checks : [];
            const checkRows = checks.map(item => `
                <div class="mission-audit-row ${item.ok ? 'ok' : 'fail'}">
                    <span>${this.escapeHtml(item.label || item.key || 'check')}</span>
                    <strong>${item.ok ? 'OK' : 'Needs Attention'}</strong>
                </div>
                <div class="mission-audit-detail">${this.escapeHtml(item.details || '')}</div>
            `).join('');

            this.healthPanel.innerHTML = `
                <div class="mission-status-header-row">
                    <div class="mission-status-header">Health Audit</div>
                    <div class="mission-status-freshness ${health.healthy ? 'ok' : 'fail'}">${health.healthy ? 'Healthy' : 'Issues Detected'}</div>
                </div>
                <div class="mission-audit-list">
                    ${checkRows || '<div class="mission-audit-detail">No health checks available.</div>'}
                </div>
            `;
        } catch (e) {
            console.error('Fetch health audit error:', e);
            this.healthPanel.innerHTML = `<div class="error">Health audit link failure: ${this.escapeHtml(e.message)}</div>`;
        }
    },


    fetchBackgroundCadence: async function () {
        if (!this.cadencePanel) return;

        try {
            const response = await fetch('/api/status/background-cadence');
            const data = await response.json();
            if (!data.success) {
                this.cadencePanel.innerHTML = `<div class="error">Cadence unavailable: ${this.escapeHtml(data.error || 'unknown error')}</div>`;
                return;
            }

            const cadence = data.cadence || {};
            const recent = data.recent || {};
            this.cadencePanel.innerHTML = `
                <div class="mission-status-header-row">
                    <div class="mission-status-header">Background Cadence</div>
                    <div class="mission-status-freshness">Runtime Tunable</div>
                </div>
                <div class="mission-kv-grid">
                    <div class="mission-kv-item"><span>Dream Mode</span><strong>${cadence.dream_mode_enabled ? 'On' : 'Off'}</strong></div>
                    <div class="mission-kv-item"><span>Dream Interval</span><strong>${cadence.dream_interval_seconds || 0}s</strong></div>
                    <div class="mission-kv-item"><span>Reminder Poll</span><strong>${cadence.reminder_check_interval_seconds || 0}s</strong></div>
                    <div class="mission-kv-item"><span>Auto Web PiP</span><strong>${cadence.auto_web_pip ? 'On' : 'Off'}</strong></div>
                </div>
                <div class="mission-status-meta">
                    Last Dream: ${this.escapeHtml(this.formatTimestamp(recent.last_dream_timestamp))}
                    · Last Visual Audit: ${this.escapeHtml(this.formatTimestamp(recent.last_visual_audit_timestamp))}
                    · Last Self-Healing: ${this.escapeHtml(this.formatTimestamp(recent.last_self_healing_timestamp))}
                </div>
            `;
        } catch (e) {
            this.cadencePanel.innerHTML = `<div class="error">Cadence link failure: ${this.escapeHtml(e.message)}</div>`;
        }
    },

    fetchSLOMetrics: async function () {
        const sloPanel = document.getElementById('mission-control-slo');
        if (!sloPanel) return;

        try {
            const response = await fetch('/api/telemetry');
            const data = await response.json();
            if (data && data.slo) {
                const slo = data.slo;
                sloPanel.innerHTML = `
                    <div class="mission-section">
                        <h4 class="mission-heading">Production SLO Dashboard</h4>
                        <div class="mission-kv-group">
                            <span class="mission-kv-pill">MTTD (Mean Time To Diagnose): ${slo.mttd_seconds}s</span>
                            <span class="mission-kv-pill">Manual Retries: ${slo.manual_retries}</span>
                            <span class="mission-kv-pill">Avg Delegation Latency: ${slo.avg_delegated_latency_seconds}s</span>
                            <span class="mission-kv-pill">Total Diagnostics Run: ${slo.total_diagnostics_run}</span>
                        </div>
                    </div>
                `;
            }
        } catch (e) {
            console.error("Fetch SLO error:", e);
        }
    },

    fetchActionAudit: async function () {
        if (!this.actionAuditPanel) return;

        try {
            const response = await fetch('/api/system/action-audit?limit=40');
            const data = await response.json();
            if (!data.success) {
                this.actionAuditPanel.innerHTML = `<div class="error">Action audit unavailable: ${this.escapeHtml(data.error || 'unknown error')}</div>`;
                return;
            }

            this.renderActionAudit(data.events || []);
        } catch (e) {
            console.error('Fetch action audit error:', e);
            this.actionAuditPanel.innerHTML = `<div class="error">Action audit link failure: ${this.escapeHtml(e.message)}</div>`;
        }
    },

    fetchRunSpine: async function () {
        if (!this.runSpinePanel) return;

        try {
            const response = await fetch('/api/system/run-spine?limit=10');
            const data = await response.json();
            if (!data.success) {
                this.runSpinePanel.innerHTML = `<div class="error">Run spine unavailable: ${this.escapeHtml(data.error || 'unknown error')}</div>`;
                return;
            }

            this.renderRunSpine(data.snapshot || {});
        } catch (e) {
            console.error('Fetch run spine error:', e);
            this.runSpinePanel.innerHTML = `<div class="error">Run spine link failure: ${this.escapeHtml(e.message)}</div>`;
        }
    },

    renderRunSpine: function (snapshot) {
        if (!this.runSpinePanel) return;

        const counts = snapshot.counts || {};
        const items = Array.isArray(snapshot.work_items) ? snapshot.work_items : [];
        const lessons = Array.isArray(snapshot.patch_lessons) ? snapshot.patch_lessons : [];

        const rows = items.slice(0, 4).map(item => {
            const scorecards = Array.isArray(item.scorecards) ? item.scorecards : [];
            const approvals = Array.isArray(item.approvals) ? item.approvals : [];
            const events = Array.isArray(item.audit_events) ? item.audit_events : [];
            const latestScore = scorecards.length ? (scorecards[0].scorecard || {}) : {};
            const testsRun = Number(latestScore.tests_run || 0);
            const testsPassed = Number(latestScore.tests_passed || 0);
            const verdict = latestScore.blocked ? 'blocked' : latestScore.accepted ? 'accepted' : item.status || 'tracked';
            const route = latestScore.suggested_route ? ` Route: ${latestScore.suggested_route}` : '';
            const failure = latestScore.failure_reason && latestScore.failure_reason.failure_class
                ? ` Failure: ${latestScore.failure_reason.failure_class}`
                : '';
            const detailParts = [
                item.task_type ? `Type: ${item.task_type}` : '',
                item.current_step ? `Step: ${item.current_step}` : '',
                scorecards.length ? `Tests: ${testsPassed}/${testsRun}` : '',
                approvals.length ? `Approvals: ${approvals.length}` : '',
                events.length ? `Audit: ${events.length}` : '',
                route,
                failure,
            ].filter(Boolean);

            return `
                <div class="mission-audit-row ${this.escapeHtml(String(verdict).toLowerCase())}">
                    <span>${this.escapeHtml(this.formatTimestamp(item.updated_at))}</span>
                    <strong>${this.escapeHtml((item.title || item.id || 'work item').toString().substring(0, 42))}</strong>
                    <span>${this.escapeHtml(String(verdict).replace(/_/g, ' '))}</span>
                </div>
                <div class="mission-audit-detail">${this.escapeHtml(detailParts.join(' | ') || 'No linked evidence yet.')}</div>
            `;
        }).join('');

        const lessonRows = lessons.slice(0, 2).map(lesson => `
            <div class="mission-audit-detail">
                Lesson: ${this.escapeHtml(lesson.failure_class || 'unknown')} | ${this.escapeHtml(lesson.rule || lesson.problem || '')}
            </div>
        `).join('');

        this.runSpinePanel.innerHTML = `
            <div class="mission-status-header-row">
                <div class="mission-status-header">Run Spine</div>
                <div class="mission-status-freshness">Unified View</div>
            </div>
            <div class="mission-kv-grid">
                <div class="mission-kv-item"><span>Active</span><strong>${Number(counts.active_tasks || 0)}</strong></div>
                <div class="mission-kv-item"><span>Approvals</span><strong>${Number(counts.pending_approvals || 0)}</strong></div>
                <div class="mission-kv-item"><span>Queued</span><strong>${Number(counts.queued_approvals || 0)}</strong></div>
                <div class="mission-kv-item"><span>Scorecards</span><strong>${Number(counts.scorecards || 0)}</strong></div>
                <div class="mission-kv-item"><span>Blocked</span><strong>${Number(counts.blocked_scorecards || 0)}</strong></div>
                <div class="mission-kv-item"><span>Lessons</span><strong>${Number(counts.patch_lessons || 0)}</strong></div>
            </div>
            <div class="mission-audit-list">
                ${rows || '<div class="mission-audit-detail">No active work. Recent scorecards and lessons will appear here when recorded.</div>'}
                ${lessonRows}
            </div>
        `;
    },

    fetchExperimentScoreboard: async function () {
        if (!this.scoreboardPanel) return;

        try {
            const response = await fetch('/api/system/experiment-scoreboard?limit=20');
            const data = await response.json();
            if (!data.success) {
                this.scoreboardPanel.innerHTML = `<div class="error">Scoreboard unavailable: ${this.escapeHtml(data.error || 'unknown error')}</div>`;
                return;
            }

            this.renderExperimentScoreboard(data.records || []);
        } catch (e) {
            console.error('Fetch experiment scoreboard error:', e);
            this.scoreboardPanel.innerHTML = `<div class="error">Scoreboard link failure: ${this.escapeHtml(e.message)}</div>`;
        }
    },

    fetchToolLifecycle: async function () {
        if (!this.toolLifecyclePanel) return;

        try {
            const response = await fetch('/api/system/tool-lifecycle?limit=20');
            const data = await response.json();
            if (!data.success) {
                this.toolLifecyclePanel.innerHTML = `<div class="error">Tool lifecycle unavailable: ${this.escapeHtml(data.error || 'unknown error')}</div>`;
                return;
            }

            this.renderToolLifecycle(data.records || []);
        } catch (e) {
            console.error('Fetch tool lifecycle error:', e);
            this.toolLifecyclePanel.innerHTML = `<div class="error">Tool lifecycle link failure: ${this.escapeHtml(e.message)}</div>`;
        }
    },

    renderToolLifecycle: function (records) {
        if (!this.toolLifecyclePanel) return;

        const rows = (records || []).slice(0, 6).map(record => {
            const state = String(record.state || 'candidate').toLowerCase();
            const stateClass = ['graduated', 'registered', 'candidate', 'quarantined', 'deprecated'].includes(state)
                ? state
                : 'candidate';
            const latestEvent = Array.isArray(record.events) && record.events.length
                ? record.events[record.events.length - 1]
                : null;
            const type = record.tool_type || 'generated';
            const usage = Number(record.usage_count || 0);
            const failures = Number(record.failure_count || 0);
            const updated = this.formatTimestamp(record.updated_at || record.created_at);

            return `
                <div class="mission-audit-row ${stateClass}">
                    <span>${this.escapeHtml(updated)}</span>
                    <strong>${this.escapeHtml(record.tool_name || 'tool')}</strong>
                    <span>${this.escapeHtml(state)}</span>
                </div>
                <div class="mission-audit-detail">
                    Type: ${this.escapeHtml(type)} Â· Usage: ${usage} Â· Failures: ${failures}
                </div>
                <div class="mission-audit-detail">
                    ${latestEvent ? this.escapeHtml(latestEvent.summary || latestEvent.event_type || '') : 'No lifecycle events recorded.'}
                </div>
            `;
        }).join('');

        const stateCounts = (records || []).reduce((acc, record) => {
            const state = String(record.state || 'candidate').toLowerCase();
            acc[state] = (acc[state] || 0) + 1;
            return acc;
        }, {});
        const summary = Object.entries(stateCounts)
            .map(([state, count]) => `${state}:${count}`)
            .join(' ');

        this.toolLifecyclePanel.innerHTML = `
            <div class="mission-status-header-row">
                <div class="mission-status-header">Tool Lifecycle</div>
                <div class="mission-status-freshness">${this.escapeHtml(summary || 'No records')}</div>
            </div>
            <div class="mission-audit-list">
                ${rows || '<div class="mission-audit-detail">No generated tools tracked yet.</div>'}
            </div>
        `;
    },

    renderExperimentScoreboard: function (records) {
        if (!this.scoreboardPanel) return;

        const rows = (records || []).slice(0, 6).map(record => {
            const scorecard = record.scorecard || {};
            const accepted = !!scorecard.accepted;
            const blocked = !!scorecard.blocked;
            const verdict = blocked ? 'Blocked' : accepted ? 'Accepted' : 'Rejected';
            const verdictClass = blocked ? 'blocked' : accepted ? 'accepted' : 'rejected';
            const testsRun = Number(scorecard.tests_run || 0);
            const testsPassed = Number(scorecard.tests_passed || 0);
            const files = Array.isArray(scorecard.files_touched) ? scorecard.files_touched.slice(0, 3) : [];
            const route = scorecard.suggested_route ? ` · Route: ${scorecard.suggested_route}` : '';
            const failure = scorecard.failure_reason && scorecard.failure_reason.failure_class
                ? ` · Failure: ${scorecard.failure_reason.failure_class}`
                : '';

            return `
                <div class="mission-audit-row ${verdictClass}">
                    <span>${this.escapeHtml(this.formatTimestamp(record.timestamp))}</span>
                    <strong>${this.escapeHtml(record.experiment_type || 'experiment')}</strong>
                    <span>${this.escapeHtml(verdict)}</span>
                </div>
                <div class="mission-audit-detail">
                    Tests: ${testsPassed}/${testsRun} · Risk: ${this.escapeHtml(scorecard.risk_level || 'low')}${route}${failure}
                </div>
                <div class="mission-audit-detail">
                    ${files.length ? `Files: ${this.escapeHtml(files.join(', '))}` : 'Files: none recorded'}
                </div>
            `;
        }).join('');

        this.scoreboardPanel.innerHTML = `
            <div class="mission-status-header-row">
                <div class="mission-status-header">Experiment Scoreboard</div>
                <div class="mission-status-freshness">Recent ${Array.isArray(records) ? records.length : 0}</div>
            </div>
            <div class="mission-audit-list">
                ${rows || '<div class="mission-audit-detail">No experiment scorecards recorded yet.</div>'}
            </div>
        `;
    },

    classifyAuditEvent: function (event) {
        const status = String(event.status || '').toLowerCase();
        const type = String(event.event_type || '').toLowerCase();
        if (status.includes('block')) return 'blocked';
        if (status.includes('fail') || status.includes('reject') || type.includes('failed')) return 'failed';
        if (type.startsWith('action_') || type.includes('policy')) return 'actions';
        if (type.startsWith('task_')) return 'tasks';
        return 'other';
    },

    renderActionAudit: function (events) {
        if (!this.actionAuditPanel) return;

        const filters = [
            { id: 'all', label: 'All' },
            { id: 'actions', label: 'Actions' },
            { id: 'tasks', label: 'Tasks' },
            { id: 'blocked', label: 'Blocked' },
            { id: 'failed', label: 'Failed' },
        ];

        const filteredEvents = (events || []).filter(event => {
            if (this.actionAuditFilter === 'all') return true;
            return this.classifyAuditEvent(event) === this.actionAuditFilter;
        });

        const filterControls = filters.map(filter => `
            <button class="mission-filter-chip ${this.actionAuditFilter === filter.id ? 'active' : ''}"
                type="button" data-filter="${this.escapeHtml(filter.id)}">${this.escapeHtml(filter.label)}</button>
        `).join('');

        const rows = filteredEvents.slice(0, 8).map(event => {
            const kind = this.classifyAuditEvent(event);
            const taskId = event.task_id ? String(event.task_id).substring(0, 12) : '';
            const source = event.source ? String(event.source).substring(0, 18) : '';
            const timestamp = this.formatTimestamp(event.timestamp);
            const type = String(event.event_type || 'event').replace(/_/g, ' ');
            const actionType = String(event.action_type || '').replace(/_/g, ' ');
            const status = String(event.status || kind || 'recorded').replace(/_/g, ' ');
            const details = [
                event.actor ? `Actor: ${event.actor}` : '',
                taskId ? `Task: ${taskId}` : '',
                source ? `Source: ${source}` : '',
                actionType ? `Lane: ${actionType}` : '',
            ].filter(Boolean).join(' · ');

            return `
                <div class="mission-audit-row ${kind}">
                    <span>${this.escapeHtml(timestamp)}</span>
                    <strong>${this.escapeHtml(type)}</strong>
                    <span>${this.escapeHtml(status)}</span>
                </div>
                <div class="mission-audit-detail">${this.escapeHtml(event.summary || '')}</div>
                ${details ? `<div class="mission-audit-detail">${this.escapeHtml(details)}</div>` : ''}
            `;
        }).join('');

        this.actionAuditPanel.innerHTML = `
            <div class="mission-status-header-row">
                <div class="mission-status-header">Action Audit Trail</div>
                <div class="mission-status-freshness">Recent ${Array.isArray(events) ? events.length : 0}</div>
            </div>
            <div class="mission-filter-row">${filterControls}</div>
            <div class="mission-audit-list">
                ${rows || '<div class="mission-audit-detail">No audit events for this filter yet.</div>'}
            </div>
        `;

        this.actionAuditPanel.querySelectorAll('.mission-filter-chip').forEach(button => {
            button.addEventListener('click', () => {
                this.actionAuditFilter = button.dataset.filter || 'all';
                this.renderActionAudit(events);
            });
        });
    },

    fetchStatusSnapshot: async function () {
        if (!this.statusPanel) return;

        try {
            const response = await fetch('/api/status/snapshot');
            const data = await response.json();

            if (data.success) {
                this.lastStatusAtMs = Number.isFinite(data.generated_at_ms) ? data.generated_at_ms : Date.now();
                this.snapshotSchemaVersion = data.schema_version ?? null;
                this.renderStatusSnapshot(data.snapshot, data.generated_at);
            } else {
                this.statusPanel.innerHTML = `<div class="error">Status unavailable: ${this.escapeHtml(data.error || 'unknown error')}</div>`;
                this.updateStaleState();
            }
        } catch (e) {
            console.error("Fetch status snapshot error:", e);
            this.statusPanel.innerHTML = `<div class="error">Status link failure: ${this.escapeHtml(e.message)}</div>`;
            this.updateStaleState();
        }
    },

    renderStatusSnapshot: function (snapshot, generatedAtIso) {
        if (!this.statusPanel || !snapshot) return;

        const toolsByType = Object.entries(snapshot.tools_by_type || {})
            .map(([toolType, count]) => `<span class="mission-kv-pill">${this.escapeHtml(toolType)}: ${count}</span>`)
            .join('');
        const delegationByWorker = Object.entries(snapshot.delegation_by_worker || {})
            .map(([worker, count]) => `<span class="mission-kv-pill">Worker ${this.escapeHtml(worker)}: ${count}</span>`)
            .join('');
        const delegationByScope = Object.entries(snapshot.delegation_by_scope || {})
            .map(([scope, count]) => `<span class="mission-kv-pill">Scope ${this.escapeHtml(scope)}: ${count}</span>`)
            .join('');
        const delegationByState = Object.entries(snapshot.delegation_by_state || {})
            .map(([state, count]) => `<span class="mission-kv-pill">State ${this.escapeHtml(state)}: ${count}</span>`)
            .join('');
        const topologyRows = (snapshot.delegation_topology || [])
            .slice(0, 5)
            .map(edge => `<div class="mission-audit-row"><span>${this.escapeHtml((edge.task_id || '').toString().substring(0, 8) || 'task')}</span><strong>${this.escapeHtml(edge.worker_profile || 'worker')}</strong><span>${this.escapeHtml(edge.scope_type || 'scope')}</span><span>${this.escapeHtml(edge.state || 'state')}</span></div><div class="mission-audit-detail">Capability: ${this.escapeHtml(edge.capability_profile || 'unknown')} · Retention: ${this.escapeHtml(edge.retention_policy || 'unknown')}</div>`)
            .join('');
        const inboxPreviewRows = (snapshot.work_inbox_preview || [])
            .slice(0, 3)
            .map(n => `<div class="mission-audit-row"><span>${this.escapeHtml((n.id || '').toString())}</span><strong>${this.escapeHtml((n.task_id || '').toString().substring(0, 8) || 'task')}</strong><span>${this.escapeHtml(n.status || 'unknown')}</span></div><div class="mission-audit-detail">${this.escapeHtml(n.message || '')}</div>`)
            .join('');
        const staleIdentityRows = (snapshot.identity_session_pointers_stale_preview || [])
            .slice(0, 3)
            .map(p => `<div class="mission-audit-row"><span>${this.escapeHtml((p.identity_key || '').toString())}</span><strong>${this.escapeHtml((p.session_id || '').toString().substring(0, 8) || 'session')}</strong><span>stale</span></div>`)
            .join('');

        this.statusPanel.innerHTML = `
            <div class="mission-status-header-row">
                <div class="mission-status-header">System Health</div>
                <div class="mission-status-freshness">Live · updated just now</div>
            </div>
            <div class="mission-kv-grid">
                <div class="mission-kv-item"><span>Tools</span><strong>${snapshot.tools_total}</strong></div>
                <div class="mission-kv-item"><span>Background Tasks</span><strong>${snapshot.background_tasks}</strong></div>
                <div class="mission-kv-item"><span>Delegated Tasks</span><strong>${snapshot.delegation_active_tasks || 0}</strong></div>
                <div class="mission-kv-item"><span>Chat Delegates</span><strong>${snapshot.delegation_chat_delegate_active_tasks || 0}</strong></div>
                <div class="mission-kv-item"><span>Work Inbox (Unread)</span><strong>${snapshot.work_inbox_unread || 0}</strong></div>
                <div class="mission-kv-item"><span>Work Inbox (Total)</span><strong>${snapshot.work_inbox_total || 0}</strong></div>
                <div class="mission-kv-item"><span>Identity Pointers</span><strong>${snapshot.identity_session_pointers_total || 0}</strong></div>
                <div class="mission-kv-item"><span>Telegram Pointers</span><strong>${(snapshot.identity_session_pointers_by_platform && snapshot.identity_session_pointers_by_platform.telegram) || 0}</strong></div>
                <div class="mission-kv-item"><span>Stale Identity Pointers</span><strong>${snapshot.identity_session_pointers_stale || 0}</strong></div>
                <div class="mission-kv-item"><span>Debug Mode</span><strong>${snapshot.debug_mode_enabled ? 'Enabled' : 'Disabled'}</strong></div>
                <div class="mission-kv-item"><span>Autonomous Learning</span><strong>${snapshot.autonomous_learning_enabled ? 'Enabled' : 'Disabled'}</strong></div>
            </div>
            <div class="mission-kv-pills">${toolsByType || '<span class="mission-kv-pill">No tools registered</span>'}</div>
            <div class="mission-kv-pills">${delegationByWorker || '<span class="mission-kv-pill">Worker mix unavailable</span>'}</div>
            <div class="mission-kv-pills">${delegationByScope || '<span class="mission-kv-pill">Scope mix unavailable</span>'}</div>
            <div class="mission-kv-pills">${delegationByState || '<span class="mission-kv-pill">State mix unavailable</span>'}</div>
            <div class="mission-audit-list">${topologyRows || '<div class="mission-audit-detail">No active delegation edges.</div>'}</div>
            <div class="mission-audit-list">${inboxPreviewRows || '<div class="mission-audit-detail">No unread work notices.</div>'}</div>
            <div class="mission-audit-list">${staleIdentityRows || '<div class="mission-audit-detail">No stale identity pointers.</div>'}</div>
            <div class="mission-status-meta">
                ${generatedAtIso ? `Generated: ${this.escapeHtml(generatedAtIso)}` : "Generated: n/a"}
                ${this.snapshotSchemaVersion !== null ? ` · Schema v${this.snapshotSchemaVersion}` : ""}
            </div>
        `;
        this.updateStaleState();
    },

    fetchReflectionSuggestions: async function () {
        if (!this.reflectionPanel) return;

        try {
            const response = await fetch('/api/status/reflection-suggestions?limit=12');
            const data = await response.json();
            if (!data.success) {
                this.reflectionPanel.innerHTML = `<div class="error">Reflection feed unavailable: ${this.escapeHtml(data.error || 'unknown error')}</div>`;
                return;
            }

            this.renderReflectionSuggestions(data.items || [], data.generated_at);
        } catch (e) {
            console.error('Reflection suggestion fetch error:', e);
            this.reflectionPanel.innerHTML = `<div class="error">Reflection feed failure: ${this.escapeHtml(e.message)}</div>`;
        }
    },


    renderReflectionSuggestions: function (items, generatedAtIso) {
        if (!this.reflectionPanel) return;

        const rows = (items || []).slice(0, 12).map(item => {
            const templates = Array.isArray(item.recommended_specialist_templates)
                ? item.recommended_specialist_templates
                : [];
            const templateBadges = templates.length
                ? templates.map(t => `<span class="mission-kv-pill">${this.escapeHtml(t)}</span>`).join('')
                : '';

            return `
                <div class="reflection-suggestion" data-insight-id="${this.escapeHtml(item.insight_id || item.suggestion_id || '')}">
                    <div class="mission-audit-row">
                        <span>${this.escapeHtml((item.insight_id || item.suggestion_id || '').toString().substring(0, 12) || 'insight')}</span>
                        <strong>${this.escapeHtml(item.type || 'UNKNOWN')}</strong>
                        <span class="status-${this.escapeHtml(item.status || 'UNKNOWN')}">${this.escapeHtml(item.status || 'UNKNOWN')}</span>
                    </div>
                    <div class="mission-audit-detail">${this.escapeHtml(item.description || '')}</div>
                    ${templateBadges ? `<div class="mission-kv-pills">${templateBadges}</div>` : ''}
                </div>
            `;
        }).join('');

        this.reflectionPanel.innerHTML = `
            <div class="mission-status-header-row">
                <div class="mission-status-header">Recent Autonomous Insights</div>
                <div class="mission-status-freshness">Monitoring ${Array.isArray(items) ? items.length : 0} items</div>
            </div>
            <div class="mission-audit-list">${rows || '<div class="mission-audit-detail">No recent insights monitored.</div>'}</div>
            <div class="mission-status-meta">${generatedAtIso ? `Generated: ${this.escapeHtml(generatedAtIso)}` : 'Generated: n/a'}</div>
        `;
    },


    showPrompt: function(title, message, placeholder) {
        return new Promise(resolve => {
            let modal = document.getElementById('mc-prompt-modal');
            if (!modal) {
                modal = document.createElement('div');
                modal.id = 'mc-prompt-modal';
                modal.className = 'modal-overlay';
                modal.innerHTML = `
                    <div class="modal-content" style="max-width: 400px; background: rgba(10, 15, 30, 0.95); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 20px;">
                        <div class="modal-header" id="mc-prompt-title" style="font-size: 16px; margin-bottom: 15px; color: var(--accent-cyan);">Title</div>
                        <div class="modal-body">
                            <div id="mc-prompt-message" style="margin-bottom:10px; font-size: 14px;"></div>
                            <input type="text" id="mc-prompt-input" style="width:100%; padding:10px; border-radius:4px; background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.2); color:#eee; box-sizing: border-box;">
                        </div>
                        <div class="modal-buttons" style="margin-top:20px; display:flex; justify-content:flex-end; gap:10px;">
                            <button class="btn-modal cancel" id="mc-prompt-cancel" style="padding:8px 16px; background:rgba(255,255,255,0.1); border:none; border-radius:4px; color:white; cursor:pointer;">Cancel</button>
                            <button class="btn-modal confirm" id="mc-prompt-confirm" style="padding:8px 16px; background:var(--accent-cyan); border:none; border-radius:4px; color:black; font-weight:bold; cursor:pointer;">Confirm</button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modal);
            }
            document.getElementById('mc-prompt-title').textContent = title;
            document.getElementById('mc-prompt-message').textContent = message;
            const input = document.getElementById('mc-prompt-input');
            input.placeholder = placeholder || '';
            input.value = '';
            
            const cancelBtn = document.getElementById('mc-prompt-cancel');
            const confirmBtn = document.getElementById('mc-prompt-confirm');
            
            const cleanup = () => {
                modal.classList.remove('active');
                cancelBtn.onclick = null;
                confirmBtn.onclick = null;
            };
            
            cancelBtn.onclick = () => {
                cleanup();
                resolve(null);
            };
            confirmBtn.onclick = () => {
                cleanup();
                resolve(input.value);
            };
            
            modal.classList.add('active');
            input.focus();
        });
    },

    showAlert: function(title, message) {
        return new Promise(resolve => {
            let modal = document.getElementById('mc-alert-modal');
            if (!modal) {
                modal = document.createElement('div');
                modal.id = 'mc-alert-modal';
                modal.className = 'modal-overlay';
                modal.innerHTML = `
                    <div class="modal-content" style="max-width: 400px; background: rgba(10, 15, 30, 0.95); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 20px;">
                        <div class="modal-header" id="mc-alert-title" style="font-size: 16px; margin-bottom: 15px; color: var(--accent-cyan);">Title</div>
                        <div class="modal-body" id="mc-alert-message" style="margin-bottom:20px; font-size: 14px; line-height: 1.5;"></div>
                        <div class="modal-buttons" style="display:flex; justify-content:flex-end;">
                            <button class="btn-modal confirm" id="mc-alert-confirm" style="padding:8px 16px; background:var(--accent-cyan); border:none; border-radius:4px; color:black; font-weight:bold; cursor:pointer;">OK</button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modal);
            }
            document.getElementById('mc-alert-title').textContent = title;
            document.getElementById('mc-alert-message').textContent = message;
            const confirmBtn = document.getElementById('mc-alert-confirm');
            
            confirmBtn.onclick = () => {
                modal.classList.remove('active');
                resolve();
            };
            modal.classList.add('active');
        });
    },

    handleReflectionAction: async function (item, action) {
        if (!item || !item.actions) return;

        let endpoint = '';
        let payload = {};

        if (action === 'approve') {
            endpoint = item.actions.approve;
            const feedback = await this.showPrompt('Approve Action', 'Optional approval feedback:', '');
            if (feedback === null) return;
            if (feedback) payload.feedback = feedback;
        } else if (action === 'reject') {
            endpoint = item.actions.reject;
            const feedback = await this.showPrompt('Reject Action', 'Optional rejection reason:', '');
            if (feedback === null) return;
            if (feedback) payload.feedback = feedback;
        } else if (action === 'spawn') {
            endpoint = item.actions.spawn_specialist;
            const templates = Array.isArray(item.recommended_specialist_templates) ? item.recommended_specialist_templates : [];
            const templateId = templates.length ? String(templates[0] || '').trim() : '';
            if (!templateId) {
                await this.showAlert('Error', 'No recommended specialist template is available for this suggestion.');
                return;
            }
            payload = {
                template_id: templateId,
                task_description: String(item.description || '').trim(),
            };
        }

        if (!endpoint) return;

        try {
            const res = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (!data.success) {
                await this.showAlert('Action Failed', data.error || 'Reflection action failed.');
                return;
            }

            const successMsg = action === 'spawn'
                ? `Specialist spawned: ${(data.task_id || '').toString().substring(0, 12)}`
                : `Suggestion ${(item.insight_id || '').toString().substring(0, 12)} ${action}d.`;
            await this.showAlert('Success', successMsg);

            this.fetchReflectionSuggestions();
            this.fetchTasks();
            this.fetchStatusSnapshot();
        } catch (e) {
            console.error('Reflection action error:', e);
            await this.showAlert('Error', `Reflection action failed: ${e.message}`);
        }
    },


    fetchTasks: async function () {
        if (!this.board) return;
        this.board.innerHTML = '<div class="loading">Aligning satellites...</div>';

        try {
            const response = await fetch('/api/tasks');
            const data = await response.json();

            if (data.success) {
                this.renderTasks(data.tasks);
            } else {
                this.board.innerHTML = `<div class="error">Failed to fetch directives: ${data.error}</div>`;
            }
        } catch (e) {
            console.error("Fetch tasks error:", e);
            this.board.innerHTML = `<div class="error">Comm link failure: ${e.message}</div>`;
        }
    },

    renderTasks: function (tasks) {
        this.board.innerHTML = '';
        if (tasks.length === 0) {
            this.board.innerHTML = '<div class="empty-state">No active directives. Systems idle.</div>';
            return;
        }

        tasks.forEach(task => {
            const card = this.createTaskCard(task);
            this.board.appendChild(card);
        });
    },

    isFailureStatus: function (status) {
        return [
            'FAILED_PRE_REVIEW', 'FAILED_DURING_APPLY', 'FAILED_UNKNOWN',
            'FAILED_CODE_GENERATION', 'FAILED_INTERRUPTED', 'PROJECT_PLAN_FAILED_STEP',
            'CRITIC_REVIEW_REJECTED', 'POST_MOD_TEST_FAILED'
        ].includes(status);
    },

    createTaskCard: function (task) {
        const div = document.createElement('div');
        div.className = `task-card status-${task.status}`;
        div.id = `task-${task.task_id}`;

        let statusClass = 'status-DEFAULT';
        if (task.status.includes('FAIL')) statusClass = 'status-FAILED';
        else if (task.status.includes('SUCCESS')) statusClass = 'status-SUCCESS';
        else if (task.status === 'PLANNING' || task.status === 'GENERATING_CODE') statusClass = 'status-ACTIVE';

        const progressHtml = task.progress_percentage !== null
            ? `<div class="task-progress">
                 <div class="progress-bar" style="width: ${task.progress_percentage}%"></div>
               </div>`
            : '';

        // Plan is now hidden in footer by default
        let planHtml = '';
        if (task.task_type === 'HIERARCHICAL_PROJECT_EXECUTION' && task.details.project_plan) {
            planHtml = this.renderHierarchicalPlan(task);
        }

        const isDelegatedTask = task.task_type === 'EPHEMERAL_AGENT_TASK';
        const delegateMeta = isDelegatedTask
            ? `<div class="task-current-step">🧠 Worker: ${this.escapeHtml((task.details && task.details.worker_profile) || 'coder_worker')} · Scope: ${this.escapeHtml((task.details && task.details.scope_type) || 'session')} · State: ${this.escapeHtml(task.status || 'UNKNOWN')} · Source: ${this.escapeHtml((task.details && task.details.source) || 'unknown')}</div>`
            : '';

        div.innerHTML = `
            <div class="task-header">
                <div style="display:flex; align-items:center; gap:10px;">
                    <span class="task-expand-icon">▼</span>
                    <span class="task-id">${task.task_id.substring(0, 8)}</span>
                </div>
                <span class="task-status-badge ${statusClass}">${task.status.replace(/_/g, ' ')}</span>
            </div>
            <div class="task-body">
                <div class="task-desc">${task.description}</div>
                ${delegateMeta}
                ${task.current_step_description ? `<div class="task-current-step">▶ ${task.current_step_description}</div>` : ''}
                ${task.output_preview ? `<div class="task-preview"><code>${this.escapeHtml(task.output_preview)}</code></div>` : ''}
            </div>
            ${progressHtml}
            <div class="task-footer">
                <div class="task-plan-details">
                    ${planHtml || '<div class="no-plan">No detailed plan available.</div>'}
                </div>
                <div class="task-controls">
                     <button class="btn-control stop" data-action="stop">⛔ STOP TASK</button>
                     ${this.isFailureStatus(task.status) ? '<button class="btn-control assistant-action" data-action="retry">♻ RETRY</button><button class="btn-control assistant-action" data-action="summarize">🔎 EXPLAIN</button><button class="btn-control assistant-action" data-action="pause">⏸ PAUSE AUTO</button>' : ''}
                </div>
                <div class="task-feedback">
                    <input type="text" placeholder="Inject instructions to agent..." class="feedback-input">
                    <button class="btn-send-feedback">SEND</button>
                </div>
            </div>
        `;

        // Event Listeners
        div.addEventListener('click', (e) => {
            // Toggle expansion unless clicking interactive elements
            if (e.target.closest('button') || e.target.closest('input')) return;
            this.toggleTaskCard(task.task_id);
        });

        const stopBtn = div.querySelector('.btn-control.stop');
        if (stopBtn) {
            stopBtn.addEventListener('click', () => this.stopTask(task.task_id));
        }

        div.querySelectorAll('.btn-control.assistant-action').forEach(btn => {
            btn.addEventListener('click', () => this.triggerAssistantAction(task.task_id, btn.dataset.action));
        });

        const sendBtn = div.querySelector('.btn-send-feedback');
        const input = div.querySelector('.feedback-input');
        if (sendBtn && input) {
            sendBtn.addEventListener('click', () => this.sendTaskMessage(task.task_id, input.value, input));
            input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') this.sendTaskMessage(task.task_id, input.value, input);
            });
        }

        return div;
    },

    toggleTaskCard: function (taskId) {
        const card = document.getElementById(`task-${taskId}`);
        if (card) {
            card.classList.toggle('expanded');
        }
    },

    stopTask: async function (taskId) {
        if (!confirm("Are you sure you want to stop this task?")) return;
        try {
            const res = await fetch(`/api/tasks/${taskId}/stop`, { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                // UI update via socket usually, but alert for now
                console.log("Task stopped.");
            } else {
                alert("Failed to stop task: " + data.error);
            }
        } catch (e) {
            console.error(e);
        }
    },

    sendTaskMessage: async function (taskId, message, inputElem) {
        if (!message.trim()) return;
        try {
            const res = await fetch(`/api/tasks/${taskId}/message`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: message })
            });
            const data = await res.json();
            if (data.success) {
                if (inputElem) inputElem.value = ''; // Clear input
                // Maybe show a toast
                console.log("Message sent.");
            } else {
                alert("Failed to send message: " + data.error);
            }
        } catch (e) {
            console.error(e);
        }
    },

    triggerAssistantAction: async function (taskId, action) {
        if (action === 'pause' && !confirm('Pause autonomous retries for this task?')) {
            return;
        }

        try {
            const res = await fetch(`/api/tasks/${taskId}/assistant-action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action })
            });
            const data = await res.json();

            if (data.success) {
                alert(data.message || `Action '${action}' completed.`);
                this.fetchTasks();
                this.fetchStatusSnapshot();
                this.fetchHealthAudit();
                this.fetchBackgroundCadence();
            } else {
                alert(data.error || `Action '${action}' failed.`);
            }
        } catch (e) {
            console.error('Assistant action error:', e);
            alert(`Assistant action failed: ${e.message}`);
        }
    },

    renderHierarchicalPlan: function (task) {
        // Group steps by outline group
        const plan = task.details.project_plan || [];
        const statuses = task.details.plan_step_statuses || [];

        const groups = {};
        plan.forEach(step => {
            const groupName = step.outline_group || 'General';
            if (!groups[groupName]) groups[groupName] = [];
            groups[groupName].push(step);
        });

        let html = '<div class="plan-container">';

        for (const [groupName, steps] of Object.entries(groups)) {
            html += `
                <div class="plan-outline-group">
                    <div class="plan-group-title">${groupName}</div>
                    <div class="plan-steps">
            `;

            steps.forEach(step => {
                const statusObj = statuses.find(s => s.step_id === step.step_id) || {};
                const status = statusObj.status || 'pending'; // pending, running, success, failed
                let icon = '○';
                if (status === 'success') icon = '●'; // or checkmark
                if (status === 'running') icon = '▶';
                if (status === 'failed') icon = '✖';

                html += `
                    <div class="plan-step ${status}" title="${step.description}">
                        <span class="step-icon">${icon}</span>
                        <span class="step-desc">${step.description}</span>
                    </div>
                `;
            });

            html += `
                    </div>
                </div>
            `;
        }
        html += '</div>';
        return html;
    },

    handleTaskUpdate: function (taskData) {
        // Find existing card
        const card = document.getElementById(`task-${taskData.task_id}`);
        const wasExpanded = card ? card.classList.contains('expanded') : false;

        if (card) {
            // Replace it with new version
            const newCard = this.createTaskCard(taskData);
            if (wasExpanded) newCard.classList.add('expanded');
            this.board.replaceChild(newCard, card);
        } else {
            // Add new card
            const newCard = this.createTaskCard(taskData);
            this.board.insertBefore(newCard, this.board.firstChild);
        }
    },

    formatTimestamp: function (timestampValue) {
        let timestampMs = 0;

        if (typeof timestampValue === 'string' && timestampValue.trim()) {
            const parsed = Date.parse(timestampValue);
            timestampMs = Number.isFinite(parsed) ? parsed : 0;
        } else {
            const ts = Number(timestampValue);
            if (Number.isFinite(ts) && ts > 0) {
                timestampMs = ts > 100000000000 ? ts : ts * 1000;
            }
        }

        if (!Number.isFinite(timestampMs) || timestampMs <= 0) return 'Never';

        const diffMs = Date.now() - timestampMs;
        const diffSeconds = Math.max(0, Math.floor(diffMs / 1000));

        if (diffSeconds < 60) return `${diffSeconds}s ago`;
        if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m ago`;
        if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)}h ago`;

        return new Date(timestampMs).toLocaleString();
    },

    escapeHtml: function (text) {
        if (!text) return '';
        return text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }
};

// Auto-init when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Check if view exists (index.html might have conditional rendering, though not here)
    if (document.getElementById('view-mission-control')) {
        missionControl.init();
    }
});
