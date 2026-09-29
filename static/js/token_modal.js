// token_modal.js
// Handles Token & Budget Center modal logic

export function initTokenModal() {
    const modal = document.getElementById('token-modal');
    if (!modal) return;

    const closeBtn = document.getElementById('token-modal-close');
    closeBtn.addEventListener('click', () => {
        modal.classList.remove('active');
    });

    const saveBudgetBtn = document.getElementById('save-budget-btn');
    const budgetInput = document.getElementById('budget-input');
    const saveStatus = document.getElementById('budget-save-status');
    const budgetDisplay = document.getElementById('token-daily-budget');

    // Fetch initial config for budget
    fetch('/api/config')
        .then(r => r.json())
        .then(config => {
            if (config.DAILY_TOKEN_BUDGET !== undefined) {
                budgetInput.value = config.DAILY_TOKEN_BUDGET;
                budgetDisplay.textContent = `$${parseFloat(config.DAILY_TOKEN_BUDGET).toFixed(2)}`;
            }
        }).catch(err => console.error("Failed to load budget:", err));

    saveBudgetBtn.addEventListener('click', async () => {
        const val = parseFloat(budgetInput.value);
        if (isNaN(val) || val < 0) return;

        saveBudgetBtn.disabled = true;
        saveBudgetBtn.textContent = 'Saving...';

        try {
            const res = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 'DAILY_TOKEN_BUDGET': val })
            });

            if (res.ok) {
                saveStatus.textContent = 'Saved!';
                saveStatus.style.color = 'var(--success-color)';
                budgetDisplay.textContent = `$${val.toFixed(2)}`;
                setTimeout(() => { saveStatus.textContent = ''; }, 2000);
            } else {
                saveStatus.textContent = 'Error saving.';
                saveStatus.style.color = 'var(--error-color)';
            }
        } catch (err) {
            saveStatus.textContent = 'Error saving.';
            saveStatus.style.color = 'var(--error-color)';
        } finally {
            saveBudgetBtn.disabled = false;
            saveBudgetBtn.textContent = 'Save';
        }
    });

    // Make available globally for the top-right button
    window.openTokenModal = async () => {
        modal.classList.add('active');
        await renderTokenBreakdown();
    };

    // Close on click outside
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.classList.remove('active');
        }
    });
}

async function renderTokenBreakdown() {
    const totalSpentEl = document.getElementById('token-total-spent');
    const container = document.getElementById('token-bars-container');
    const escapeHtml = (value) => String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    const numberOrZero = (value) => {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? numeric : 0;
    };

    try {
        const res = await fetch('/api/telemetry');
        const usage = await res.json();
        const estimatedCost = numberOrZero(usage && usage.estimated_cost);
        const modelUsage = usage && usage.model_usage && typeof usage.model_usage === 'object'
            ? usage.model_usage
            : {};

        totalSpentEl.textContent = `$${estimatedCost.toFixed(4)}`;

        if (Object.keys(modelUsage).length === 0) {
            container.innerHTML = '<div style="color: var(--text-secondary); text-align: center;">No token usage recorded today.</div>';
            return;
        }

        // Group by components (Approximated based on model names or tasks if available in telemetry)
        // Currently, standard telemetry only tracks model_usage dict: { "gemini-2.0-flash": { input: X, output: Y } }
        // For a true breakdown by *component* (Dreamer, Chat), we would need task-based telemetry.
        // Assuming we display what we have:

        let html = '';
        const overallTokens = Math.max(1, numberOrZero(usage.total_tokens));
        for (const [model, rawStats] of Object.entries(modelUsage)) {
            const stats = rawStats && typeof rawStats === 'object' ? rawStats : {};
            const inputTokens = numberOrZero(stats.input_tokens);
            const outputTokens = numberOrZero(stats.output_tokens);
            // Rough estimate of cost per model based on tokens (hardcoded approx for visualization)
            // Just visualize token percentage for now
            const thinkingTokens = numberOrZero(stats.thinking_tokens);
            const totalTokens = numberOrZero(stats.total_tokens) || (inputTokens + outputTokens + thinkingTokens);
            const pct = Math.min(100, (totalTokens / overallTokens) * 100).toFixed(1);
            const budgetText = stats.latest_thinking_budget === null || stats.latest_thinking_budget === undefined
                ? 'Budget: n/a'
                : `Budget: ${numberOrZero(stats.latest_thinking_budget).toLocaleString()}`;

            html += `
                <div style="margin-bottom: 15px;">
                    <div style="display: flex; justify-content: space-between; font-size: 0.9em; margin-bottom: 5px;">
                        <span>${escapeHtml(model)}</span>
                        <span>${pct}% (${totalTokens.toLocaleString()} tokens)</span>
                    </div>
                    <div style="width: 100%; background: rgba(255,255,255,0.05); border-radius: 4px; height: 8px; overflow: hidden; border: 1px solid rgba(255,255,255,0.02);">
                        <div style="height: 100%; width: ${pct}%; background: linear-gradient(90deg, var(--accent-cyan), var(--accent-purple)); border-radius: 4px; box-shadow: 0 0 10px rgba(0, 240, 255, 0.4);"></div>
                    </div>
                    <div style="font-size: 0.8em; color: var(--text-secondary); margin-top: 2px;">
                        In: ${inputTokens.toLocaleString()} | Out: ${outputTokens.toLocaleString()} | Thinking: ${thinkingTokens.toLocaleString()} | ${escapeHtml(budgetText)}
                    </div>
                </div>
            `;
        }

        container.innerHTML = html;

    } catch (e) {
        console.error(e);
        container.innerHTML = '<div style="color: var(--error-color);">Failed to load telemetry.</div>';
    }
}
