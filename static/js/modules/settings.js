// static/js/modules/settings.js
import { showModal } from './ui.js';

let settingsDirty = false;
let loadedConfig = {};

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function setDirty(value) {
    settingsDirty = Boolean(value);
    const statusEl = document.getElementById('save-status');
    if (!statusEl) return;
    if (settingsDirty) {
        statusEl.textContent = 'Unsaved changes';
        statusEl.className = 'is-dirty';
        statusEl.style.color = '';
    } else if (statusEl.className === 'is-dirty') {
        statusEl.textContent = '';
        statusEl.className = '';
    }
}

function syncPrimaryControls(config) {
    const executionMode = document.getElementById('setting-execution-mode');
    const reasoning = document.getElementById('setting-reasoning');
    const thinking = document.getElementById('setting-thinking-enabled');

    if (executionMode) {
        const configuredMode = String(config.DEFAULT_EXECUTION_MODE || 'AUTO').toUpperCase();
        executionMode.value = Array.from(executionMode.options).some(option => option.value === configuredMode)
            ? configuredMode
            : 'AUTO';
    }
    if (reasoning) {
        const configuredStrategy = String(config.REASONING_STRATEGIES?.default || 'RAW').toUpperCase();
        reasoning.value = configuredStrategy === 'PARALLEL' ? 'PARALLEL' : 'RAW';
    }
    if (thinking) {
        const budget = Number(config.GEMINI_THINKING_BUDGET);
        thinking.checked = budget === -1 || budget > 0;
    }
}

export function hasUnsavedChanges() {
    return settingsDirty;
}

export function confirmDiscardChanges(onContinue) {
    if (!settingsDirty) {
        onContinue?.();
        return true;
    }

    showModal(
        'Unsaved Settings',
        'You have unsaved settings changes. Discard them and continue?',
        () => {
            setDirty(false);
            onContinue?.();
        },
        true,
        true,
        'Discard'
    );
    return false;
}

