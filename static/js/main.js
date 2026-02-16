
// static/js/main.js
import { socket } from './modules/socket_client.js';
import * as UI from './modules/ui.js';
import * as Chat from './modules/chat.js';
import * as Terminal from './modules/terminal.js';
import * as Files from './modules/files.js';
import * as Voice from './modules/voice.js';
import * as Settings from './modules/settings.js';
import * as Memory from './modules/memory.js';
import { notifyIfHidden } from './modules/utils.js';

// Expose Globals
window.confirmShutdown = UI.confirmShutdown;

// Top-level startup log
console.log("[System] Main.js initializing...");

document.addEventListener('DOMContentLoaded', () => {
    console.log("[System] DOM Ready. Initializing modules...");

    // ... (References) ...
    // Global Error Handler for Unhandled Rejections
    window.addEventListener('unhandledrejection', event => {
        console.error("[System] Unhandled promise rejection:", event.reason);
    });

    // --- Event Delegation for New Chat ---
    // We bind to document to ensure we catch clicks even if the button is replaced
    document.addEventListener('click', async (e) => {
        const btn = e.target.closest('#new-chat-btn');
        if (btn) {
            console.log("[System] New Chat button clicked (Delegated Event)");
            e.preventDefault();
            e.stopPropagation();

            try {
                const res = await fetch('/api/sessions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: "New Chat" })
                });

                if (!res.ok) {
                    throw new Error(`Server returned ${res.status}: ${res.statusText}`);
                }

                const data = await res.json();
                console.log("[System] New Session Created:", data);

                if (data.success) {
                    if (Chat && typeof Chat.loadChatSession === 'function') {
                        Chat.loadChatSession(data.session_id, chatContainer);
                        Chat.loadSessions(chatSessionsList, (sid) => {
                            Chat.loadChatSession(sid, chatContainer);
                            Layout.openMainView('view-chat', 'Chat');
                        });
                        Layout.openMainView('view-chat', 'Chat');
                    } else {
                        console.error("[System] Chat module not fully loaded", Chat);
                        UI.showAlert("Error", "Chat module not ready. Please refresh.");
                    }
                } else {
                    throw new Error(data.error || "Unknown error");
                }
            } catch (err) {
                console.error("[System] New Chat Error:", err);
                UI.showAlert("Error", `Failed to create new chat: ${err.message}`);
                if (!UI || !UI.showAlert) alert(`Error: ${err.message}`);
            }
        }
    });

    // ... (rest of init)

    const mobileChatBtn = document.getElementById('mobile-chat-btn');
    const sidebarPanel = document.getElementById('sidebar-panel');
    const bottomPanel = document.getElementById('bottom-panel');
    const sidebarViews = document.querySelectorAll('.sidebar-view');
    const mainViews = document.querySelectorAll('.view-container');
    const mainTabsHeader = document.getElementById('main-tabs-header');

    // Components
    const chatContainer = document.getElementById('chat-container');
    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const terminalOutput = document.getElementById('terminal-output');
    const terminalInput = document.getElementById('terminal-input');
    const terminalSendBtn = document.getElementById('terminal-send-btn');
    const editorContainer = document.getElementById('editor-container');
    const chatSessionsList = document.getElementById('chat-sessions-list');
    const fileTreeContainer = document.getElementById('file-tree');

    // --- Layout State Management ---
    const Layout = {
        openSidebarView: (viewId) => {
            // 1. Show Panel
            sidebarPanel.classList.remove('collapsed');
            sidebarPanel.classList.add('active'); // for mobile

            // 2. Switch Content
            sidebarViews.forEach(v => v.classList.add('hidden'));
            const view = document.getElementById(viewId);
            if (view) view.classList.remove('hidden');

            // 3. Highlight Activity Icon
            document.querySelectorAll('.activity-item').forEach(i => i.classList.remove('active'));
            const trigger = document.querySelector(`.activity-item[data-target="${viewId}"]`);
            if (trigger) trigger.classList.add('active');
        },

        closeSidebar: () => {
            sidebarPanel.classList.add('collapsed');
            sidebarPanel.classList.remove('active');
            document.querySelectorAll('.activity-item').forEach(i => i.classList.remove('active'));
            // Keep Chat icon active if we are in chat? No, they are separate now.
        },

        openMainView: (viewId, tabLabel = "View") => {
            // 1. Switch View Container
            mainViews.forEach(v => v.classList.remove('active'));
            const view = document.getElementById(viewId);
            if (view) view.classList.add('active');

            // 2. Update Tabs
            Layout.updateMainTabs(viewId, tabLabel);

            // 3. Special Handling
            if (viewId === 'view-chat' && chatInput) chatInput.focus();
            if (viewId === 'view-editor-main') {
                // Trigger resize on Editor
                setTimeout(() => window.dispatchEvent(new Event('resize')), 50);
            }
        },

        updateMainTabs: (activeViewId, label) => {
            // Simple logic: If tab exists, activate it. If not, append it.
            // For now, we will just rebuild headers or static toggle.
            // Let's implement a dynamic tab system later. For now, simple toggling.
            const tabs = mainTabsHeader.querySelectorAll('.main-tab');
            tabs.forEach(t => t.classList.remove('active'));

            let targetTab = mainTabsHeader.querySelector(`[data-target="${activeViewId}"]`);
            if (!targetTab) {
                // Create Tab
                targetTab = document.createElement('div');
                targetTab.className = 'main-tab active';
                targetTab.dataset.target = activeViewId;
                targetTab.innerHTML = `<span>${label}</span><span class="close-tab">×</span>`;
                targetTab.onclick = (e) => {
                    if (e.target.classList.contains('close-tab')) {
                        // Close logic
                        targetTab.remove();
                        Layout.openMainView('view-chat', 'Chat');
                        return;
                    }
                    Layout.openMainView(activeViewId, label);
                };
                mainTabsHeader.appendChild(targetTab);
            }
            targetTab.classList.add('active');
        },

        toggleBottomPanel: (forceState = null) => {
            if (forceState === true) bottomPanel.classList.remove('collapsed');
            else if (forceState === false) bottomPanel.classList.add('collapsed');
            else bottomPanel.classList.toggle('collapsed');
        }
    };

    // --- Initialization ---

    // 1. Editor
    let editor = null;
    if (editorContainer) {
        editor = CodeMirror(editorContainer, {
            mode: "python", theme: "dracula",
            lineNumbers: true, indentUnit: 4, matchBrackets: true
        });
        new ResizeObserver(() => editor.refresh()).observe(editorContainer);
        Files.setEditor(editor);

        // Hook into Files.js to update tab name
        // We'll need to monkey-patch or update Files.js later.
        // For now, let's observe changes?
    }

    // 2. Voice
    Voice.initVoice((transcript) => {
        if (chatInput) chatInput.value = transcript;
    });

    // 3. Settings
    Settings.init();
    Settings.loadConfig();

    // 4. Initial Load
    Files.loadProjects(fileTreeContainer, () => {
        const path = Files.getCurrentFilePath();
        const filename = path ? path.split('/').pop() : 'Editor';
        Layout.openMainView('view-editor-main', filename);
    });
    Chat.loadSessions(chatSessionsList, (sessionId) => {
        Chat.loadChatSession(sessionId, chatContainer);
    });

    // --- Toolbar & System Listeners ---

    const shutdownBtn = document.getElementById('shutdown-btn');
    if (shutdownBtn) {
        shutdownBtn.addEventListener('click', () => {
            Settings.shutdownSystem();
        });
    }

    document.querySelectorAll('.toolbar-item').forEach(item => {
        item.addEventListener('click', () => {
            const targetId = item.dataset.target;
            if (item.classList.contains('item-shutdown')) return;

            if (targetId) {
                Layout.openMainView(targetId, item.title || 'View');
                if (targetId === 'view-settings') {
                    Settings.loadConfig();
                }
            }
        });
    });


    // --- Event Listeners ---

    // 1. Navigation (Activity Bar)
    document.querySelectorAll('.activity-item').forEach(item => {
        item.addEventListener('click', () => {
            const target = item.dataset.target;
            const label = item.dataset.label || item.title;

            if (!target) return;

            // Sidebar Views Only for Activity Bar
            if (target.startsWith('view-sidebar-')) {
                // Toggle Logic
                if (item.classList.contains('active')) {
                    Layout.closeSidebar();
                } else {
                    Layout.openSidebarView(target);
                    // Load Data
                    if (target === 'view-sidebar-files') Files.loadProjects(fileTreeContainer, () => {
                        const path = Files.getCurrentFilePath();
                        const filename = path ? path.split('/').pop() : 'Editor';
                        Layout.openMainView('view-editor-main', filename);
                    });
                    if (target === 'view-sidebar-chats') Chat.loadSessions(chatSessionsList, (sessionId) => {
                        Chat.loadChatSession(sessionId, chatContainer);
                        Layout.openMainView('view-chat', 'Chat'); // Switch to stage
                    });
                    if (target === 'view-sidebar-memory') Memory.loadMemory(
                        document.getElementById('memory-list'),
                        document.getElementById('episodes-list'),
                        document.getElementById('facts-list')
                    );
                }
            }
        });
    });

    // 1b. Toolbar Navigation (Top Right)
    document.querySelectorAll('.toolbar-item').forEach(item => {
        item.addEventListener('click', () => {
            const target = item.dataset.target;
            const label = item.getAttribute('title');
            if (target) {
                Layout.openMainView(target, label);
            }
        });
    });



    // 1c. Mobile Chat/Home Button
    if (mobileChatBtn) {
        mobileChatBtn.addEventListener('click', () => {
            Layout.openMainView('view-chat', 'Chat');
        });
    }

    // 2. Mobile Menu
    const mobileMenuBtn = document.getElementById('mobile-menu-btn');
    const mobileOverlay = document.getElementById('mobile-overlay');

    if (mobileMenuBtn) {
        mobileMenuBtn.addEventListener('click', () => {
            sidebarPanel.classList.toggle('active'); // Mobile drawer
            mobileOverlay.classList.toggle('active');

            // If opening, ensure sidebar is visible (not collapsed width)
            if (sidebarPanel.classList.contains('active')) {
                sidebarPanel.classList.remove('collapsed');
                // Open default view if none
                if (document.querySelectorAll('.sidebar-view:not(.hidden)').length === 0) {
                    Layout.openSidebarView('view-sidebar-chats');
                }
            }
        });
    }

    if (mobileOverlay) {
        mobileOverlay.addEventListener('click', () => {
            sidebarPanel.classList.remove('active');
            mobileOverlay.classList.remove('active');
        });
    }

    // 3. Bottom Panel
    document.getElementById('toggle-bottom-panel')?.addEventListener('click', () => {
        Layout.toggleBottomPanel();
    });

    document.querySelector('[data-target="terminal-output"]')?.addEventListener('click', () => {
        // Switch bottom tab logic if we had multiple
    });


    // 4. Chat & Terminal Inputs
    const handleSend = () => {
        Chat.sendMessage(chatInput, chatContainer, editor, {
            currentProject: Files.getCurrentProject(),
            currentFilePath: Files.getCurrentFilePath(),
            images: [],
            onSessionChanged: () => Chat.loadSessions(chatSessionsList)
        });
    };
    if (sendBtn) sendBtn.addEventListener('click', (e) => { e.preventDefault(); handleSend(); });
    if (chatInput) chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
    });

    const handleTermSend = () => {
        Terminal.sendTerminalCommand(terminalInput, terminalOutput, Files.getCurrentProject(), Chat.getCurrentSessionId());
    };
    if (terminalSendBtn) terminalSendBtn.addEventListener('click', handleTermSend);
    if (terminalInput) terminalInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') handleTermSend();
    });

    // Clear Terminal
    document.getElementById('clear-terminal')?.addEventListener('click', () => {
        terminalOutput.innerHTML = '<div class="line system">Terminal Cleared.</div>';
    });


    // 5. File Actions (Run/Save)
    document.getElementById('save-file-btn')?.addEventListener('click', Files.saveFile);
    document.getElementById('run-file-btn')?.addEventListener('click', () => {
        Layout.toggleBottomPanel(true); // Open terminal
        Files.runFile(terminalOutput, () => {
            // Callback usually switches view, but we handled it by opening panel
        });
    });

    // Listen for file open events to update tabs
    // Note: Files.js calls `openView('view-editor-main')` which might fail if we don't expose it or update Files.js
    // We should patch openView mechanism globally or expose Layout.

    // Legacy support: Files.js triggers clicks on data-target items.
    // Our logic handles clicks, so it should work if Files.js finds the element.
    // But Files.js might search for `document.querySelector('[data-target="view-editor-main"]')`
    // We have that element in the DOM (the tab/view), but maybe not the trigger button in Activity Bar (we do have one).


    // 6. New Chat (Handled via Event Delegation above)

    // Mic
    document.getElementById('mic-btn')?.addEventListener('click', Voice.toggleListening);

    // Socket Events
    socket.on('response', (data) => {
        if (Terminal.getWaitingForTerminal()) {
            // Ensure terminal is visible?
            // Layout.toggleBottomPanel(true); 
            Terminal.handleTerminalResponse(data.response, terminalOutput);
            Terminal.setWaitingForTerminal(false);
        }
    });




    let hasAutoOpenedTerminal = false;

    socket.on('log_event', (data) => {
        // 1. Append to Council Log
        const councilContainer = document.getElementById('council-logs');
        if (councilContainer) {
            const entry = document.createElement('div');
            entry.className = `log-entry ${data.level || 'INFO'}`;
            let icon = '';
            if (data.level === 'ERROR') icon = '❌';
            entry.innerHTML = `<span class="timestamp">${new Date().toLocaleTimeString()}</span> ${icon} <span class="log-msg">${UI.escapeHtml(data.message)}</span>`;
            councilContainer.appendChild(entry);
            councilContainer.scrollTop = councilContainer.scrollHeight;
        }

        // 2. Mirror to Terminal
        const terminalOutput = document.getElementById('terminal-output');
        if (terminalOutput) {
            import('./modules/terminal.js').then(module => {
                // Basic formatting for log
                const time = new Date().toLocaleTimeString().split(' ')[0]; // HH:MM:SS
                const line = `[${time}] ${data.level ? data.level + ': ' : ''}${data.message || JSON.stringify(data)}`;
                module.handleTerminalResponse(line, terminalOutput);

                // Auto-open on first log if not manually toggled
                if (!hasAutoOpenedTerminal) {
                    hasAutoOpenedTerminal = true;
                    // Check if collapsed
                    const panel = document.getElementById('bottom-panel');
                    if (panel && panel.classList.contains('collapsed')) {
                        Layout.toggleBottomPanel(true); // Force open
                        // Layout scroll to bottom?
                    }
                }
            });
        }
    });

    // Default View
    Layout.openMainView('view-chat', 'Chat');

    // --- Telemetry Polling ---
    function updateTokenTelemetry() {
        fetch('/api/telemetry/tokens')
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    const usage = data.usage;
                    // Create or update badge in header toolbar
                    let badge = document.getElementById('token-usage-badge');
                    if (!badge) {
                        const toolbar = document.querySelector('.header-toolbar');
                        if (toolbar) {
                            badge = document.createElement('div');
                            badge.id = 'token-usage-badge';
                            badge.className = 'toolbar-item';
                            badge.style.fontSize = '12px';
                            badge.style.padding = '0 10px';
                            badge.style.display = 'flex';
                            badge.style.alignItems = 'center';
                            badge.style.color = 'var(--text-secondary)';
                            badge.style.borderLeft = '1px solid var(--border-color)';
                            toolbar.insertBefore(badge, toolbar.firstChild);
                        }
                    }
                    if (badge) {
                        badge.textContent = `⚡ $${usage.estimated_cost.toFixed(4)} (${usage.total_calls} calls)`;
                        badge.title = `Input: ${usage.total_input_tokens} | Output: ${usage.total_output_tokens}`;
                    }
                }
            })
            .catch(err => console.error("Telemetry error:", err));
    }

    // Update every 10 seconds
    setInterval(updateTokenTelemetry, 10000);
    updateTokenTelemetry(); // Initial call
});
