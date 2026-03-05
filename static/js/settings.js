document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('settings-modal');
    const closeBtn = document.getElementById('close-settings-btn');
    const saveBtn = document.getElementById('save-settings-btn');
    const container = document.getElementById('task-profiles-container');
    const toggleBtn = document.getElementById('toggle-settings-btn'); // Assuming there's a button to open it

    if (toggleBtn) {
        toggleBtn.addEventListener('click', openSettings);
    }

    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            modal.style.display = 'none';
        });
    }

    async function openSettings() {
        if (!modal || !container) return;
        modal.style.display = 'flex';
        container.innerHTML = '<div class="loading">Loading configuration...</div>';

        try {
            const res = await fetch('/api/config');
            const config = await res.json();

            const profiles = config.TASK_PROFILES || {};
            let html = '';

            for (const [taskName, profile] of Object.entries(profiles)) {
                html += `
                    <div class="profile-card" style="background: rgba(0,0,0,0.2); padding: 10px; margin-bottom: 10px; border-radius: 4px;">
                        <h4 style="margin: 0 0 10px 0; color: var(--accent-blue); text-transform: uppercase;">Task: ${taskName}</h4>
                        <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 11px; color: var(--text-dim);">Provider</label>
                                <select class="profile-provider" data-task="${taskName}" style="width: 100%; padding: 4px; background: #1a1a1a; color: white; border: 1px solid #333;">
                                    <option value="gemini" ${profile.provider === 'gemini' ? 'selected' : ''}>Gemini</option>
                                    <option value="ollama" ${profile.provider === 'ollama' ? 'selected' : ''}>Ollama</option>
                                    <option value="openai" ${profile.provider === 'openai' ? 'selected' : ''}>OpenAI</option>
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 11px; color: var(--text-dim);">Model</label>
                                <input type="text" class="profile-model" data-task="${taskName}" value="${profile.model || ''}" style="width: 100%; padding: 4px; background: #1a1a1a; color: white; border: 1px solid #333;">
                            </div>
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 11px; color: var(--text-dim);">Mode</label>
                                <select class="profile-mode" data-task="${taskName}" style="width: 100%; padding: 4px; background: #1a1a1a; color: white; border: 1px solid #333;">
                                    <option value="BICAMERAL" ${profile.mode === 'BICAMERAL' ? 'selected' : ''}>Bicameral (Think -> Act)</option>
                                    <option value="DIRECT" ${profile.mode === 'DIRECT' ? 'selected' : ''}>Direct (Act)</option>
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 11px; color: var(--text-dim);">Endpoint (Optional)</label>
                                <input type="text" class="profile-endpoint" data-task="${taskName}" value="${profile.endpoint || ''}" placeholder="http://..." style="width: 100%; padding: 4px; background: #1a1a1a; color: white; border: 1px solid #333;">
                            </div>
                        </div>
                    </div>
                `;
            }
            container.innerHTML = html;
        } catch (e) {
            container.innerHTML = `<div class="error" style="color: red;">Failed to load config: ${e}</div>`;
        }
    }

    if (saveBtn) {
        saveBtn.addEventListener('click', async () => {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';

            const updatedProfiles = {};
            const providers = document.querySelectorAll('.profile-provider');

            providers.forEach(prov => {
                const task = prov.dataset.task;
                const model = document.querySelector(`.profile-model[data-task="${task}"]`).value;
                const mode = document.querySelector(`.profile-mode[data-task="${task}"]`).value;
                const endpoint = document.querySelector(`.profile-endpoint[data-task="${task}"]`).value;

                updatedProfiles[task] = {
                    provider: prov.value,
                    model: model,
                    mode: mode,
                    endpoint: endpoint || null
                };
            });

            try {
                const res = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ TASK_PROFILES: updatedProfiles })
                });

                const data = await res.json();
                if (data.success) {
                    saveBtn.textContent = 'Saved!';
                    setTimeout(() => {
                        saveBtn.textContent = 'Save Configuration';
                        saveBtn.disabled = false;
                        modal.style.display = 'none';
                    }, 1000);
                } else {
                    throw new Error(data.errors ? data.errors.join(', ') : 'Unknown error');
                }
            } catch (e) {
                alert(`Save failed: ${e.message}`);
                saveBtn.textContent = 'Save Configuration';
                saveBtn.disabled = false;
            }
        });
    }
});
