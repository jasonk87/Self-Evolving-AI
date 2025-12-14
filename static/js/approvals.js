document.addEventListener('DOMContentLoaded', () => {
    const approvalsList = document.getElementById('approvals-list');
    const badge = document.getElementById('approval-badge');
    const refreshBtn = document.getElementById('refresh-approvals-btn');

    // Poll every 5 seconds
    setInterval(fetchApprovals, 5000);
    // Initial fetch
    setTimeout(fetchApprovals, 1000);

    if (refreshBtn) {
        refreshBtn.addEventListener('click', fetchApprovals);
    }

    async function fetchApprovals() {
        if (!approvalsList) return;
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

        if (approvals.length === 0) {
            approvalsList.innerHTML = '<div class="empty-state" style="opacity:0.5; text-align:center; margin-top:20px;">No pending approvals.</div>';
            return;
        }

        approvalsList.innerHTML = '';
        approvals.forEach(req => {
            const card = document.createElement('div');
            card.className = 'approval-card glass-panel-light';

            let typeLabel = formatType(req.type);

            card.innerHTML = `
                <div class="approval-header">
                    <span class="approval-type type-${req.type}">${typeLabel}</span>
                    <span class="approval-time">${new Date(req.timestamp * 1000).toLocaleTimeString()}</span>
                </div>
                <div class="approval-body">
                    ${req.data.related_tool_name ? `<div class="target-name">Target Tool: <code>${req.data.related_tool_name}</code></div>` : ''}
                    ${req.data.details && req.data.details.target_file ? `<div class="target-name">Target File: <code>${req.data.details.target_file}</code></div>` : ''}
                    <p class="approval-desc">${req.description}</p>
                    <textarea class="approval-feedback-input" placeholder="Optional feedback (e.g., reason for denial, or instructions)..." style="width:100%; margin-top:10px; padding:8px; border-radius:4px; background:rgba(0,0,0,0.2); border:1px solid rgba(255,255,255,0.1); color:#eee; font-size:12px; resize:vertical; min-height:60px;"></textarea>
                </div>
                <div class="approval-actions">
                    <button class="btn-deny" data-id="${req.id}">Deny</button>
                    <button class="btn-approve" data-id="${req.id}">Approve</button>
                </div>
            `;

            // Listeners
            const feedbackInput = card.querySelector('.approval-feedback-input');

            card.querySelector('.btn-approve').addEventListener('click', () => {
                const feedback = feedbackInput.value;
                handleAction(req.id, 'approve', feedback);
            });
            card.querySelector('.btn-deny').addEventListener('click', () => {
                const feedback = feedbackInput.value;
                handleAction(req.id, 'deny', feedback);
            });

            approvalsList.appendChild(card);
        });
    }

    function formatType(type) {
        if (type === 'tool_bug_suspected') return 'Tool Fix';
        if (type === 'tool_enhancement_suggested') return 'Tool Upgrade';
        if (type === 'new_tool_suggested') return 'New Tool';
        if (type === 'knowledge_gap_identified') return 'Knowledge Gap';
        if (type === 'learned_fact_correction') return 'Fact Correction';
        if (type === 'suggestion') return 'Suggestion';
        if (type === 'architect_proposal') return 'Architect Proposal';
        return type.replace(/_/g, ' ').toUpperCase();
    }

    async function handleAction(id, action, feedback) {
        const btn = document.querySelector(`button[data-id="${id}"].btn-${action}`);
        if (btn) btn.disabled = true;

        try {
            const res = await fetch(`/api/approvals/${id}/${action}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ feedback: feedback })
            });
            const data = await res.json();
            if (data.success) {
                fetchApprovals();
            } else {
                window.showAlert("Error", "Action failed: " + (data.error || "Unknown error"));
                if (btn) btn.disabled = false;
            }
        } catch (e) {
            console.error(e);
            if (btn) btn.disabled = false;
            window.showAlert("Error", "An unexpected error occurred.");
        }
    }
});
