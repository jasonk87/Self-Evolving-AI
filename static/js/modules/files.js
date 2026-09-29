
// static/js/modules/files.js
import { escapeHtml, showAlert } from './ui.js';

let currentProject = null;
let currentFilePath = null;
let editorObj = null;
let suppressEditorChange = false;
let editorDirty = false;
const RUNNABLE_EXTENSIONS = new Set(['.py']);

export function isRunnableFile(path = currentFilePath) {
    const extension = String(path || '').toLowerCase().match(/\.[^.\\/]+$/)?.[0] || '';
    return RUNNABLE_EXTENSIONS.has(extension);
}

function updateRunControl(path) {
    const runButton = document.getElementById('run-file-btn');
    if (!runButton) return;
    const runnable = isRunnableFile(path);
    runButton.disabled = !runnable;
    runButton.title = runnable ? 'Run Python file' : 'Run is available for Python files only';
    runButton.setAttribute('aria-label', runButton.title);
}

function setEditorDirty(value) {
    editorDirty = Boolean(value);
    const status = document.getElementById('editor-dirty-status');
    if (status) status.textContent = editorDirty ? 'Unsaved changes' : '';
    const saveButton = document.getElementById('save-file-btn');
    if (saveButton) saveButton.classList.toggle('is-dirty', editorDirty);
}

export function setEditor(cmInstance) {
    editorObj = cmInstance;
    editorObj?.on('change', () => {
        if (!suppressEditorChange) setEditorDirty(true);
    });
}

export function getCurrentProject() { return currentProject; }
export function getCurrentFilePath() { return currentFilePath; }

export async function loadProjects(container, onFileOpenCallback) {
    if (!container) return;
    container.innerHTML = '<div class="loading">Loading...</div>';
    try {
        const res = await fetch('/api/projects');
        const data = await res.json();
        if (data.success && data.projects) {
            container.innerHTML = '';
            data.projects.forEach(p => {
                const root = document.createElement('div');
                root.className = 'tree-item project-root';
                root.innerHTML = `<span class="icon">🚀</span> ${escapeHtml(p.name)}`;
                container.appendChild(root);

                const childContainer = document.createElement('div');
                childContainer.style.paddingLeft = '15px';
                childContainer.style.display = 'none';
                root.addEventListener('click', () => {
                    childContainer.style.display = childContainer.style.display === 'none' ? 'block' : 'none';
                    if (childContainer.children.length === 0) loadFiles(p.name, '', childContainer, onFileOpenCallback);
                });
                container.appendChild(childContainer);
            });
        }
    } catch (e) { container.innerHTML = 'Error loading projects.'; }
}

async function loadFiles(projName, path, container, onFileOpenCallback) {
    const res = await fetch(`/api/files/list?project_name=${encodeURIComponent(projName)}&path=${encodeURIComponent(path)}`);
    const data = await res.json();
    if (data.success) {
        container.innerHTML = '';
        data.directories.forEach(d => {
            const el = document.createElement('div');
            el.className = 'tree-item folder';
            el.innerHTML = `<span class="icon">📂</span> ${escapeHtml(d)}`;
            container.appendChild(el);
            const sub = document.createElement('div');
            sub.style.paddingLeft = '15px'; sub.style.display = 'none';
            container.appendChild(sub);
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                sub.style.display = sub.style.display === 'none' ? 'block' : 'none';
                if (sub.children.length === 0) loadFiles(projName, path ? path + '/' + d : d, sub, onFileOpenCallback);
            });
        });
        data.files.forEach(f => {
            const el = document.createElement('div');
            el.className = 'tree-item file';
            el.innerHTML = `<span class="icon">📄</span> ${escapeHtml(f)}`;
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                document.querySelectorAll('.tree-item').forEach(i => i.classList.remove('active'));
                el.classList.add('active');
                openFile(projName, path ? path + '/' + f : f, onFileOpenCallback);
            });
            container.appendChild(el);
        });
    }
}

export async function openFile(projName, path, callback) {
    const res = await fetch(`/api/files/read?project_name=${encodeURIComponent(projName)}&path=${encodeURIComponent(path)}`);
    const data = await res.json();
    if (data.success) {
        currentProject = projName;
        currentFilePath = path;
        updateRunControl(path);
        document.getElementById('current-file-name').textContent = path;
        if (editorObj) {
            suppressEditorChange = true;
            editorObj.setValue(data.content);
            suppressEditorChange = false;
        }
        setEditorDirty(false);
        if (callback) callback();
    }
}

export async function saveFile() {
    if (!currentProject || !currentFilePath || !editorObj) return showAlert("Info", "No file open.");
    const content = editorObj.getValue();
    try {
        const res = await fetch('/api/files/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_name: currentProject, path: currentFilePath, content })
        });
        const data = await res.json();
        if (data.success) {
            setEditorDirty(false);
            showAlert("Success", "File saved successfully.");
        } else {
            showAlert("Error", "Save failed: " + data.error);
        }
    } catch (e) { showAlert("Error", "Error saving file."); }
}

export async function runFile(termOutputDiv, switchToTerminalCallback) {
    if (!currentFilePath || !currentProject) return showAlert("Info", "No file open.");
    if (!isRunnableFile()) {
        showAlert("Unsupported file", "The editor currently runs Python files only. Open this file for editing or preview it instead.");
        return null;
    }

    if (switchToTerminalCallback) switchToTerminalCallback();

    if (termOutputDiv) termOutputDiv.innerHTML += `<div class="line command">$ python ${escapeHtml(currentFilePath)}</div>`;

    const fullPath = `projects/${currentProject}/${currentFilePath}`;

    try {
        const res = await fetch('/api/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: fullPath })
        });
        const data = await res.json();

        let outputText = data.success ? data.output : (data.error || data.output);

        // Return output for passing to AI logic if needed
        if (termOutputDiv) {
            const formattedOutput = escapeHtml(outputText).replace(/\n/g, '<br>');
            termOutputDiv.innerHTML += `<div class="line output">${formattedOutput}</div>`;
            termOutputDiv.scrollTop = termOutputDiv.scrollHeight;
        }

        return outputText;

    } catch (e) {
        if (termOutputDiv) termOutputDiv.innerHTML += `<div class="line error">Execution failed.</div>`;
        return "Execution Failed";
    }
}
