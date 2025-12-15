
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

        let planHtml = '';
        if (task.task_type === 'HIERARCHICAL_PROJECT_EXECUTION' && task.details.project_plan) {
            planHtml = this.renderHierarchicalPlan(task);
        }

        div.innerHTML = `
            <div class="task-header">
                <span class="task-id">${task.task_id.substring(0, 8)}</span>
                <span class="task-status-badge ${statusClass}">${task.status.replace(/_/g, ' ')}</span>
            </div>
            <div class="task-body">
                <div class="task-desc">${task.description}</div>
                ${task.current_step_description ? `<div class="task-current-step">▶ ${task.current_step_description}</div>` : ''}
                ${task.output_preview ? `<div class="task-preview"><code>${this.escapeHtml(task.output_preview)}</code></div>` : ''}
                ${planHtml}
            </div>
            ${progressHtml}
        `;
        return div;
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
        if (card) {
            // Replace it with new version (simpler than selective DOM update for now)
            const newCard = this.createTaskCard(taskData);
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
