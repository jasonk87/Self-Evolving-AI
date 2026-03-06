
// static/js/mission_control.js

// Using the socket instance from main.js if available, or assume global 'socket'
// Ensure this script is loaded AFTER main.js

const missionControl = {
    board: document.getElementById('mission-control-board'),
    statusPanel: document.getElementById('mission-control-status'),
    healthPanel: document.getElementById('mission-control-health'),
    reflectionPanel: document.getElementById('mission-control-reflection'),
    cadencePanel: document.getElementById('mission-control-cadence'),
    refreshBtn: document.getElementById('refresh-tasks-btn'),
    statusPollIntervalMs: 8000,
    staleAfterMs: 20000,
    statusPollTimer: null,
    staleCheckTimer: null,
    lastStatusAtMs: null,
    snapshotSchemaVersion: null,

    init: function () {
        console.log("Mission Control Initialized");
        this.fetchTasks();
        this.fetchStatusSnapshot();
        this.fetchHealthAudit();
        this.fetchBackgroundCadence();
        this.fetchReflectionSuggestions();
        this.startStatusPolling();

        if (this.refreshBtn) {
            this.refreshBtn.addEventListener('click', () => {
                this.fetchTasks();
                this.fetchStatusSnapshot();
                this.fetchHealthAudit();
                this.fetchBackgroundCadence();
                this.fetchReflectionSuggestions();
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
            });
        }
    },


    initBackgroundSwitches: async function() {
        const d_switch = document.getElementById('switch-allow-dreamer');
        const m_switch = document.getElementById('switch-allow-memory');
        const a_switch = document.getElementById('switch-allow-autofix');

        if (!d_switch || !m_switch || !a_switch) return;

        try {
            const res = await fetch('/api/system/config');
            const config = await res.json();

            d_switch.checked = config.ALLOW_DREAMER !== false;
            m_switch.checked = config.ALLOW_MEMORY_LEARNING !== false;
            a_switch.checked = config.ALLOW_AUTO_FIXING !== false;

        } catch(e) { console.error("Could not init switches", e); }

        const toggleConfig = async (key, val) => {
            const fd = new FormData();
            fd.append(key, val);
            try {
                await fetch('/api/system/config', { method: 'POST', body: fd });
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
                : '<span class="mission-kv-pill">No template recommendation</span>';

            return `
                <div class="reflection-suggestion" data-insight-id="${this.escapeHtml(item.insight_id || '')}">
                    <div class="mission-audit-row">
                        <span>${this.escapeHtml((item.insight_id || '').toString().substring(0, 12) || 'insight')}</span>
                        <strong>${this.escapeHtml(item.type || 'UNKNOWN')}</strong>
                        <span>${this.escapeHtml(item.status || 'UNKNOWN')}</span>
                    </div>
                    <div class="mission-audit-detail">${this.escapeHtml(item.description || '')}</div>
                    <div class="mission-kv-pills">${templateBadges}</div>
                    <div class="reflection-actions">
                        <button class="btn-control assistant-action" data-reflection-action="approve">Approve</button>
                        <button class="btn-control assistant-action" data-reflection-action="reject">Reject</button>
                        <button class="btn-control assistant-action" data-reflection-action="spawn" ${templates.length ? '' : 'disabled title="No recommended template available"'}>Spawn Specialist</button>
                    </div>
                </div>
            `;
        }).join('');

        this.reflectionPanel.innerHTML = `
            <div class="mission-status-header-row">
                <div class="mission-status-header">Reflection & Specialist Management</div>
                <div class="mission-status-freshness">Pending ${Array.isArray(items) ? items.length : 0}</div>
            </div>
            <div class="mission-audit-list">${rows || '<div class="mission-audit-detail">No pending reflection suggestions.</div>'}</div>
            <div class="mission-status-meta">${generatedAtIso ? `Generated: ${this.escapeHtml(generatedAtIso)}` : 'Generated: n/a'}</div>
        `;

        this.reflectionPanel.querySelectorAll('[data-reflection-action]').forEach(btn => {
            btn.addEventListener('click', async (event) => {
                const action = event.currentTarget.getAttribute('data-reflection-action');
                const card = event.currentTarget.closest('.reflection-suggestion');
                const insightId = card ? card.getAttribute('data-insight-id') : '';
                const item = (items || []).find(i => String(i.insight_id || '') === String(insightId || ''));
                if (!item) return;
                await this.handleReflectionAction(item, action);
            });
        });
    },


    handleReflectionAction: async function (item, action) {
        if (!item || !item.actions) return;

        let endpoint = '';
        let payload = {};

        if (action === 'approve') {
            endpoint = item.actions.approve;
            const feedback = prompt('Optional approval feedback:', '');
            if (feedback) payload.feedback = feedback;
        } else if (action === 'reject') {
            endpoint = item.actions.reject;
            const feedback = prompt('Optional rejection reason:', '');
            if (feedback) payload.feedback = feedback;
        } else if (action === 'spawn') {
            endpoint = item.actions.spawn_specialist;
            const templates = Array.isArray(item.recommended_specialist_templates) ? item.recommended_specialist_templates : [];
            const templateId = templates.length ? String(templates[0] || '').trim() : '';
            if (!templateId) {
                alert('No recommended specialist template is available for this suggestion.');
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
                alert(data.error || 'Reflection action failed.');
                return;
            }

            const successMsg = action === 'spawn'
                ? `Specialist spawned: ${(data.task_id || '').toString().substring(0, 12)}`
                : `Suggestion ${(item.insight_id || '').toString().substring(0, 12)} ${action}d.`;
            alert(successMsg);

            this.fetchReflectionSuggestions();
            this.fetchTasks();
            this.fetchStatusSnapshot();
        } catch (e) {
            console.error('Reflection action error:', e);
            alert(`Reflection action failed: ${e.message}`);
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

    formatTimestamp: function (timestampSeconds) {
        const ts = Number(timestampSeconds);
        if (!Number.isFinite(ts) || ts <= 0) return 'Never';

        const timestampMs = ts * 1000;
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
