
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
// Expose socket globally so non-module scripts (mission_control.js, approvals.js,
// cortex.js, council.js, quarantine.js) can register socket event listeners.
window.socket = socket;

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
        const navButton = e.target.closest('.mission-action-btn[data-target], .text-link-btn[data-target]');
        if (navButton) {
            const target = navButton.dataset.target;
            const navItem = document.querySelector(`.mobile-nav-item[data-target="${target}"], .toolbar-item[data-target="${target}"], .main-tab[data-target="${target}"]`);
            if (navItem) {
                navItem.click();
                return;
            }
        }

        const sidebarButton = e.target.closest('.mission-action-btn[data-sidebar-target], .text-link-btn[data-sidebar-target]');
        if (sidebarButton) {
            const target = sidebarButton.dataset.sidebarTarget;
            const navItem = document.querySelector(`.activity-item[data-target="${target}"], .mobile-sidebar-tab[data-target="${target}"]`);
            if (navItem) {
                navItem.click();
                return;
            }
        }

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
    const imageUploadBtn = document.getElementById('image-upload-btn');
    const imageUploadInput = document.getElementById('image-upload-input');
    const imagePreviewContainer = document.getElementById('image-preview-container');
    const terminalOutput = document.getElementById('terminal-output');
    const terminalInput = document.getElementById('terminal-input');
    const terminalSendBtn = document.getElementById('terminal-send-btn');
    const editorContainer = document.getElementById('editor-container');
    const chatSessionsList = document.getElementById('chat-sessions-list');
    const fileTreeContainer = document.getElementById('file-tree');

    // Helper to sync sidebar tab active state on mobile
    function syncSidebarTabState(targetId) {
        document.querySelectorAll('.mobile-sidebar-tab').forEach(tab => {
            if (tab.dataset.target === targetId) {
                tab.classList.add('active');
            } else {
                tab.classList.remove('active');
            }
        });
    }

    // Helper to load sidebar content dynamically
    const loadSidebarData = (target) => {
        if (target === 'view-sidebar-files') {
            Files.loadProjects(fileTreeContainer, () => {
                const path = Files.getCurrentFilePath();
                const filename = path ? path.split('/').pop() : 'Editor';
                Layout.openMainView('view-editor-main', filename);
            });
        }
        if (target === 'view-sidebar-chats') {
            Chat.loadSessions(chatSessionsList, (sessionId) => {
                Chat.loadChatSession(sessionId, chatContainer);
                Layout.openMainView('view-chat', 'Chat'); // Switch to stage
            });
        }
        if (target === 'view-sidebar-memory') {
            Memory.loadMemory(
                document.getElementById('memory-list'),
                document.getElementById('episodes-list'),
                (sessionId) => {
                    Chat.loadChatSession(sessionId, chatContainer);
                    Layout.openMainView('view-chat', 'Chat');
                }
            );
        }
    };

    // --- Layout State Management ---
    const Layout = {
        currentMainView: 'view-chat',

        openSidebarView: (viewId) => {
            // 1. Show Panel
            sidebarPanel.classList.remove('collapsed');
            sidebarPanel.classList.add('active'); // for mobile

            // 2. Switch Content with Fade
            sidebarViews.forEach(v => {
                if (v.id !== viewId) {
                    v.classList.remove('fade-active');
                    // Reduced timeout slightly to feel snappier
                    setTimeout(() => v.classList.add('hidden'), 250);
                }
            });

            const view = document.getElementById(viewId);
            if (view) {
                // Remove hidden immediately, then fade in next frame
                view.classList.remove('hidden');
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        view.classList.add('fade-active');
                    });
                });
            }

            // 3. Highlight Activity Icon
            document.querySelectorAll('.activity-item').forEach(i => i.classList.remove('active'));
            const trigger = document.querySelector(`.activity-item[data-target="${viewId}"]`);
            if (trigger) trigger.classList.add('active');

            // 4. Sync mobile sidebar tabs
            syncSidebarTabState(viewId);
        },

        closeSidebar: () => {
            sidebarPanel.classList.add('collapsed');
            sidebarPanel.classList.remove('active');
            document.querySelectorAll('.activity-item').forEach(i => i.classList.remove('active'));
            // Keep Chat icon active if we are in chat? No, they are separate now.
        },

        openMainView: (viewId, tabLabel = "View") => {
            Layout.currentMainView = viewId;

            // 1. Switch View Container with Fade
            mainViews.forEach(v => {
                if (v.id !== viewId) {
                    v.classList.remove('fade-active');
                    setTimeout(() => v.classList.remove('active'), 300);
                }
            });

            const view = document.getElementById(viewId);
            if (view) {
                view.classList.add('active');
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        view.classList.add('fade-active');
                    });
                });
            }

            // 2. Update Tabs
            Layout.updateMainTabs(viewId, tabLabel);
            Layout.syncMainNavigation(viewId);

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
                mainTabsHeader.appendChild(targetTab);
            }
            targetTab.classList.add('active');
        },

        syncMainNavigation: (activeViewId) => {
            document.querySelectorAll('.toolbar-item[data-target], .mobile-settings-btn[data-target]').forEach(item => {
                item.classList.toggle('active', item.dataset.target === activeViewId);
            });

            document.querySelectorAll('.mobile-nav-item[data-target]').forEach(item => {
                item.classList.toggle('active', item.dataset.target === activeViewId);
            });
        },

        toggleBottomPanel: (forceState = null) => {
            if (forceState === true) bottomPanel.classList.remove('collapsed');
            else if (forceState === false) bottomPanel.classList.add('collapsed');
            else bottomPanel.classList.toggle('collapsed');

            const mobileTerminalBtn = document.getElementById('mobile-terminal-btn');
            if (mobileTerminalBtn) {
                if (bottomPanel.classList.contains('collapsed')) {
                    mobileTerminalBtn.classList.remove('active');
                } else {
                    mobileTerminalBtn.classList.add('active');
                }
            }
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
                    loadSidebarData(target);
                }
            }
        });
    });

    // 1b. Toolbar Navigation (Top Right)
    document.querySelectorAll('.toolbar-item[data-target], .mobile-settings-btn[data-target]').forEach(item => {
        item.addEventListener('click', () => {
            const target = item.dataset.target;
            const label = item.getAttribute('title');
            if (target) {
                const nextTarget = Layout.currentMainView === target && target !== 'view-chat'
                    ? 'view-chat'
                    : target;
                const nextLabel = nextTarget === 'view-chat' ? 'Chat' : label;

                Layout.openMainView(nextTarget, nextLabel);

                if (nextTarget === 'view-settings') {
                    Settings.loadConfig();
                }
            }
        });
    });


    if (mainTabsHeader) {
        mainTabsHeader.addEventListener('click', (event) => {
            const tab = event.target.closest('.main-tab[data-target]');
            if (!tab) return;

            if (event.target.classList.contains('close-tab')) {
                tab.remove();
                Layout.openMainView('view-chat', 'Chat');
                return;
            }

            const target = tab.dataset.target;
            const label = tab.querySelector('span')?.textContent || 'View';
            if (target) {
                Layout.openMainView(target, label);
            }
        });
    }


    // 1c. Mobile Chat/Home Button
    if (mobileChatBtn) {
        mobileChatBtn.addEventListener('click', () => {
            Layout.openMainView('view-chat', 'Chat');
        });
    }

    // 2. Mobile Menu & Navigation
    const mobileMenuBtn = document.getElementById('mobile-menu-btn');
    const mobileMenuBtnBottom = document.getElementById('mobile-menu-btn-bottom');
    const mobileOverlay = document.getElementById('mobile-overlay');

    const toggleMobileMenu = () => {
        sidebarPanel.classList.toggle('active'); // Mobile drawer
        mobileOverlay.classList.toggle('active');

        // If opening, ensure sidebar is visible (not collapsed width)
        if (sidebarPanel.classList.contains('active')) {
            sidebarPanel.classList.remove('collapsed');
            // Open default view if none
            if (document.querySelectorAll('.sidebar-view:not(.hidden)').length === 0) {
                Layout.openSidebarView('view-sidebar-chats');
                loadSidebarData('view-sidebar-chats');
            }
        }
    };

    if (mobileMenuBtn) mobileMenuBtn.addEventListener('click', toggleMobileMenu);
    if (mobileMenuBtnBottom) mobileMenuBtnBottom.addEventListener('click', toggleMobileMenu);

    if (mobileOverlay) {
        mobileOverlay.addEventListener('click', () => {
            sidebarPanel.classList.remove('active');
            mobileOverlay.classList.remove('active');
        });
    }

    // Mobile Bottom Nav items (Chat, Mission, Cortex)
    document.querySelectorAll('.mobile-nav-item[data-target]').forEach(item => {
        item.addEventListener('click', () => {
            const targetId = item.dataset.target;
            const label = item.querySelector('span')?.textContent || 'View';

            if (targetId) {
                Layout.openMainView(targetId, label);
                Layout.toggleBottomPanel(false); // Collapsed on view switch
            }
        });
    });

    // Mobile Terminal btn
    const mobileTerminalBtn = document.getElementById('mobile-terminal-btn');
    if (mobileTerminalBtn) {
        mobileTerminalBtn.addEventListener('click', () => {
            Layout.toggleBottomPanel();
        });
    }

    // Mobile Sidebar tabs switcher
    document.querySelectorAll('.mobile-sidebar-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.dataset.target;
            if (target) {
                Layout.openSidebarView(target);
                loadSidebarData(target);
            }
        });
    });

    // 3. Bottom Panel
    document.getElementById('toggle-bottom-panel')?.addEventListener('click', () => {
        Layout.toggleBottomPanel();
    });

    document.querySelector('[data-target="terminal-output"]')?.addEventListener('click', () => {
        // Switch bottom tab logic if we had multiple
    });


    // 4. Chat & Terminal Inputs
    let selectedImages = [];

    const renderSelectedImages = () => {
        if (!imagePreviewContainer) return;
        imagePreviewContainer.innerHTML = '';
        if (selectedImages.length === 0) {
            imagePreviewContainer.classList.add('hidden');
            return;
        }

        imagePreviewContainer.classList.remove('hidden');
        selectedImages.forEach((src, index) => {
            const item = document.createElement('div');
            item.className = 'image-preview-item';
            item.innerHTML = `
                <img src="${src}" alt="Selected image ${index + 1}">
                <button class="image-preview-remove" type="button" aria-label="Remove image" data-index="${index}">×</button>
            `;
            imagePreviewContainer.appendChild(item);
        });
    };

    const readSelectedImageFiles = async (files) => {
        const imageFiles = Array.from(files || []).filter(file => file.type.startsWith('image/'));
        const readers = imageFiles.map(file => new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
        }));
        const loaded = await Promise.all(readers);
        selectedImages = selectedImages.concat(loaded).slice(0, 6);
        renderSelectedImages();
    };

    if (imageUploadBtn && imageUploadInput) {
        imageUploadBtn.addEventListener('click', (e) => {
            e.preventDefault();
            imageUploadInput.click();
        });
        imageUploadInput.addEventListener('change', async () => {
            await readSelectedImageFiles(imageUploadInput.files);
            imageUploadInput.value = '';
        });
    }

    if (imagePreviewContainer) {
        imagePreviewContainer.addEventListener('click', (e) => {
            const removeBtn = e.target.closest('.image-preview-remove');
            if (!removeBtn) return;
            const index = Number(removeBtn.dataset.index);
            if (!Number.isNaN(index)) {
                selectedImages.splice(index, 1);
                renderSelectedImages();
            }
        });
    }

    const handleSend = () => {
        Chat.sendMessage(chatInput, chatContainer, editor, {
            currentProject: Files.getCurrentProject(),
            currentFilePath: Files.getCurrentFilePath(),
            images: selectedImages,
            // Pass a full onSessionSelected callback so the refreshed session list
            // remains clickable (previously passed no callback, making list items dead).
            onSessionChanged: () => Chat.loadSessions(chatSessionsList, (sessionId) => {
                Chat.loadChatSession(sessionId, chatContainer);
                Layout.openMainView('view-chat', 'Chat');
            })
        }).then((response) => {
            if (response !== undefined) {
                selectedImages = [];
                renderSelectedImages();
            }
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

    // 7. Live / Ghost Mode toggle (LIVE UPLINK button + settings checkbox)
    let liveMode = false;
    const liveToggleBtn = document.getElementById('live-toggle-btn');
    const ghostModeCheckbox = document.getElementById('setting-ghost-mode');

    // Keep both controls in sync with the current state
    const syncLiveControls = () => {
        if (liveToggleBtn) {
            liveToggleBtn.textContent = liveMode ? 'DISCONNECT' : 'LIVE UPLINK';
            liveToggleBtn.classList.toggle('active', liveMode);
        }
        if (ghostModeCheckbox) ghostModeCheckbox.checked = liveMode;
    };

    // Shared toggle action
    const applyLiveMode = async (desiredState) => {
        liveMode = desiredState;
        if (liveToggleBtn) liveToggleBtn.disabled = true;
        if (ghostModeCheckbox) ghostModeCheckbox.disabled = true;
        try {
            const res = await fetch('/toggle_live_mode', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ active: liveMode })
            });
            const data = await res.json();
            if (!data.success) {
                liveMode = !liveMode; // revert on failure
                UI.showAlert('Live Mode Error', data.error || 'Failed to toggle live mode.');
            }
        } catch (e) {
            liveMode = !liveMode;
            UI.showAlert('Live Mode Error', e.message);
        } finally {
            if (liveToggleBtn) liveToggleBtn.disabled = false;
            if (ghostModeCheckbox) ghostModeCheckbox.disabled = false;
            syncLiveControls();
        }
    };

    // Sync with server state on load
    fetch('/get_live_status')
        .then(r => r.json())
        .then(d => { liveMode = d.status === 'active'; syncLiveControls(); })
        .catch(() => {});

    if (liveToggleBtn) {
        liveToggleBtn.addEventListener('click', () => applyLiveMode(!liveMode));
    }
    if (ghostModeCheckbox) {
        ghostModeCheckbox.addEventListener('change', () => applyLiveMode(ghostModeCheckbox.checked));
    }

    // 8. Add Fact button (Memory sidebar)
    document.getElementById('add-fact-btn')?.addEventListener('click', async () => {
        const text = window.prompt('Enter a fact to save to memory:');
        if (!text || !text.trim()) return;
        try {
            const res = await fetch('/api/memory/facts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: text.trim() })
            });
            const data = await res.json();
            if (data.success) {
                Memory.loadMemory(
                    document.getElementById('memory-list'),
                    document.getElementById('episodes-list'),
                    (sid) => {
                        Chat.loadChatSession(sid, chatContainer);
                        Layout.openMainView('view-chat', 'Chat');
                    }
                );
            } else {
                UI.showAlert('Memory Error', data.error || 'Failed to save fact.');
            }
        } catch (e) {
            UI.showAlert('Memory Error', e.message);
        }
    });

    // 9. Close File button (Editor header → returns to chat view)
    document.getElementById('close-file-btn')?.addEventListener('click', () => {
        Layout.openMainView('view-chat', 'Chat');
    });

    // Memory Tabs Navigation
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('tab-btn')) {
            const targetTab = e.target.dataset.tab;
            if (targetTab) {
                const parent = e.target.closest('.memory-tabs');
                if (parent) {
                    parent.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
                    e.target.classList.add('active');
                }
                const wrapper = e.target.closest('.sidebar-view');
                if (wrapper) {
                    wrapper.querySelectorAll('.memory-tab-content').forEach(content => {
                        content.classList.remove('active');
                        content.classList.add('hidden');
                    });
                    const activeContent = document.getElementById(targetTab);
                    if (activeContent) {
                        activeContent.classList.remove('hidden');
                        activeContent.classList.add('active');
                    }
                }
            }
        }
    });

    // Mic
    document.getElementById('mic-btn')?.addEventListener('click', Voice.toggleListening);

    // Voice Toggle
    const voiceToggleBtn = document.getElementById('voice-toggle-btn');
    if (voiceToggleBtn) {
        voiceToggleBtn.addEventListener('click', () => {
            const enabled = !Voice.getAutoSpeak();
            Voice.setAutoSpeak(enabled);
            voiceToggleBtn.classList.toggle('active', enabled);
            voiceToggleBtn.setAttribute('title', `Auto-Speech (${enabled ? 'On' : 'Off'})`);
        });
    }


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


    socket.on('assistant_alert', (data) => {
        try {
            const message = data?.message || '⚠️ System alert received.';
            const alertSessionId = data?.session_id || null;

            if (alertSessionId && Chat.getCurrentSessionId() === alertSessionId) {
                Chat.appendMessage(chatContainer, 'assistant', message);
            } else {
                UI.showAlert('AI System Warning', 'A background mission failed. Open chat for details.');
                if (chatContainer) {
                    Chat.appendMessage(chatContainer, 'assistant', message);
                }
            }

            notifyIfHidden('AI System Warning', message);
            Chat.loadSessions(chatSessionsList, (sid) => {
                Chat.loadChatSession(sid, chatContainer);
                Layout.openMainView('view-chat', 'Chat');
            });
        } catch (alertErr) {
            console.error('[System] assistant_alert handling failed:', alertErr);
        }
    });

    socket.on('agent_message', (data) => {
        try {
            const message = data?.content || data?.message || 'Background agent completed.';
            const targetSessionId = data?.session_id || null;

            if (targetSessionId && Chat.getCurrentSessionId() === targetSessionId) {
                Chat.appendMessage(chatContainer, 'assistant', message);
            } else {
                UI.showAlert('Agent Report', 'A background agent finished. Open chat for details.');
                if (targetSessionId) {
                    Chat.loadChatSession(targetSessionId, chatContainer);
                    Layout.openMainView('view-chat', 'Chat');
                }
            }

            notifyIfHidden('Agent Report', message);
            Chat.loadSessions(chatSessionsList, (sid) => {
                Chat.loadChatSession(sid, chatContainer);
            });
        } catch (agentErr) {
            console.error('[System] agent_message handling failed:', agentErr);
        }
    });

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
                        // Layout.toggleBottomPanel(true); // Force open
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
                            badge.className = 'toolbar-item token-badge';
                            badge.style.width = 'auto';
                            badge.style.whiteSpace = 'nowrap';
                            badge.style.fontSize = '12px';
                            badge.style.padding = '0 10px';
                            badge.style.display = 'flex';
                            badge.style.alignItems = 'center';
                            badge.style.color = 'var(--text-secondary)';
                            badge.style.borderLeft = '1px solid var(--border-color)';
                            badge.style.cursor = 'pointer';
                            badge.addEventListener('click', () => {
                                // Trigger token modal
                                if (window.openTokenModal) {
                                    window.openTokenModal();
                                }
                            });
                            toolbar.insertBefore(badge, toolbar.firstChild);
                        }
                    }
                    if (badge) {
                        const thinking = usage.total_thinking_tokens || 0;
                        badge.textContent = `Tokens $${usage.estimated_cost.toFixed(4)} (${usage.total_calls} calls)`;
                        badge.title = `Input: ${usage.total_input_tokens} | Output: ${usage.total_output_tokens} | Thinking: ${thinking} (Click for Breakdown)`;
                    }
                }
            })
            .catch(err => console.error("Telemetry error:", err));
    }

    // Update every 10 seconds
    setInterval(updateTokenTelemetry, 10000);
    updateTokenTelemetry(); // Initial call
});
