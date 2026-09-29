document.addEventListener('DOMContentLoaded', () => {
    const approvalsList = document.getElementById('approvals-list');
    const normalApprovalsList = document.getElementById('normal-approvals-list');
    const normalApprovalCount = document.getElementById('normal-approval-count');
    const badge = document.getElementById('approval-badge');
    const refreshBtn = document.getElementById('refresh-approvals-btn');
    const approvalsView = document.getElementById('view-sidebar-approvals');
    let lastFetchAt = 0;

    const escapeHtml = (value) => String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');

    // Poll quickly while open, but keep a slow background refresh for the badge.
    setInterval(() => {
        const viewOpen = !approvalsView || !approvalsView.classList.contains('hidden');
        if (viewOpen || Date.now() - lastFetchAt >= 30000) fetchApprovals();
    }, 5000);
    // Initial fetch
    setTimeout(fetchApprovals, 1000);

    if (approvalsView) {
        new MutationObserver(() => {
            if (!approvalsView.classList.contains('hidden')) fetchApprovals();
        }).observe(approvalsView, { attributes: true, attributeFilter: ['class'] });
    }

    if (refreshBtn) {
        refreshBtn.addEventListener('click', fetchApprovals);
    }

    async function fetchApprovals() {
        if (!approvalsList) return;
        lastFetchAt = Date.now();
        try {
            const res = await fetch('/api/approvals');
            const data = await res.json();
            if (data.success) {
                renderApprovals(data.approvals);
            }
        } catch (e) {
            console.error("Failed to fetch approvals", e);
        }
    }

    let lastApprovalsJson = '';

    function renderApprovals(approvals) {
        const currentJson = JSON.stringify(approvals);
        if (currentJson === lastApprovalsJson) return;
        lastApprovalsJson = currentJson;
        // Update Badge
        if (badge) {
            if (approvals.length > 0) {
                badge.textContent = approvals.length;
                badge.classList.remove('hidden');
            } else {
                badge.classList.add('hidden');
            }
        }
        if (normalApprovalCount) {
            normalApprovalCount.textContent = approvals.length;
        }

        if (approvals.length === 0) {
            approvalsList.innerHTML = '<div class="empty-state-panel"><strong>Nothing needs your approval</strong><span>New decisions will appear here when Weebo needs your input.</span><button type="button" class="btn-xs" data-refresh-empty>Refresh</button></div>';
            approvalsList.querySelector('[data-refresh-empty]')?.addEventListener('click', fetchApprovals);
            if (normalApprovalsList) {
                normalApprovalsList.innerHTML = '<div class="normal-empty-state">No approvals waiting. Weebo will surface the next decision here.</div>';
            }
            return;
        }

        approvalsList.innerHTML = '';
        if (normalApprovalsList) normalApprovalsList.innerHTML = '';
        approvals.forEach(req => {
            approvalsList.appendChild(createApprovalCard(req));
            if (normalApprovalsList) {
                normalApprovalsList.appendChild(createApprovalCard(req, true));
            }
        });
    }

    function createApprovalCard(req, compact = false) {
        const card = document.createElement('div');
        card.className = compact ? 'approval-card glass-panel-light compact-approval-card' : 'approval-card glass-panel-light';

        let typeLabel = formatType(req.type);
        const details = req.data && (req.data.details || req.data.provenance || req.data.metadata);
        let detailsText = '';
        if (details) {
            try {
                detailsText = JSON.stringify(details, null, 2).slice(0, 12000);
            } catch (e) {
                detailsText = String(details);
            }
        }
        const rollbackPlan = req.data?.provenance?.rollback_plan || req.data?.details?.rollback_plan;

        card.innerHTML = `
            <div class="approval-header">
                <span class="approval-type type-${escapeHtml(req.type)}">${escapeHtml(typeLabel)}</span>
                <span class="approval-time">${formatApprovalTime(req)}</span>
            </div>
            <div class="approval-body">
                ${req.data && req.data.related_tool_name ? `<div class="target-name">Target Tool: <code>${escapeHtml(req.data.related_tool_name)}</code></div>` : ''}
                ${req.data && req.data.details && req.data.details.target_file ? `<div class="target-name">Target File: <code>${escapeHtml(req.data.details.target_file)}</code></div>` : ''}
                <p class="approval-desc">${escapeHtml(req.description)}</p>
                ${rollbackPlan ? `<div class="approval-rollback"><strong>Rollback:</strong> ${escapeHtml(rollbackPlan)}</div>` : ''}
                ${detailsText ? `<details class="approval-details"><summary>View proposed change details</summary><pre>${escapeHtml(detailsText)}</pre></details>` : ''}
                <textarea class="approval-feedback-input" placeholder="Optional feedback..." style="width:100%; margin-top:10px; padding:8px; border-radius:4px; background:rgba(0,0,0,0.2); border:1px solid rgba(255,255,255,0.1); color:#eee; font-size:12px; resize:vertical; min-height:${compact ? '44px' : '60px'};"></textarea>
            </div>
            <div class="approval-actions">
                <button class="btn-deny" data-id="${escapeHtml(req.id)}">Deny</button>
                <button class="btn-approve" data-id="${escapeHtml(req.id)}">Approve</button>
            </div>
        `;

        const feedbackInput = card.querySelector('.approval-feedback-input');

        card.querySelector('.btn-approve').addEventListener('click', () => {
            const feedback = feedbackInput.value;
            handleAction(req.id, 'approve', feedback);
        });
        card.querySelector('.btn-deny').addEventListener('click', () => {
            const feedback = feedbackInput.value;
            handleAction(req.id, 'deny', feedback);
        });

        return card;
    }

    function formatApprovalTime(req) {
        const raw = req && (req.timestamp ?? req.created_at);
        if (raw === null || raw === undefined || raw === '') return 'Unknown time';

        let date;
        if (typeof raw === 'number') {
            date = new Date(raw > 100000000000 ? raw : raw * 1000);
        } else if (/^\d+(\.\d+)?$/.test(String(raw).trim())) {
            const numeric = Number(raw);
            date = new Date(numeric > 100000000000 ? numeric : numeric * 1000);
        } else {
            date = new Date(raw);
        }

        return Number.isNaN(date.getTime()) ? 'Unknown time' : date.toLocaleTimeString();
    }

    function formatType(type) {
        if (type === 'tool_bug_suspected') return 'Tool Fix';
        if (type === 'tool_enhancement_suggested') return 'Tool Upgrade';
        if (type === 'new_tool_suggested') return 'New Tool';
        if (type === 'knowledge_gap_identified') return 'Knowledge Gap';
        if (type === 'learned_fact_correction') return 'Fact Correction';
        if (type === 'suggestion') return 'Suggestion';
        if (type === 'architect_proposal') return 'Architect Proposal';
        if (type === 'architect_source_change') return 'Architect Source Change';
        return type.replace(/_/g, ' ').toUpperCase();
    }

    async function handleAction(id, action, feedback) {
        const btn = document.querySelector(`button[data-id="${id}"].btn-${action}`);
        const originalText = btn ? btn.textContent : '';
        if (btn) {
            btn.disabled = true;
            btn.textContent = action === 'approve' ? 'Queuing...' : 'Denying...';
        }

        try {
            const res = await fetch(`/api/approvals/${id}/${action}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ feedback: feedback })
            });
            const data = await res.json();
            if (data.success) {
                if (btn) {
                    btn.textContent = data.task_id ? 'Queued' : 'Done';
                }
                fetchApprovals();
            } else {
                window.showAlert("Error", "Action failed: " + (data.error || "Unknown error"));
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = originalText;
                }
            }
        } catch (e) {
            console.error(e);
            if (btn) {
                btn.disabled = false;
                btn.textContent = originalText;
            }
            window.showAlert("Error", "An unexpected error occurred.");
        }
    }
});
