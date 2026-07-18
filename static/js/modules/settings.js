// static/js/modules/settings.js

export function init() {
    // Bind Save Button
    const saveBtn = document.getElementById('save-settings-btn');
    if (saveBtn) {
        saveBtn.addEventListener('click', saveSettings);
    }
}

export function loadConfig() {
    const container = document.getElementById('settings-container');
    const loading = document.getElementById('settings-loading');
    const footer = document.getElementById('settings-actions-footer');

    if (!container) return;

    if (loading) loading.style.display = 'block';

    fetch('/api/config?t=' + Date.now())
        .then(response => response.json())
        .then(config => {
            renderSettingsForm(config, container);
            if (loading) loading.style.display = 'none';
            if (container) container.classList.remove('hidden');
            if (footer) footer.classList.remove('hidden');
        })
        .catch(err => {
            console.error("Error loading config:", err);
            if (loading) loading.textContent = "Error loading configuration.";
        });
}

function renderSettingsForm(config, container) {
    container.innerHTML = '';

    if (!document.getElementById('available-models')) {
        const datalist = document.createElement('datalist');
        datalist.id = 'available-models';
        datalist.innerHTML = `
            <option value="deepseek-v4-pro">Deepseek V4 Pro</option>
            <option value="deepseek-v4-flash">Deepseek V4 Flash</option>
            <option value="gemini-2.5-flash-lite">Gemini 2.5 Flash-Lite</option>
            <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
            <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
            <option value="gpt-4o">GPT-4o</option>
            <option value="gpt-4o-mini">GPT-4o Mini</option>
            <option value="claude-3-5-sonnet-latest">Claude 3.5 Sonnet</option>
            <option value="claude-3-haiku-20240307">Claude 3 Haiku</option>
        `;
        document.body.appendChild(datalist);
    }

    // Sort keys mostly alphabetically, or define a specific order
    const hiddenKeys = new Set(['GHOST_MODE', 'AUTO_WEB_PIP']);
    const priorityKeys = ['DEFAULT_MODEL', 'LLM_PROVIDER', 'ENABLE_THINKING', 'SAFE_MODE'];
    const keys = Object.keys(config).filter(key => !hiddenKeys.has(key)).sort((a, b) => {
        const aIdx = priorityKeys.indexOf(a);
        const bIdx = priorityKeys.indexOf(b);
        if (aIdx !== -1 && bIdx !== -1) return aIdx - bIdx;
        if (aIdx !== -1) return -1;
        if (bIdx !== -1) return 1;
        return a.localeCompare(b);
    });

    keys.forEach(key => {
        const value = config[key];
        const group = document.createElement('div');
        group.className = 'setting-group';

        const label = document.createElement('label');
        label.textContent = key.replace(/_/g, ' ');
        group.appendChild(label);

        let input;

        if (key === 'TASK_PROFILES') {
            input = document.createElement('div');
            input.dataset.key = key;
            input.dataset.type = 'custom-task-profiles';
            input.id = `setting-${key}`;

            let html = '<div style="display:flex; flex-direction:column; gap:10px;">';
            for (const [taskName, profile] of Object.entries(value || {})) {
                html += `
                    <div class="profile-card" data-task="${taskName}" style="background: rgba(0,0,0,0.2); padding: 10px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.05);">
                        <h4 style="margin: 0 0 10px 0; color: var(--accent-cyan); text-transform: uppercase;">Task: ${taskName}</h4>
                        <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 11px; color: var(--text-dim);">Provider</label>
                                <select class="profile-provider" style="width: 100%; padding: 4px; background: rgba(0,0,0,0.3); color: white; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px;">
                                    <option value="gemini" ${profile.provider === 'gemini' ? 'selected' : ''}>Gemini</option>
                                    <option value="ollama" ${profile.provider === 'ollama' ? 'selected' : ''}>Ollama</option>
                                    <option value="openai" ${profile.provider === 'openai' ? 'selected' : ''}>OpenAI</option>
                                    <option value="anthropic" ${profile.provider === 'anthropic' ? 'selected' : ''}>Anthropic</option>
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 11px; color: var(--text-dim);">Model</label>
                                <input type="text" list="available-models" class="profile-model" value="${profile.model || ''}" style="width: 100%; padding: 4px; background: rgba(0,0,0,0.3); color: white; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px;">
                            </div>
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 11px; color: var(--text-dim);">Mode</label>
                                <select class="profile-mode" style="width: 100%; padding: 4px; background: rgba(0,0,0,0.3); color: white; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px;">
                                    <option value="BICAMERAL" ${profile.mode === 'BICAMERAL' ? 'selected' : ''}>Bicameral (Think -> Act)</option>
                                    <option value="DIRECT" ${profile.mode === 'DIRECT' ? 'selected' : ''}>Direct (Act)</option>
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 11px; color: var(--text-dim);">Endpoint</label>
                                <input type="text" class="profile-endpoint" value="${profile.endpoint || ''}" placeholder="http://..." style="width: 100%; padding: 4px; background: rgba(0,0,0,0.3); color: white; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px;">
                            </div>
                        </div>
                    </div>
                `;
            }
            html += '</div>';
            input.innerHTML = html;
        } else if (typeof value === 'boolean') {
            input = document.createElement('select');
            input.innerHTML = `
                <option value="true" ${value === true ? 'selected' : ''}>Enabled</option>
                <option value="false" ${value === false ? 'selected' : ''}>Disabled</option>
            `;
            // Or use a nice toggle switch UI later
        } else if (typeof value === 'number') {
            input = document.createElement('input');
            input.type = 'number';
            input.value = value;
        } else if (typeof value === 'string') {
            input = document.createElement('input');
            input.type = 'text';
            if (key === 'DEFAULT_MODEL') {
                input.setAttribute('list', 'available-models');
            }
            input.value = value;
        } else if (typeof value === 'object') {
            // Arrays or Objects -> TextArea JSON
            input = document.createElement('textarea');
            input.value = JSON.stringify(value, null, 2);
            input.dataset.type = 'json';
        }

        if (key !== 'TASK_PROFILES') {
            input.dataset.key = key;
            input.id = `setting-${key}`;
        }

        group.appendChild(input);
        container.appendChild(group);
    });
}

