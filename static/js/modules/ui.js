
// static/js/modules/ui.js

export function escapeHtml(text) {
    if (!text) return text;
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

export function showTypingIndicator(container, text = "Thinking...") {
    if (!container) return;
    const existing = document.getElementById('typing-indicator');
    if (existing) {
        const label = existing.querySelector('.typing-label');
        if (label) label.textContent = text;
        return;
    }

    const indicator = document.createElement('div');
    indicator.id = 'typing-indicator';
    indicator.className = 'typing-indicator';
    indicator.innerHTML = `
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
        <span class="typing-label" style="margin-left: 10px; font-size: 12px; color: var(--text-secondary);">${text}</span>
    `;
    container.appendChild(indicator);
    container.scrollTop = container.scrollHeight;
}

export function removeTypingIndicator() {
    const indicator = document.getElementById('typing-indicator');
    if (indicator) indicator.remove();
}

// Modal Logic
export function showModal(title, message, onConfirm, isDestructive = false, showCancel = true, confirmText = null) {
    const modal = document.getElementById('custom-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalMessage = document.getElementById('modal-message');
    const confirmBtn = document.getElementById('modal-confirm-btn');
    const cancelBtn = document.getElementById('modal-cancel-btn');

    if (!modal) return;

    modalTitle.textContent = title;
    modalMessage.textContent = message;

    if (isDestructive) {
        confirmBtn.className = 'btn-modal danger';
        confirmBtn.textContent = confirmText || 'Delete';
    } else {
        confirmBtn.className = 'btn-modal confirm';
        confirmBtn.textContent = confirmText || 'OK';
    }

    if (!showCancel) {
        cancelBtn.style.display = 'none';
        confirmBtn.style.width = '100%';
    } else {
        cancelBtn.style.display = 'block';
        confirmBtn.style.width = 'auto';
    }

    const close = () => {
        modal.classList.remove('active');
        confirmBtn.onclick = null;
        cancelBtn.onclick = null;
    };

    confirmBtn.onclick = () => {
        if (onConfirm) onConfirm();
        close();
    };

    cancelBtn.onclick = close;
    modal.classList.add('active');
}

export function showAlert(title, message) {
    showModal(title, message, null, false, false);
}

export function confirmShutdown() {
    showModal(
        "System Shutdown",
        "Are you sure you want to shut down the AI Assistant? The server will stop immediately.",
        async () => {
            showAlert("System", "Shutting down services...");
            try {
                const res = await fetch('/api/system/shutdown', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    document.body.innerHTML = '<div style="display:flex;justify-content:center;align-items:center;height:100vh;background:#000;color:#fa5252;font-family:monospace;font-size:2em;flex-direction:column;"><div>SYSTEM OFFLINE</div><div style="font-size:0.5em;color:#666;margin-top:20px;">Connection Terminated</div></div>';
                }
            } catch (e) {
                console.error("Shutdown failed:", e);
                showAlert("Error", "Shutdown signal failed to transmit.");
            }
        },
        true, // Destructive
        true, // Show Cancel
        "Shutdown"
    );
}
