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

    fetch('/api/config')
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

    // Sort keys mostly alphabetically, or define a specific order
    const priorityKeys = ['DEFAULT_MODEL', 'LLM_PROVIDER', 'ENABLE_THINKING', 'SAFE_MODE', 'GHOST_MODE'];
    const keys = Object.keys(config).sort((a, b) => {
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

        if (typeof value === 'boolean') {
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
            input.value = value;
        } else if (typeof value === 'object') {
            // Arrays or Objects -> TextArea JSON
            input = document.createElement('textarea');
            input.value = JSON.stringify(value, null, 2);
            input.dataset.type = 'json';
        }

        input.dataset.key = key;
        input.id = `setting-${key}`;

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