export function saveSettings() {
    const container = document.getElementById('settings-container');
    const statusEl = document.getElementById('save-status');
    const inputs = container.querySelectorAll('input, select, textarea');
    const updates = {};

    inputs.forEach(input => {
        const key = input.dataset.key;
        if (!key) return;

        let value = input.value;

        if (input.tagName === 'SELECT') {
            value = (value === 'true');
        } else if (input.type === 'number') {
            value = Number(value);
        } else if (input.dataset.type === 'json') {
            try {
                value = JSON.parse(value);
            } catch (e) {
                console.error(`Invalid JSON for ${key}`);
                alert(`Invalid JSON for ${key}`);
                return;
            }
        }

        updates[key] = value;
    });

    const taskProfilesContainer = container.querySelector('[data-type="custom-task-profiles"]');
    if (taskProfilesContainer) {
        const profiles = {};
        const cards = taskProfilesContainer.querySelectorAll('.profile-card');
        cards.forEach(card => {
            const task = card.dataset.task;
            const provider = card.querySelector('.profile-provider').value;
            const model = card.querySelector('.profile-model').value;
            const mode = card.querySelector('.profile-mode').value;
            const endpoint = card.querySelector('.profile-endpoint').value;
            profiles[task] = {
                provider: provider,
                model: model,
                mode: mode,
                endpoint: endpoint || null
            };
        });
        updates['TASK_PROFILES'] = profiles;
    }

    fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates)
    })
        .then(res => res.json())
        .then(result => {
            if (result.success) {
                if (statusEl) {
                    statusEl.textContent = "Saved!";
                    statusEl.style.color = "#4cd137";
                    setTimeout(() => statusEl.textContent = '', 3000);
                }
            } else {
                alert("Save Failed: " + result.errors.join(", "));
            }
        })
        .catch(err => {
            console.error(err);
            alert("Error saving settings.");
        });
}

export function shutdownSystem() {
    // Delegate to the shared UI module's modal implementation
    const UI = window.UI || null; // Fallback if not globally exposed, but it should be via main.js
    // Actually, distinct modules might not see globals easily if not explicitly imported.
    // However, main.js assigns window.confirmShutdown = UI.confirmShutdown
    if (window.confirmShutdown) {
        window.confirmShutdown();
    } else {
        // Fallback or explicit import if window binding is unreliable
        console.error("UI.confirmShutdown not found on window object. Check main.js initialization.");
        alert("System Shutdown: Please use the toolbar button.");
    }
}
