
// static/js/mission_control.js

// Using the socket instance from main.js if available, or assume global 'socket'
// Ensure this script is loaded AFTER main.js

const missionControl = {
    board: document.getElementById('mission-control-board'),
    refreshBtn: document.getElementById('refresh-tasks-btn'),

    init: function () {
        console.log("Mission Control Initialized");
        this.fetchTasks();

        if (this.refreshBtn) {
            this.refreshBtn.addEventListener('click', () => this.fetchTasks());
        }

        // Listen for socket events
        if (typeof socket !== 'undefined') {
            socket.on('task_update', (taskData) => {
                this.handleTaskUpdate(taskData);
            });
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
