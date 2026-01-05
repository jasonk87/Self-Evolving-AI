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
    const chatSessionsList = document.getElementById('chat-sessions-list');
    const newChatBtn = document.getElementById('new-chat-btn');

    // Voice Elements
    const micBtn = document.getElementById('mic-btn');
    const voiceToggleBtn = document.getElementById('voice-toggle-btn');

    // Editor State
    let currentProject = null;
    let currentFilePath = null;
    let editor = null;
    let lastRunOutput = ""; // Store last visualization/run output for AI context
    let currentSessionId = null; // Track active session


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

    // Typing Indicator Logic
    function showTypingIndicator() {
        if (document.getElementById('typing-indicator')) return; // Already showing
        const indicator = document.createElement('div');
        indicator.id = 'typing-indicator';
        indicator.className = 'typing-indicator';
        indicator.innerHTML = `
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
        `;
        chatContainer.appendChild(indicator);
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    function removeTypingIndicator() {
        const indicator = document.getElementById('typing-indicator');
        if (indicator) indicator.remove();
    }

    socket.on('response', (data) => {
        removeTypingIndicator();
        appendMessage('assistant', data.response);
        notifyIfHidden("AI Assistant", data.response);
    });

    socket.on('chat_response', (data) => {
        if (data.success) {
            removeTypingIndicator();
            appendMessage('assistant', data.response);
            notifyIfHidden("AI Assistant", data.response);
        }
    });

    socket.on('log_event', (data) => {
        // Add to Council Console
        const entry = document.createElement('div');
        entry.className = `log-entry ${data.level || 'INFO'}`;

        // Add spinner if it's a "start" or "processing" type event (heuristic)
        let icon = '';
        if (data.message.toLowerCase().includes('analyzing') || data.message.toLowerCase().includes('thinking')) {
            icon = '<span class="spinner"></span>';
        }

        entry.innerHTML = `[${new Date().toLocaleTimeString()}] ${icon} ${data.message}`;
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

    // --- Voice Logic (Speech-to-Text & Text-to-Speech) ---
    let recognition = null;
    let isListening = false;
    let autoSpeakEnabled = false; // "Voice Toggle"
    let lastInputWasVoice = false; // Track if we should reply with voice

    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        recognition = new SpeechRecognition();
        recognition.continuous = false; // Auto-stop after phrase
        recognition.interimResults = false;
        recognition.lang = 'en-US';

        recognition.onstart = () => {
            isListening = true;
            micBtn.classList.add('listening');
            chatInput.placeholder = "Listening...";
        };

        recognition.onend = () => {
            isListening = false;
            micBtn.classList.remove('listening');
            chatInput.placeholder = "Instructions...";
        };

        recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript;
            chatInput.value = transcript;
            lastInputWasVoice = true; // Mark as voice input
            sendMessage(); // Auto-send
        };

        recognition.onerror = (event) => {
            console.error("Speech Error:", event.error);
            isListening = false;
            micBtn.classList.remove('listening');
            chatInput.placeholder = "Error. Try again.";
        };
    } else {
        if (micBtn) micBtn.style.display = 'none'; // Hide if not supported
    }

    if (micBtn) {
        micBtn.addEventListener('click', () => {
            if (!recognition) return;
            if (isListening) {
                recognition.stop();
            } else {
                recognition.start();
            }
        });
    }

    if (voiceToggleBtn) {
        voiceToggleBtn.addEventListener('click', () => {
            autoSpeakEnabled = !autoSpeakEnabled;
            voiceToggleBtn.classList.toggle('active', autoSpeakEnabled);
            voiceToggleBtn.title = autoSpeakEnabled ? "Auto-Speech (On)" : "Auto-Speech (Off)";
        });
    }

    function speakText(text) {
        if (!('speechSynthesis' in window)) return;

        // Strip markdown/code for reading
        const cleanText = text.replace(/```[\s\S]*?```/g, " code block ")
                              .replace(/`([^`]+)`/g, "$1")
                              .replace(/[*_#]/g, "");

        const utterance = new SpeechSynthesisUtterance(cleanText);
        utterance.rate = 1.1;
        utterance.pitch = 1.0;

        // Try to select a good voice
        const voices = window.speechSynthesis.getVoices();
        const preferredVoice = voices.find(v => v.name.includes("Google US English") || v.name.includes("Samantha"));
        if (preferredVoice) utterance.voice = preferredVoice;

        window.speechSynthesis.speak(utterance);
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
                if (targetId === 'view-sidebar-chats') loadSessions();
            }
        });
    });

    // --- Feature: Chat Sessions ---
    async function loadSessions() {
        if (!chatSessionsList) return;
        chatSessionsList.innerHTML = '<div class="loading">Loading chats...</div>';
        try {
            const res = await fetch('/api/sessions');
            const data = await res.json();
            if (data.success) {
                chatSessionsList.innerHTML = '';
                if (!data.sessions || data.sessions.length === 0) {
                    chatSessionsList.innerHTML = '<div style="padding:10px; color:#666;">No active chats. Start a new one!</div>';
                } else {
                    data.sessions.forEach(s => {
                        const el = document.createElement('div');
                        el.className = `session-item ${s.id === currentSessionId ? 'active' : ''}`;
                        el.innerHTML = `
                            <div class="session-title">${s.title}</div>
                            <div class="session-meta">
                                <span>${new Date(s.updated_at * 1000).toLocaleDateString()}</span>
                                <span class="btn-delete-session" data-id="${s.id}">🗑️</span>
                            </div>
                        `;
                        // Load Session
                        el.addEventListener('click', () => loadChatSession(s.id));

                        // Delete Session
                        el.querySelector('.btn-delete-session').addEventListener('click', async (e) => {
                            e.stopPropagation();
                            showModal(
                                "Delete Chat",
                                `Are you sure you want to delete "${s.title}"? This cannot be undone.`,
                                async () => {
                                    await fetch(`/api/sessions/${s.id}`, { method: 'DELETE' });
                                    if (currentSessionId === s.id) {
                                        currentSessionId = null;
                                        chatContainer.innerHTML = ''; // Clear view
                                        appendMessage('system', '<div class="bubble">Session deleted.</div>');
                                    }
                                    loadSessions();
                                },
                                true // isDestructive
                            );
                        });
                        chatSessionsList.appendChild(el);
                    });
                }
            }
        } catch (e) {
            chatSessionsList.innerHTML = 'Error loading sessions';
        }
    }

    // --- Modal Logic ---
    function showModal(title, message, onConfirm, isDestructive = false) {
        const modal = document.getElementById('custom-modal');
        const modalTitle = document.getElementById('modal-title');
        const modalMessage = document.getElementById('modal-message');
        const confirmBtn = document.getElementById('modal-confirm-btn');
        const cancelBtn = document.getElementById('modal-cancel-btn');

        if (!modal) return;

        modalTitle.textContent = title;
        modalMessage.textContent = message;

        // Styling for destructive actions
        if (isDestructive) {
            confirmBtn.className = 'btn-modal danger';
            confirmBtn.textContent = 'Delete';
        } else {
            confirmBtn.className = 'btn-modal confirm';
            confirmBtn.textContent = 'Confirm';
        }

        // Event Handlers (One-time)
        const close = () => {
            modal.classList.remove('active');
            confirmBtn.onclick = null;
            cancelBtn.onclick = null;
        };

        confirmBtn.onclick = () => {
            onConfirm();
            close();
        };

        cancelBtn.onclick = close;

        // Show
        modal.classList.add('active');
    }

    async function loadChatSession(sessionId) {
        // Highlight in UI
        currentSessionId = sessionId;
        if (chatSessionsList) {
            const items = chatSessionsList.querySelectorAll('.session-item');
            items.forEach(i => i.classList.remove('active'));
            // Find the one with matching delete btn data-id (hacky but works)
            // Better: re-render or find by text content? 
            // Re-render is safer to update active state correctly
            loadSessions();
        }

        chatContainer.innerHTML = '<div class="loading">Loading history...</div>';

        try {
            const res = await fetch(`/api/sessions/${sessionId}`);
            const data = await res.json();
            chatContainer.innerHTML = ''; // Clear loading

            if (data.success && data.session) {
                // Populate History
                if (data.session.history && data.session.history.length > 0) {
                    data.session.history.forEach(msg => {
                        appendMessage(msg.role, msg.content);
                    });
                } else {
                    appendMessage('system', '<div class="bubble">New conversation started.</div>');
                }

                // Ensure main view is chat
                const chatTrigger = document.querySelector('[data-target="view-chat"]');
                // Don't auto-click fetch trigger if we want to keep sidebar open?
                // User clicked sidebar item, so they expect to see chat.
                // But standard behavior: selecting items updates the "main stage" (chat area) but KEEPS sidebar open (like VS Code explorer).
                // So we do NOT switch views, just ensure chat main stage is visible if it wasn't?
                const chatMain = document.getElementById('view-chat');
                if (chatMain && !chatMain.classList.contains('active')) {
                    // We need to switch main stage but NOT close sidebar
                    mainViews.forEach(v => v.classList.remove('active'));
                    chatMain.classList.add('active');
                }
                chatInput.focus();
            }
        } catch (e) {
            chatContainer.innerHTML = 'Error loading chat history.';
        }
    }

    if (newChatBtn) {
        newChatBtn.addEventListener('click', async () => {
            // Create new session via API
            try {
                const res = await fetch('/api/sessions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: "New Chat" })
                });
                const data = await res.json();
                if (data.success) {
                    loadChatSession(data.session_id);
                }
            } catch (e) {
                alert("Failed to create new chat");
            }
        });
    }

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
                        showModal(
                            "Forget Fact",
                            "Are you sure you want to delete this memory?",
                            async () => {
                                await fetch(`/api/memory/facts/${fact.fact_id}`, { method: 'DELETE' });
                                loadMemory();
                            },
                            true
                        );
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
        showTypingIndicator(); // Show typing immediately

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
                    context: context,
                    session_id: currentSessionId
                })
            });
            const data = await res.json();
            removeTypingIndicator(); // Remove on fetch complete (though socket might handle it too)

            if (data.success) {
                // Update Session ID if it was new
                if (data.session_id && currentSessionId !== data.session_id) {
                    currentSessionId = data.session_id;
                    // Refresh list to show new title/session
                    loadSessions();
                } else {
                    // Refresh list to update timestamp/title?
                    // Maybe debounce this or only do it occasionally.
                    // For now, let's do it to keep "Last Updated" fresh.
                    loadSessions();
                }

                // If the backend sends 'chat_response' via socket, this might double post if we don't check.
                // Current implementation in web_app.py returns JSON response AND doesn't seem to emit chat_response for the direct reply?
                // Wait, web_app.py returns jsonify(...). It does NOT emit 'chat_response' for the main reply.
                // So we MUST append here.
                appendMessage('assistant', data.response);
                notifyIfHidden("AI Assistant", data.response);

                // Handle Voice Response
                if (lastInputWasVoice || autoSpeakEnabled) {
                    speakText(data.response);
                }
                lastInputWasVoice = false; // Reset for next turn
            }
        } catch (e) {
            removeTypingIndicator();
            appendMessage('assistant', 'Error sending.');
        }
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
    // Default: Show Chat Sessions Sidebar
    loadSessions();
    const chatSidebarBtn = document.querySelector('[data-target="view-sidebar-chats"]');
    if (chatSidebarBtn) chatSidebarBtn.click();

    // Ensure Chat Main Stage is active (it is by default in HTML usually, but good to force)
    document.querySelector('[data-target="view-chat"]').classList.add('active');


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