export function init() {
    // Bind Save Button
    const saveBtn = document.getElementById('save-settings-btn');
    if (saveBtn) {
        saveBtn.addEventListener('click', saveSettings);
    }

    ['setting-execution-mode', 'setting-reasoning', 'setting-thinking-enabled'].forEach(id => {
        const control = document.getElementById(id);
        if (control) {
            control.addEventListener('input', () => setDirty(true));
            control.addEventListener('change', () => setDirty(true));
        }
    });

    window.addEventListener('beforeunload', event => {
        if (!settingsDirty) return;
        event.preventDefault();
        event.returnValue = '';
    });
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
            loadedConfig = config || {};
            renderSettingsForm(config, container);
            syncPrimaryControls(config || {});
            setDirty(false);
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

    const descriptions = {
        DEFAULT_MODEL: 'Fallback model used when a task profile does not provide a more specific model.',
        ENABLE_THINKING: 'Allow the assistant to spend extra steps reasoning before it acts.',
        CONTEXT_COMPRESSION_ENABLED: 'Compress older conversation context when it approaches the configured limit.',
        CONTEXT_COMPRESSION_TRIGGER_TOKENS: 'Approximate context size that triggers compression.',
        DAILY_TOKEN_BUDGET: 'Maximum daily spend limit for model calls.',
        TASK_MODELS: 'Legacy task-to-model map. Prefer Task Profiles below for provider, model, and mode together.',
        TASK_PROFILES: 'Fine-tune the provider and execution mode for each class of work.',
        ENABLE_DREAM_MODE: 'Allow background reflection work to run on its configured schedule.'
    };

    // Sort keys mostly alphabetically, or define a specific order
    const hiddenKeys = new Set([
        'GHOST_MODE',
        'AUTO_WEB_PIP',
        // These are represented by the friendly controls above.
        'DEFAULT_EXECUTION_MODE',
        'REASONING_STRATEGIES',
        'GEMINI_THINKING_BUDGET'
    ]);
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
        group.dataset.settingKey = key;

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
                const knownProviders = ['gemini', 'deepseek', 'ollama', 'openai', 'anthropic'];
                const provider = String(profile.provider || '');
                const customProviderOption = provider && !knownProviders.includes(provider)
                    ? `<option value="${escapeHtml(provider)}" selected>${escapeHtml(provider)} (Custom)</option>`
                    : '';
                html += `
                    <div class="profile-card" data-task="${escapeHtml(taskName)}" style="background: rgba(0,0,0,0.2); padding: 10px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.05);">
                        <h4 style="margin: 0 0 10px 0; color: var(--accent-cyan); text-transform: uppercase;">Task: ${escapeHtml(taskName)}</h4>
                        <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 11px; color: var(--text-dim);">Provider</label>
                                <select class="profile-provider" aria-label="${escapeHtml(taskName)} provider" style="width: 100%; padding: 4px; background: rgba(0,0,0,0.3); color: white; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px;">
                                    <option value="gemini" ${profile.provider === 'gemini' ? 'selected' : ''}>Gemini</option>
                                    <option value="deepseek" ${profile.provider === 'deepseek' ? 'selected' : ''}>DeepSeek</option>
                                    <option value="ollama" ${profile.provider === 'ollama' ? 'selected' : ''}>Ollama</option>
                                    <option value="openai" ${profile.provider === 'openai' ? 'selected' : ''}>OpenAI</option>
                                    <option value="anthropic" ${profile.provider === 'anthropic' ? 'selected' : ''}>Anthropic</option>
                                    ${customProviderOption}
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 11px; color: var(--text-dim);">Model</label>
                                <select class="profile-model" aria-label="${escapeHtml(taskName)} model" style="width: 100%; padding: 4px; background: rgba(0,0,0,0.3); color: white; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px;">
                                    <option value="deepseek-v4-pro" ${profile.model === 'deepseek-v4-pro' ? 'selected' : ''}>Deepseek V4 Pro</option>
                                    <option value="deepseek-v4-flash" ${profile.model === 'deepseek-v4-flash' ? 'selected' : ''}>Deepseek V4 Flash</option>
                                    <option value="gemini-2.5-flash-lite" ${profile.model === 'gemini-2.5-flash-lite' ? 'selected' : ''}>Gemini 2.5 Flash-Lite</option>
                                    <option value="gemini-2.5-flash" ${profile.model === 'gemini-2.5-flash' ? 'selected' : ''}>Gemini 2.5 Flash</option>
                                    <option value="gemini-2.5-pro" ${profile.model === 'gemini-2.5-pro' ? 'selected' : ''}>Gemini 2.5 Pro</option>
                                    <option value="gpt-4o" ${profile.model === 'gpt-4o' ? 'selected' : ''}>GPT-4o</option>
                                    <option value="gpt-4o-mini" ${profile.model === 'gpt-4o-mini' ? 'selected' : ''}>GPT-4o Mini</option>
                                    <option value="claude-3-5-sonnet-latest" ${profile.model === 'claude-3-5-sonnet-latest' ? 'selected' : ''}>Claude 3.5 Sonnet</option>
                                    <option value="claude-3-haiku-20240307" ${profile.model === 'claude-3-haiku-20240307' ? 'selected' : ''}>Claude 3 Haiku</option>
                                    ${!['deepseek-v4-pro','deepseek-v4-flash','gemini-2.5-flash-lite','gemini-2.5-flash','gemini-2.5-pro','gpt-4o','gpt-4o-mini','claude-3-5-sonnet-latest','claude-3-haiku-20240307'].includes(profile.model) ? '<option value="' + escapeHtml(profile.model) + '" selected>' + escapeHtml(profile.model) + ' (Custom)</option>' : ''}
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 11px; color: var(--text-dim);">Mode</label>
                                <select class="profile-mode" aria-label="${escapeHtml(taskName)} execution mode" style="width: 100%; padding: 4px; background: rgba(0,0,0,0.3); color: white; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px;">
                                    <option value="BICAMERAL" ${profile.mode === 'BICAMERAL' ? 'selected' : ''}>Bicameral (Think -> Act)</option>
                                    <option value="DIRECT" ${profile.mode === 'DIRECT' ? 'selected' : ''}>Direct (Act)</option>
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 11px; color: var(--text-dim);">Endpoint</label>
                                <input type="text" class="profile-endpoint" aria-label="${escapeHtml(taskName)} endpoint" value="${escapeHtml(profile.endpoint || '')}" placeholder="http://..." style="width: 100%; padding: 4px; background: rgba(0,0,0,0.3); color: white; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px;">
                            </div>
                        </div>
                    </div>
                `;
            }
            html += '</div>';
            input.innerHTML = html;
        } else if (typeof value === 'boolean') {
            input = document.createElement('select');
            input.dataset.valueType = 'boolean';
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
            if (key === 'DEFAULT_MODEL') {
                input = document.createElement('select');
                const models = [
                    {val: 'deepseek-v4-pro', label: 'Deepseek V4 Pro'},
                    {val: 'deepseek-v4-flash', label: 'Deepseek V4 Flash'},
                    {val: 'gemini-2.5-flash-lite', label: 'Gemini 2.5 Flash-Lite'},
                    {val: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash'},
                    {val: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro'},
                    {val: 'gpt-4o', label: 'GPT-4o'},
                    {val: 'gpt-4o-mini', label: 'GPT-4o Mini'},
                    {val: 'claude-3-5-sonnet-latest', label: 'Claude 3.5 Sonnet'},
                    {val: 'claude-3-haiku-20240307', label: 'Claude 3 Haiku'}
                ];
                let found = false;
                models.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m.val;
                    opt.textContent = m.label;
                    if (value === m.val) {
                        opt.selected = true;
                        found = true;
                    }
                    input.appendChild(opt);
                });
                if (!found && value) {
                    const opt = document.createElement('option');
                    opt.value = value;
                    opt.textContent = value + ' (Custom)';
                    opt.selected = true;
                    input.appendChild(opt);
                }
            } else {
                input = document.createElement('input');
                input.type = 'text';
                input.value = value;
            }
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

        if (input.id) label.htmlFor = input.id;
        const description = descriptions[key];
        if (description) {
            const help = document.createElement('p');
            help.className = 'setting-desc';
            help.textContent = description;
            group.appendChild(help);
        }

        group.appendChild(input);
        container.appendChild(group);
    });

    if (!container.dataset.dirtyBound) {
        container.addEventListener('input', () => setDirty(true));
        container.addEventListener('change', () => setDirty(true));
        container.dataset.dirtyBound = 'true';
    }
}

