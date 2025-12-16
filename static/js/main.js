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
    const imageUploadInput = document.getElementById('image-upload-input');
    const imageUploadBtn = document.getElementById('image-upload-btn');
    const imagePreviewContainer = document.getElementById('image-preview-container');
    const imagePreviewImg = document.getElementById('image-preview-img');
    const clearImageBtn = document.getElementById('clear-image-btn');
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
    function showTypingIndicator(text = "Thinking...") {
        const existing = document.getElementById('typing-indicator');
        if (existing) {
            // Update text if already exists
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
        chatContainer.appendChild(indicator);
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    function removeTypingIndicator() {
        const indicator = document.getElementById('typing-indicator');
        if (indicator) indicator.remove();
    }

    // Prevent duplicate AI messages by tracking the last processed response ID or content hash
    let lastResponseHash = "";

    // Hash function for simple string deduplication
    const cyrb53 = (str, seed = 0) => {
        let h1 = 0xdeadbeef ^ seed, h2 = 0x41c6ce57 ^ seed;
        for (let i = 0, ch; i < str.length; i++) {
            ch = str.charCodeAt(i);
            h1 = Math.imul(h1 ^ ch, 2654435761);
            h2 = Math.imul(h2 ^ ch, 1597334677);
        }
        h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
        h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
        return 4294967296 * (2097151 & h2) + (h1 >>> 0);
    };

    socket.on('response', (data) => {
        // Handle generic response events (e.g. from Terminal)

        // Check if we are waiting for terminal output
        if (window.isWaitingForTerminal && window.handleTerminalResponse) {
            window.handleTerminalResponse(data.response);
            window.isWaitingForTerminal = false;
        } else {
            // Only log if not terminal (to avoid console noise)
            // console.log("Socket 'response' received (Ignored/Not Terminal):", data);
        }
    });

    socket.on('chat_response', (data) => {
        if (data.success) {
            const hash = cyrb53(data.response);
            if (hash === lastResponseHash) return; // Prevent duplicate

            removeTypingIndicator();

            // Check if we are waiting for terminal output
            console.log("Socket response received:", data);
            console.log("isWaitingForTerminal:", window.isWaitingForTerminal);
            console.log("handleTerminalResponse exists:", !!window.handleTerminalResponse);

            if (window.isWaitingForTerminal && window.handleTerminalResponse) {
                console.log("Routing to terminal...");
                window.handleTerminalResponse(data.response);
                window.isWaitingForTerminal = false;
                // Don't append to chat if it was a terminal command? 
                // Let's allow it in chat too for history, but maybe suppress notification?
                // For now, let's just append to chat as well so they have a record.
            }

            appendMessage('assistant', data.response);
            lastResponseHash = hash;
            notifyIfHidden("AI Assistant", data.response);
        }
    });

    socket.on('log_event', (data) => {
        // Add to Council Console
        const entry = document.createElement('div');
        entry.className = `log-entry ${data.level || 'INFO'}`;

        let icon = '';
        const msgLower = data.message.toLowerCase();

        // Icons based on content/logger
        if (msgLower.includes('analyzing') || msgLower.includes('thinking') || msgLower.includes('planning')) {
            icon = '🧠';
        } else if (data.logger && data.logger.includes('ActionExecutor')) {
            icon = '⚡';
        } else if (data.level === 'ERROR') {
            icon = '❌';
        } else if (data.level === 'WARNING') {
            icon = '⚠️';
        }

        const timestamp = new Date().toLocaleTimeString('en-US', { hour12: false });
        const loggerName = data.logger ? `<span class="logger-name">[${data.logger.split('.').pop()}]</span>` : '';

        entry.innerHTML = `<span class="timestamp">${timestamp}</span> ${loggerName} ${icon} <span class="log-msg">${data.message}</span>`;

        councilContainer.appendChild(entry);
        councilContainer.scrollTop = councilContainer.scrollHeight;
    });

    socket.on('task_update', (task) => {
        // Prevent background tasks from hijacking the chat UI "Thinking" state
        // Only show status for tasks belonging to this session
        if (task.session_id && task.session_id !== currentSessionId) {
            return;
        }

        // If task has NO session_id, it is likely a background task (e.g. Council Debate from cron).
        // We should NOT show blocking "Thinking..." UI for these.
        if (!task.session_id) {
            // Optional: Show in a non-intrusive way (toast/statusbar) instead?
            // For now, identifying it's a background task and NOT blocking chat is the priority.
            console.log("Background task update ignored in chat:", task.description);
            return;
        }

        // Show detailed status in chat if "Thinking"
        if (task.status !== 'COMPLETED_SUCCESSFULLY' &&
            task.status !== 'FAILED_UNKNOWN' &&
            !task.status.startsWith('FAILED')) {

            let statusText = task.current_step_description || task.description || "Processing...";
            if (statusText.length > 50) statusText = statusText.substring(0, 50) + "...";

            showTypingIndicator(statusText);
        } else {
            // If completed/failed, we might want to remove indicator OR wait for final chat response.
            // Usually chat response comes after. Let's leave it, but maybe update text.
            // removeTypingIndicator(); // Don't remove, let the final response do it.
            if (task.status === 'COMPLETED_SUCCESSFULLY') {
                showTypingIndicator("Finalizing...");
            }
        }
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
            const result = event.results[0];
            const transcript = result[0].transcript;

            // Only process final results
            if (!result.isFinal) return;

            if (!transcript.trim()) return;

            // Prevent double-submission guard
            if (chatInput.disabled) return;

            chatInput.value = transcript;
            lastInputWasVoice = true;
            isListening = false;
            recognition.stop();
            micBtn.classList.remove('listening');

            // Disable input briefly to prevent race conditions
            chatInput.disabled = true;
            sendMessage().then(() => {
                chatInput.disabled = false;
                chatInput.focus();
            });
        };

        recognition.onerror = (event) => {
            console.warn("Speech Recognition Error:", event.error); // Warn instead of Error to reduce noise
            isListening = false;
            micBtn.classList.remove('listening');

            if (event.error === 'not-allowed') {
                chatInput.placeholder = "Mic permission denied.";
                // We could show a notification or toast here
            } else if (event.error === 'no-speech') {
                chatInput.placeholder = "No speech detected.";
            } else {
                chatInput.placeholder = "Error. Try again.";
            }
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
        if (!text) return;

        // Strip markdown for cleaner reading
        const cleanText = text.replace(/```[\s\S]*?```/g, "Code block omitted.")
            .replace(/`([^`]+)`/g, "$1")
            .replace(/\*/g, ""); // Basic cleanup

        // Use backend TTS
        fetch('/api/speak', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: cleanText.substring(0, 1000) }) // Limit length
        })
            .then(response => {
                if (!response.ok) throw new Error("TTS Failed");
                return response.blob();
            })
            .then(blob => {
                const url = URL.createObjectURL(blob);
                const audio = new Audio(url);
                audio.play();
                audio.onended = () => URL.revokeObjectURL(url);
            })
            .catch(err => {
                console.error("TTS Error, falling back to local:", err);
                // Fallback to local synthesis
                if ('speechSynthesis' in window) {
                    const utterance = new SpeechSynthesisUtterance(cleanText);
                    utterance.rate = 1.0;
                    utterance.pitch = 1.0;
                    window.speechSynthesis.speak(utterance);
                }
            });
    }


    // --- Terminal Input Logic (Global) ---
    const termInput = document.getElementById('terminal-input');
    const termSendBtn = document.getElementById('terminal-send-btn');
    const terminalOutputDiv = document.getElementById('terminal-output');

    // Global state for terminal
    window.isWaitingForTerminal = false;
    window.handleTerminalResponse = (responseText) => {
        if (!terminalOutputDiv) return;

        // Remove "Processing..." line if it exists (it's the last child)
        if (terminalOutputDiv.lastChild && terminalOutputDiv.lastChild.textContent === 'Processing...') {
            terminalOutputDiv.removeChild(terminalOutputDiv.lastChild);
        }

        const outputLine = document.createElement('div');
        outputLine.className = 'line output';
        // Handle basic formatting
        outputLine.innerHTML = responseText.replace(/\n/g, '<br>');
        terminalOutputDiv.appendChild(outputLine);
        terminalOutputDiv.scrollTop = terminalOutputDiv.scrollHeight;
    };

    const aiSuggestions = [
        "I noticed an error in the terminal. Do you want me to see if I can help?",
        "That command didn't work as expected. Should I investigate?",
        "It looks like something went wrong. Want me to take a look?",
        "Error detected. Do you want me to try and fix it?"
    ];

    function showAiAssistanceSuggestion(originalCmd, errorContext) {
        if (!terminalOutputDiv) return;

        const suggestion = aiSuggestions[Math.floor(Math.random() * aiSuggestions.length)];

        const div = document.createElement('div');
        div.className = 'line system ai-suggestion';
        div.style.marginTop = '10px';
        div.style.padding = '10px';
        div.style.backgroundColor = 'rgba(255, 255, 255, 0.05)';
        div.style.borderRadius = '5px';
        div.style.borderLeft = '3px solid var(--accent-color)';

        div.innerHTML = `
            <div style="margin-bottom:8px;">🤖 ${suggestion}</div>
            <button class="btn-xs" style="padding: 4px 8px; cursor: pointer; background: var(--accent-color); border: none; color: white; border-radius: 4px;">Yes, help me fix this</button>
        `;

        const btn = div.querySelector('button');
        btn.onclick = () => {
            div.remove(); // Remove suggestion

            // Construct specific help request
            const message = `I ran the command \`${originalCmd}\` and it failed. Here is the output:\n\`\`\`\n${errorContext}\n\`\`\`\nCan you help me fix this?`;

            // Show in chat as user message
            // appendMessage('user', message); // Optional: Do we want to duplicate it in chat? Yes, for history.

            // Send to AI
            if (socket) {
                // We want the response in the terminal?
                window.isWaitingForTerminal = true;
                socket.emit('message', { session_id: currentSessionId, message: message });

                if (terminalOutputDiv) {
                    const loading = document.createElement('div');
                    loading.className = 'line system';
                    loading.innerText = 'AI Analysis running...';
                    terminalOutputDiv.appendChild(loading);
                }
            }
        };

        terminalOutputDiv.appendChild(div);
        terminalOutputDiv.scrollTop = terminalOutputDiv.scrollHeight;
    }

    async function sendTerminalCommand() {
        if (!termInput) return;
        const cmd = termInput.value.trim();
        if (!cmd) return;

        // Display user command
        const cmdLine = document.createElement('div');
        cmdLine.className = 'line command';
        cmdLine.innerText = `$ ${cmd}`;
        if (terminalOutputDiv) {
            terminalOutputDiv.appendChild(cmdLine);
            terminalOutputDiv.scrollTop = terminalOutputDiv.scrollHeight;
        }
        termInput.value = '';

        // Reset waiting flag
        window.isWaitingForTerminal = false;

        // Loading feedback
        let loadingId = 'term-loading-' + Date.now();
        if (terminalOutputDiv) {
            const loading = document.createElement('div');
            loading.id = loadingId;
            loading.className = 'line system';
            loading.innerText = 'Executing...';
            terminalOutputDiv.appendChild(loading);
            terminalOutputDiv.scrollTop = terminalOutputDiv.scrollHeight;
        }

        try {
            const res = await fetch('/api/terminal/exec', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: cmd, project_name: currentProject })
            });
            const data = await res.json();

            // Remove loading
            const loadingEl = document.getElementById(loadingId);
            if (loadingEl) loadingEl.remove();

            if (data.success) {
                // Display Stdout
                if (data.stdout) {
                    const out = document.createElement('div');
                    out.className = 'line output';
                    out.innerHTML = data.stdout.replace(/\n/g, '<br>');
                    terminalOutputDiv.appendChild(out);
                }
                // Display Stderr
                if (data.stderr) {
                    const err = document.createElement('div');
                    err.className = 'line output error';
                    err.style.color = '#ff6b6b';
                    err.innerHTML = data.stderr.replace(/\n/g, '<br>');
                    terminalOutputDiv.appendChild(err);
                }

                terminalOutputDiv.scrollTop = terminalOutputDiv.scrollHeight;

                // Intelligent Error Detection
                if (data.returncode !== 0 || (data.stderr && data.stderr.trim().length > 0)) {
                    showAiAssistanceSuggestion(cmd, `Command failed with code ${data.returncode}.\nStderr: ${data.stderr}\nStdout: ${data.stdout}`);
                }
            } else {
                // API Error
                const err = document.createElement('div');
                err.className = 'line error';
                err.innerText = "Execution Error: " + (data.error || "Unknown error");
                terminalOutputDiv.appendChild(err);
                showAiAssistanceSuggestion(cmd, "Execution Error: " + data.error);
                terminalOutputDiv.scrollTop = terminalOutputDiv.scrollHeight;
            }

        } catch (e) {
            const loadingEl = document.getElementById(loadingId);
            if (loadingEl) loadingEl.remove();

            if (terminalOutputDiv) {
                const err = document.createElement('div');
                err.className = 'line error';
                err.innerText = "Network Error: " + e.message;
                terminalOutputDiv.appendChild(err);
                terminalOutputDiv.scrollTop = terminalOutputDiv.scrollHeight;
            }
        }
    }

    if (termSendBtn) {
        termSendBtn.addEventListener('click', sendTerminalCommand);
    }
    if (termInput) {
        termInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') sendTerminalCommand();
        });
    }

    // --- Navigation Logic ---
    // 1. Sidebar Tools (Files, Terminal, Council, Memory, Settings)
    // 2. Main Stage (Chat, Editor)

    activityItems.forEach(item => {
        item.addEventListener('click', () => {
            const targetId = item.getAttribute('data-target');
            if (!targetId) return;

            // Handle Main Stage Switches (Chat / Editor / Cortex)
            if (targetId === 'view-chat' || targetId === 'view-editor-main' || targetId === 'view-cortex' || targetId === 'view-mission-control') {
                // Switch Main View
                mainViews.forEach(v => v.classList.remove('active'));
                const main = document.getElementById(targetId);
                if (main) main.classList.add('active');

                // If chat, AUTO-COLLAPSE SIDEBAR as requested
                if (targetId === 'view-chat' || targetId === 'view-mission-control') {
                    chatInput.focus();
                    sidebarPanel.classList.add('collapsed'); // Collapse sidebar
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
                sidebarPanel.classList.add('collapsed');
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
            sidebarPanel.classList.remove('collapsed'); // Ensure visible

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
                            window.showModal(
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
    window.showModal = function (title, message, onConfirm, isDestructive = false, showCancel = true) {
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
            confirmBtn.textContent = 'OK';
        }

        // Toggle Cancel Button
        if (!showCancel) {
            cancelBtn.style.display = 'none';
            confirmBtn.style.width = '100%';
        } else {
            cancelBtn.style.display = 'block';
            confirmBtn.style.width = 'auto';
        }

        // Event Handlers (One-time)
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

        // Show
        modal.classList.add('active');
    }

    // Helper for simple alerts
    window.showAlert = function (title, message) {
        window.showModal(title, message, null, false, false);
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
                        appendMessage(msg.role, msg.content, msg.images);
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

                // Mobile UX: If on mobile, collapse sidebar by triggering the chat view button
                if (window.innerWidth <= 768) {
                    const chatNavBtn = document.querySelector('[data-target="view-chat"]');
                    if (chatNavBtn) chatNavBtn.click();
                }
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
                    await loadChatSession(data.session_id);

                    // Mobile UX: Switch to main chat view immediately
                    const chatNavBtn = document.querySelector('[data-target="view-chat"]');
                    if (chatNavBtn) chatNavBtn.click();
                }
            } catch (e) {
                window.showAlert("Error", "Failed to create new chat");
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
                        window.showModal(
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
        if (!currentProject || !currentFilePath) return window.showAlert("Info", "No file open.");
        const content = editor.getValue();
        try {
            const res = await fetch('/api/files/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ project_name: currentProject, path: currentFilePath, content })
            });
            const data = await res.json();
            if (data.success) {
                // Optional: Toast notification instead of modal for success? For now, modal is fine.
                // window.showAlert("Success", "File saved!"); 
                // Actually user might find it annoying to dismiss. Let's rely on standard log or something?
                // But user ASKED for modal.
                // Let's use a non-intrusive notification if we had one, but we don't really.
                // We'll use the modal but maybe we can make it auto-close later.
                window.showAlert("Success", "File saved successfully.");
            } else {
                window.showAlert("Error", "Save failed: " + data.error);
            }
        } catch (e) { window.showAlert("Error", "Error saving file."); }
    }

    async function runFile() {
        if (!currentFilePath) return window.showAlert("Info", "No file open.");
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
        if (!currentFilePath || !currentProject) return window.showAlert("Info", "No file open.");

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
    function appendMessage(role, text, images = null) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${role}`;
        let avatarText = role === 'user' ? '👤' : 'AI';

        // Handle images
        let imagesHtml = '';
        if (images && images.length > 0) {
            images.forEach(imgB64 => {
                 // Check if it already has the prefix or not. Backend stores whatever we sent.
                 // In sendMessage we stripped the prefix. So we likely need to add it back if missing.
                 let src = imgB64;
                 if (!src.startsWith('data:image')) {
                     src = `data:image/png;base64,${imgB64}`;
                 }
                 imagesHtml += `<div class="user-uploaded-image"><img src="${src}" style="max-width: 200px; border-radius: 5px; margin-bottom: 5px;"></div>`;
            });
        }

        // Split by html-dynamic blocks
        let parts = text.split(/(```html-dynamic[\s\S]*?```)/g);
        let finalHtml = "";

        parts.forEach(part => {
            // Check if it is our special block
            if (part.startsWith("```html-dynamic") && part.endsWith("```")) {
                // Extract raw HTML
                // Remove the first line (marker) and the last line (ticks) more robustly
                let rawHtml = part.replace(/^```html-dynamic\s*/, "").replace(/```$/, "");

                // Sanitize
                // We assume DOMPurify is loaded globally from index.html
                let cleanHtml = "";
                if (typeof DOMPurify !== 'undefined') {
                    cleanHtml = DOMPurify.sanitize(rawHtml);
                } else {
                    cleanHtml = "<i>(DOMPurify not loaded - HTML suppressed for safety)</i>";
                }

                finalHtml += `<div class="dynamic-html-wrapper">${cleanHtml}</div>`;
            } else {
                // Normal text processing (Basic Markdown)
                let md = part
                    // Handle normal code blocks
                    .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
                    // Inline code
                    .replace(/`([^`]+)`/g, '<code>$1</code>')
                    // Newlines
                    .replace(/\n/g, '<br>');
                finalHtml += md;
            }
        });

        msgDiv.innerHTML = `<div class="avatar">${avatarText}</div><div class="content">${imagesHtml}${finalHtml}</div>`;
        chatContainer.appendChild(msgDiv);
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    async function sendMessage() {
        const message = chatInput.value.trim();
        // Allow sending if image is present even if text is empty? For now require either.
        if (!message && !currentImageBase64) return;

        // Display Image in Chat History if present
        let displayMessage = message;
        if (currentImageBase64) {
             displayMessage = `<div class="user-uploaded-image"><img src="${currentImageBase64}" style="max-width: 200px; border-radius: 5px; margin-bottom: 5px;"></div>` + displayMessage;
        }

        appendMessage('user', displayMessage);
        chatInput.value = '';

        // Prepare images list
        let images = [];
        if (currentImageBase64) {
            // Strip the data URL prefix "data:image/png;base64," as backend likely expects pure b64
            // But gemini_client handles pure b64. Let's send raw base64 data only.
            const base64Data = currentImageBase64.split(',')[1];
            images.push(base64Data);

            // Clear image after sending
            currentImageBase64 = null;
            if (imageUploadInput) imageUploadInput.value = '';
            if (imagePreviewContainer) imagePreviewContainer.classList.add('hidden');
        }

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
                    images: images,
                    context: context,
                    session_id: currentSessionId
                })
            });
            const data = await res.json();
            removeTypingIndicator(); // Remove on fetch complete (though socket might handle it too)

            if (data.success || data.response) {
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
                // Track this response hash to avoid socket duplication
                lastResponseHash = cyrb53(data.response);

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
    if (sendBtn) sendBtn.addEventListener('click', (e) => {
        e.preventDefault(); // Prevent accidental form submit
        sendMessage();
    });

    if (chatInput) {
        chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
    }

    // --- Image Upload Logic ---
    let currentImageBase64 = null;

    if (imageUploadBtn && imageUploadInput) {
        imageUploadBtn.addEventListener('click', () => {
            imageUploadInput.click();
        });

        imageUploadInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = (event) => {
                const base64String = event.target.result; // "data:image/png;base64,..."
                currentImageBase64 = base64String;

                // Show Preview
                if (imagePreviewImg && imagePreviewContainer) {
                    imagePreviewImg.src = base64String;
                    imagePreviewContainer.classList.remove('hidden');
                }
            };
            reader.readAsDataURL(file);
        });
    }

    if (clearImageBtn) {
        clearImageBtn.addEventListener('click', () => {
            currentImageBase64 = null;
            if (imageUploadInput) imageUploadInput.value = ''; // Reset file input
            if (imagePreviewContainer) imagePreviewContainer.classList.add('hidden');
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
