// Main JS - Unified Left Sidebar + Council/Memory Implementation

document.addEventListener('DOMContentLoaded', () => {

    // --- Elements ---
    const activityItems = document.querySelectorAll('.activity-item');
    const sidebarViews = document.querySelectorAll('.sidebar-view');
    const mainViews = document.querySelectorAll('.view-container');
    const sidebarPanel = document.getElementById('sidebar-panel');
    let notificationPermission = 'default';

    // Request Notification Permission
    if ('Notification' in window) {
        Notification.requestPermission().then(p => notificationPermission = p);
    }

    // Components
    const councilContainer = document.getElementById('council-logs');
    const memoryList = document.getElementById('memory-list');
    const chatContainer = document.getElementById('chat-container');
    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const fileTreeContainer = document.getElementById('file-tree');
    const editorContainer = document.getElementById('editor-container');

    // Editor State
    let currentProject = null;
    let currentFilePath = null;
    let editor = null;
    let lastRunOutput = ""; // Store last visualization/run output for AI context


    // --- Initialization ---

    // CodeMirror
    if (editorContainer) {
        editor = CodeMirror(editorContainer, {
            mode: "python", theme: "dracula",
            lineNumbers: true, indentUnit: 4, matchBrackets: true
        });
        new ResizeObserver(() => editor.refresh()).observe(editorContainer);
    }

    // Socket.IO
    const socket = io();

    socket.on('response', (data) => {
        appendMessage('assistant', data.response);
        notifyIfHidden("AI Assistant", data.response);
    });

    socket.on('chat_response', (data) => {
        if (data.success) {
            appendMessage('assistant', data.response);
            notifyIfHidden("AI Assistant", data.response);
        }
    });

    socket.on('log_event', (data) => {
        // Add to Council Console
        const entry = document.createElement('div');
        entry.className = `log-entry ${data.level || 'INFO'}`;
        entry.textContent = `[${new Date().toLocaleTimeString()}] ${data.message}`;
        councilContainer.appendChild(entry);
        councilContainer.scrollTop = councilContainer.scrollHeight;
    });

    function notifyIfHidden(title, body) {
        // Notify if document is hidden OR not focused
        if ((document.hidden || !document.hasFocus()) && notificationPermission === 'granted') {
            console.log("Triggering Notification:", title);
            const notif = new Notification(title, { body: body.substring(0, 100) + '...', icon: '/favicon.ico' });
            notif.onclick = () => {
                window.focus();
                const chatViewBtn = document.querySelector('[data-target="view-chat"]');
                if (chatViewBtn) chatViewBtn.click();
            };
        } else {
            console.log("Notification skipped. Hidden:", document.hidden, "Focused:", document.hasFocus(), "Perm:", notificationPermission);
        }
    }

    // --- Navigation Logic ---
    // 1. Sidebar Tools (Files, Terminal, Council, Memory, Settings)
    // 2. Main Stage (Chat, Editor)

    activityItems.forEach(item => {
        item.addEventListener('click', () => {
            const targetId = item.getAttribute('data-target');
            if (!targetId) return;

            // Handle Main Stage Switches (Chat / Editor)
            if (targetId === 'view-chat' || targetId === 'view-editor-main') {
                // Switch Main View
                mainViews.forEach(v => v.classList.remove('active'));
                const main = document.getElementById(targetId);
                if (main) main.classList.add('active');

                // If chat, AUTO-COLLAPSE SIDEBAR as requested
                if (targetId === 'view-chat') {
                    chatInput.focus();
                    sidebarPanel.style.display = 'none'; // Collapse sidebar
                    // Deselect sidebar tools
                    activityItems.forEach(i => {
                        if (i.parentElement.classList.contains('activity-top') && i.getAttribute('data-target') !== 'view-chat') {
                            i.classList.remove('active');
                        }
                    });
                }

                if (targetId === 'view-editor-main') {
                    setTimeout(() => editor.refresh(), 50);
                    // Do we open sidebar for editor? User didn't ask. Let's leave it as keeps state.
                }

                // Highlight navigation item
                activityItems.forEach(i => {
                    if (i.parentElement.classList.contains('activity-bottom') || i.getAttribute('data-target') === 'view-chat') {
                        i.classList.remove('active');
                    }
                });
                item.classList.add('active');
                return;
            }

            // Handle Sidebar Tool Switches

            // Check if clicking the ALREADY ACTIVE tool -> Toggle Off (Collapse)
            if (item.classList.contains('active')) {
                item.classList.remove('active');
                sidebarPanel.style.display = 'none';
                return;
            }

            // Normal Switch:
            // 1. Highlight
            activityItems.forEach(i => {
                if (i.parentElement.classList.contains('activity-top') && i.getAttribute('data-target') !== 'view-chat') {
                    i.classList.remove('active');
                }
            });
            item.classList.add('active');

            // 2. Show Sidebar Panel
            sidebarPanel.style.display = 'flex'; // Ensure visible

            // 3. Switch Sidebar Content
            sidebarViews.forEach(v => v.classList.add('hidden'));
            const view = document.getElementById(targetId);
            if (view) {
                view.classList.remove('hidden');
                if (targetId === 'view-sidebar-files') loadProjects();
                if (targetId === 'view-sidebar-memory') loadMemory();
            }
        });
    });

    // --- Feature: Memory Management ---
    async function loadMemory() {
        memoryList.innerHTML = '<div class="loading">Scanning Neural Network...</div>';
        try {
            const res = await fetch('/api/memory/facts');
            const data = await res.json();
            if (data.success) {
                memoryList.innerHTML = '';
                if (data.facts.length === 0) {
                    memoryList.innerHTML = '<div style="padding:10px; color:#666;">No facts recorded.</div>';
                    return;
                }
                data.facts.forEach(fact => {
                    const el = document.createElement('div');
                    el.className = 'fact-item';
                    el.innerHTML = `
                        <div class="fact-text">${fact.text}</div>
                        <div class="fact-meta">
                            <span>${new Date(fact.created_at).toLocaleDateString()}</span>
                            <span class="btn-delete" data-id="${fact.fact_id}">🗑️</span>
                        </div>
                    `;
                    // Delete Handler
                    el.querySelector('.btn-delete').addEventListener('click', async (e) => {
                        e.stopPropagation();
                        if (confirm('Forget this fact?')) {
                            await fetch(`/api/memory/facts/${fact.fact_id}`, { method: 'DELETE' });
                            loadMemory();
                        }
                    });
                    memoryList.appendChild(el);
                });
            }
        } catch (e) {
            memoryList.innerHTML = 'Memory Access Error';
        }
    }

    // Add Fact Handler
    const addFactBtn = document.getElementById('add-fact-btn');
    if (addFactBtn) {
        addFactBtn.addEventListener('click', async () => {
            const text = prompt("Enter new fact:");
            if (text) {
                await fetch('/api/memory/facts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text })
                });
                loadMemory();
            }
        });
    }


    // --- Feature: File Tree (Re-implemented for Sidebar) ---
    window.loadProjects = async function () {
        if (!fileTreeContainer) return;
        fileTreeContainer.innerHTML = '<div class="loading">Loading...</div>';
        try {
            const res = await fetch('/api/projects');
            const data = await res.json();
            if (data.success && data.projects) {
                fileTreeContainer.innerHTML = '';
                data.projects.forEach(p => {
                    const root = document.createElement('div');
                    root.className = 'tree-item project-root';
                    root.innerHTML = `<span class="icon">🚀</span> ${p.name}`;
                    fileTreeContainer.appendChild(root);

                    const childContainer = document.createElement('div');
                    childContainer.style.paddingLeft = '15px';
                    childContainer.style.display = 'none';
                    root.addEventListener('click', () => {
                        childContainer.style.display = childContainer.style.display === 'none' ? 'block' : 'none';
                        if (childContainer.children.length === 0) loadFiles(p.name, '', childContainer);
                    });
                    fileTreeContainer.appendChild(childContainer);
                });
            }
        } catch (e) { fileTreeContainer.innerHTML = 'Error loading projects.'; }
    }

    async function loadFiles(projName, path, container) {
        const res = await fetch(`/api/files/list?project_name=${projName}&path=${path}`);
        const data = await res.json();
        if (data.success) {
            container.innerHTML = '';
            data.directories.forEach(d => {
                const el = document.createElement('div');
                el.className = 'tree-item folder';
                el.innerHTML = `<span class="icon">📂</span> ${d}`;
                container.appendChild(el);
                const sub = document.createElement('div');
                sub.style.paddingLeft = '15px'; sub.style.display = 'none';
                container.appendChild(sub);
                el.addEventListener('click', (e) => {
                    e.stopPropagation();
                    sub.style.display = sub.style.display === 'none' ? 'block' : 'none';
                    if (sub.children.length === 0) loadFiles(projName, path ? path + '/' + d : d, sub);
                });
            });
            data.files.forEach(f => {
                const el = document.createElement('div');
                el.className = 'tree-item file';
                el.innerHTML = `<span class="icon">📄</span> ${f}`;
                el.addEventListener('click', (e) => {
                    e.stopPropagation();
                    // Remove active class from all tree items
                    document.querySelectorAll('.tree-item').forEach(i => i.classList.remove('active'));
                    // Add active class to clicked item
                    el.classList.add('active');

                    openFile(projName, path ? path + '/' + f : f);
                });
                container.appendChild(el);
            });
        }
    }

    async function openFile(projName, path) {
        const res = await fetch(`/api/files/read?project_name=${projName}&path=${path}`);
        const data = await res.json();
        if (data.success) {
            currentProject = projName;
            currentFilePath = path;
            document.getElementById('current-file-name').textContent = path;
            editor.setValue(data.content);
            // Switch to Editor View
            const editorTrigger = document.querySelector('[data-target="view-editor-main"]');
            if (editorTrigger) editorTrigger.click();
        }
    }

    async function saveFile() {
        if (!currentProject || !currentFilePath) return alert("No file open.");
        const content = editor.getValue();
        try {
            const res = await fetch('/api/files/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ project_name: currentProject, path: currentFilePath, content })
            });
            const data = await res.json();
            if (data.success) {
                alert("File saved!"); // Replace with better notification if available
            } else {
                alert("Save failed: " + data.error);
            }
        } catch (e) { alert("Error saving file."); }
    }

    async function runFile() {
        if (!currentFilePath) return alert("No file open.");
        // Switch to Terminal
        const terminalTrigger = document.querySelector('[data-target="view-sidebar-terminal"]');
        if (terminalTrigger) terminalTrigger.click();

        const termOutput = document.getElementById('terminal-output');
        termOutput.innerHTML += `<div class="line system">Running ${currentFilePath}...</div>`;

        try {
            const res = await fetch('/api/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: currentFilePath }) // run_script expects 'path' starting with 'projects/'? No, let's check web_app.py
                // web_app.py run_script: path must start with 'projects/'.
                // Our currentFilePath usually comes from 'get_project_file_content' which might be relative to project root?
                // Wait, list_files returns relative paths. openFile receives it.
                // We need to verify if currentFilePath includes "projects/" prefix or not. 
                // Looking at list_files in web_app.py: result['files'] are relative to project_path.
                // project_path = os.path.join(projects_dir, project_name).
                // So currentFilePath is relative to the project folder, e.g. "web_app.py" or "static/js/main.js".
                // But run_script expects "projects/...". 
                // We need to construct the full path expected by run_script (relative to projects_dir).
                // projects_dir is the root of all projects.
                // So path should be `${currentProject}/${currentFilePath}`.
            });

            // Wait, let's verify path construction.
            // if currentProject is "Self Evolving AI" and currentFilePath is "web_app.py".
            // We want to send "projects/Self Evolving AI/web_app.py" ?? Or just "Self Evolving AI/web_app.py"?
            // web_app.py: relative_path = path[len('projects/'):] -> assumes "projects/" prefix.
            // So we MUST send "projects/" + project_name + "/" + file_path.
        } catch (e) { }
    }

    // Correcting RunFile Logic based on investigation
    async function runFileCorrected() {
        if (!currentFilePath || !currentProject) return alert("No file open.");

        // Show Terminal
        const terminalTrigger = document.querySelector('[data-target="view-sidebar-terminal"]');
        if (terminalTrigger) terminalTrigger.click();

        const termOutput = document.getElementById('terminal-output');
        termOutput.innerHTML += `<div class="line command">$ python ${currentFilePath}</div>`;

        const fullPath = `projects/${currentProject}/${currentFilePath}`;

        try {
            const res = await fetch('/api/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: fullPath })
            });
            const data = await res.json();

            let outputText = data.success ? data.output : (data.error || data.output);
            lastRunOutput = outputText; // Store for AI

            // Display in terminal
            // Handle newlines for HTML
            const formattedOutput = outputText.replace(/\n/g, '<br>');
            termOutput.innerHTML += `<div class="line output">${formattedOutput}</div>`;
            termOutput.scrollTop = termOutput.scrollHeight;

            // Proactive Error Handling
            if (!data.success || outputText.toLowerCase().includes('traceback') || outputText.toLowerCase().includes('error:')) {
                // Trigger AI assistance
                const errorMessage = outputText.substring(0, 1000); // Limit length
                // Send a special "system" message to chat (hidden from user view initially? OR just standard)
                // Let's send it as a context-heavy prompt.
                // We want the AI to SPEAK to the user.
                // So we send a message ON BEHALF of the system.
                const systemPrompt = `[System Alert] The user executed '${currentFilePath}' and it failed with the following output:\n${errorMessage}\n\nPlease proactively offer help to fix this.`;

                // We don't want to double-post in user chat history visually if possible, but for now simple is better.
                // Let's NOT appendMessage('user', ...) for this system prompt, so it feels like the AI just noticed it.

                try {
                    // Note: we are NOT awaiting this because we don't want to block the UI. active listening.
                    fetch('/chat', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            message: systemPrompt,
                            context: {
                                currentFile: { path: currentFilePath, project: currentProject, content: editor.getValue() },
                                terminalOutput: outputText
                            }
                        })
                    }).then(r => r.json()).then(d => {
                        if (d.success) {
                            appendMessage('assistant', d.response);
                            notifyIfHidden("AI Assistant (Error Detected)", d.response);
                        }
                    });
                } catch (e) { }
            }

        } catch (e) {
            termOutput.innerHTML += `<div class="line error">Execution failed.</div>`;
        }
    }

    document.getElementById('save-file-btn')?.addEventListener('click', saveFile);
    document.getElementById('run-file-btn')?.addEventListener('click', runFileCorrected);

    // --- Chat Logic ---
    function appendMessage(role, text) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${role}`;
        let avatarText = role === 'user' ? '👤' : 'AI';

        // Better markdown formatting (basic)
        let formattedText = text
            .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\n/g, '<br>');

        msgDiv.innerHTML = `<div class="avatar">${avatarText}</div><div class="content">${formattedText}</div>`;
        chatContainer.appendChild(msgDiv);
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    async function sendMessage() {
        const message = chatInput.value.trim();
        if (!message) return;
        appendMessage('user', message);
        chatInput.value = '';

        // Prepare context
        let context = {};
        if (currentFilePath && editor) {
            context.currentFile = {
                path: currentFilePath,
                project: currentProject,
                content: editor.getValue() // Send full content? Might be large. AI needs it though.
            };
        }
        if (lastRunOutput) {
            context.terminalOutput = lastRunOutput;
        }

        try {
            const res = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message,
                    context: context
                })
            });
            const data = await res.json();
            if (data.success) {
                appendMessage('assistant', data.response);
                notifyIfHidden("AI Assistant", data.response);
            }
        } catch (e) { appendMessage('assistant', 'Error sending.'); }
    }
    if (sendBtn) sendBtn.addEventListener('click', sendMessage);

    if (chatInput) {
        chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
    }

    // Initial Load
    loadProjects();
    sidebarPanel.style.display = 'none'; // Default to collapsed
    activityItems.forEach(i => i.classList.remove('active')); // Deselect all sidebar tools
    document.querySelector('[data-target="view-chat"]').classList.add('active'); // Ensure Chat is active


    const testNotifyBtn = document.getElementById('test-notify-btn');
    if (testNotifyBtn) {
        testNotifyBtn.addEventListener('click', () => {
            if (notificationPermission === 'granted') {
                new Notification("System Test", { body: "Notifications are working!", icon: '/favicon.ico' });
            } else {
                Notification.requestPermission().then(p => {
                    notificationPermission = p;
                    if (p === 'granted') new Notification("System Test", { body: "Notifications are working!", icon: '/favicon.ico' });
                    else alert("Notifications blocked.");
                });
            }
        });
    }

});// Default to Files view
