document.addEventListener('DOMContentLoaded', () => {
    const quarantineList = document.getElementById('quarantine-list');
    const refreshBtn = document.getElementById('refresh-quarantine-btn');

    // Poll every 10 seconds
    setInterval(fetchQuarantine, 10000);
    // Initial fetch
    setTimeout(fetchQuarantine, 1000);

    if (refreshBtn) {
        refreshBtn.addEventListener('click', fetchQuarantine);
    }

    if (window.socket) {
        window.socket.on('quarantine_update', function(data) {
            if (data && data.blocked_tools) {
                renderQuarantine(data.blocked_tools);
            } else {
                fetchQuarantine();
            }
        });
    }

    async function fetchQuarantine() {
        if (!quarantineList) return;
        try {
            const res = await fetch('/api/system/quarantine');
            const data = await res.json();
            if (data.success) {
                renderQuarantine(data.blocked_tools);
            }
        } catch (e) {
            console.error("Failed to fetch quarantine status", e);
        }
    }

    function renderQuarantine(blockedTools) {
        if (!quarantineList) return;

        const tools = Object.keys(blockedTools);

        if (tools.length === 0) {
            quarantineList.innerHTML = '<div class="empty-state" style="opacity:0.5; text-align:center; margin-top:20px;">No quarantined tools.</div>';
            return;
        }

        quarantineList.innerHTML = '';
        tools.forEach(toolName => {
            const info = blockedTools[toolName];
            const card = document.createElement('div');
            card.className = 'approval-card glass-panel-light'; // Reuse approval card styling

            card.innerHTML = `
                <div class="approval-header">
                    <span class="approval-type type-quarantine" style="color: #ef4444; border-color: #ef4444;">QUARANTINED</span>
                    <span class="approval-time">${info.timestamp ? new Date(info.timestamp * 1000).toLocaleTimeString() : ''}</span>
                </div>
                <div class="approval-body">
                    <div class="target-name">Tool: <code>${toolName}</code></div>
                    <div class="target-name" style="margin-top: 5px;">Failures: <strong>${info.count}</strong></div>
                    <p class="approval-desc" style="margin-top: 10px; word-break: break-all;">${info.reason || 'Repeated failures'}</p>
                </div>
                <div class="approval-actions">
                    <button class="btn-approve" data-tool="${toolName}" style="width: 100%; border-color: #10b981; color: #10b981;">Unblock</button>
                </div>
            `;

            // Listeners
            card.querySelector('.btn-approve').addEventListener('click', () => {
                unblockTool(toolName);
            });

            quarantineList.appendChild(card);
        });
    }

    async function unblockTool(toolName) {
        const btn = document.querySelector(`button[data-tool="${toolName}"]`);
        if (btn) btn.disabled = true;

        try {
            const res = await fetch('/api/system/quarantine/unblock', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tool_name: toolName })
            });
            const data = await res.json();
            if (data.success) {
                fetchQuarantine();
            } else {
                console.error("Failed to unblock tool: " + (data.error || "Unknown error"));
                if (window.showAlert) {
                    window.showAlert("Error", "Action failed: " + (data.error || "Unknown error"));
                }
                if (btn) btn.disabled = false;
            }
        } catch (e) {
            console.error(e);
            if (btn) btn.disabled = false;
            if (window.showAlert) {
                window.showAlert("Error", "An unexpected error occurred.");
            }
        }
    }
});
