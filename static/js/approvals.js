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

    function renderApprovals(approvals) {
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
                    <p class="approval-desc">${req.description}</p>
                </div>
                <div class="approval-actions">
                    <button class="btn-deny" data-id="${req.id}">Deny</button>
                    <button class="btn-approve" data-id="${req.id}">Approve</button>
                </div>
            `;

            // Listeners
            card.querySelector('.btn-approve').addEventListener('click', () => handleAction(req.id, 'approve'));
            card.querySelector('.btn-deny').addEventListener('click', () => handleAction(req.id, 'deny'));

            approvalsList.appendChild(card);
        });
    }

    function formatType(type) {
        if (type === 'insight_fix') return 'Auto-Fix';
        if (type === 'suggestion') return 'Suggestion';
        return type.replace(/_/g, ' ').toUpperCase();
    }

    async function handleAction(id, action) {
        const btn = document.querySelector(`button[data-id="${id}"].btn-${action}`);
        if (btn) btn.disabled = true;

        try {
            const res = await fetch(`/api/approvals/${id}/${action}`, { method: 'POST' });
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