export function saveSettings() {
    const container = document.getElementById('settings-container');
    const statusEl = document.getElementById('save-status');
    const inputs = container.querySelectorAll('input, select, textarea');
    const updates = {};

    let parseError = false;
    inputs.forEach(input => {
        const key = input.dataset.key;
        if (!key) return;

        let value = input.value;

        if (input.dataset.valueType === 'boolean') {
            value = (value === 'true');
        } else if (input.type === 'number') {
            value = Number(value);
        } else if (input.dataset.type === 'json') {
            try {
                value = JSON.parse(value);
            } catch (e) {
                console.error(`Invalid JSON for ${key}`);
                alert(`Invalid JSON for ${key}`);
                parseError = true;
                return;
            }
        }

        updates[key] = value;
    });

    if (parseError) return;

    const executionMode = document.getElementById('setting-execution-mode');
    const reasoning = document.getElementById('setting-reasoning');
    const thinking = document.getElementById('setting-thinking-enabled');
    if (executionMode) updates.DEFAULT_EXECUTION_MODE = executionMode.value;
    if (reasoning) {
        const strategies = (loadedConfig.REASONING_STRATEGIES && typeof loadedConfig.REASONING_STRATEGIES === 'object')
            ? { ...loadedConfig.REASONING_STRATEGIES }
            : {};
        strategies.default = reasoning.value;
        updates.REASONING_STRATEGIES = strategies;
    }
    if (thinking) {
        const currentBudget = Number(loadedConfig.GEMINI_THINKING_BUDGET);
        updates.GEMINI_THINKING_BUDGET = thinking.checked
            ? (currentBudget === -1 || currentBudget > 0 ? currentBudget : 24576)
            : 0;
    }

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
                    statusEl.className = 'is-saved';
                    statusEl.style.color = "#4cd137";
                    setTimeout(() => statusEl.textContent = '', 3000);
                }
                loadedConfig = { ...loadedConfig, ...updates };
                setDirty(false);
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
