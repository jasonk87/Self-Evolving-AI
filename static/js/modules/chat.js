
// static/js/modules/chat.js
import { socket } from './socket_client.js';
import { escapeHtml, showTypingIndicator, removeTypingIndicator, showAlert, showModal } from './ui.js';
import { cyrb53, notifyIfHidden } from './utils.js';

let currentSessionId = null;
let lastResponseHash = "";
let ghostPortalTimeout = null;
let pendingChatRequest = null; // { sessionId, container } — set while awaiting an async 'chat_response'
let sessionCache = [];
let sessionListElement = null;
let sessionSelectedCallback = null;
const ACTIVE_SESSION_STORAGE_KEY = 'weebo.activeSessionId';

function persistActiveSession(sessionId) {
    try {
        if (sessionId) localStorage.setItem(ACTIVE_SESSION_STORAGE_KEY, sessionId);
        else localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
    } catch (e) {
        // Private browsing/storage-disabled environments should not break chat.
    }
}

// The /chat POST route offloads AI processing to a background task and returns
// immediately (202 accepted). The actual reply arrives later via this socket event,
// keyed on session_id so a stale reply from a since-abandoned session is ignored.
socket.on('chat_response', (data) => {
    if (!pendingChatRequest || data.session_id !== pendingChatRequest.sessionId) {
        return;
    }
    const { container } = pendingChatRequest;
    pendingChatRequest = null;

    removeTypingIndicator();
    const sendBtn = document.getElementById('send-btn');
    const cancelBtn = document.getElementById('cancel-chat-btn');
    if (sendBtn) sendBtn.disabled = false;
    cancelBtn?.classList.add('hidden');

    if (data.session_id !== currentSessionId) {
        // User navigated to a different session while this reply was in flight.
        // The message is already persisted server-side, so there's nothing to render here.
        return;
    }

    if (data.cancelled) {
        return;
    }

    document.querySelectorAll('.app-layout .thought-bubble').forEach(el => el.remove());
    document.querySelectorAll('.app-layout .message.status-log').forEach(el => el.remove());

    lastResponseHash = cyrb53(data.response);
    appendMessage(container, 'assistant', data.response, data.images, data.metadata);
    notifyIfHidden("AI Assistant", data.response);
});

export function renderChatHome(container) {
    if (!container) return;
    container.innerHTML = `
        <section class="chat-home-card">
            <div class="chat-home-orb">W</div>
            <div class="chat-home-copy">
                <span class="mission-mode-label">Normal Mode</span>
                <h1>Weebo is ready.</h1>
                <p>Ask for anything. I will handle the work, track background agents, surface approvals, and report results back here.</p>
            </div>
            <div class="chat-home-actions">
                <button class="mission-action-btn" data-target="view-mission-control">Mission</button>
                <button class="mission-action-btn secondary" data-sidebar-target="view-sidebar-approvals">Approvals</button>
            </div>
            <div class="chat-home-status-row">
                <span>Agents report back here</span>
                <span>Approvals stay visible</span>
                <span>Council is one layer down</span>
            </div>
        </section>
    `;
}

export function dismissChatHome(container) {
    const target = container || document.getElementById('chat-container');
    target?.querySelector('.chat-home-card')?.remove();
}

// Handle live browser snapshots for Ghost Mode PIP
socket.on('browser_snapshot', (data) => {
    const portal = document.getElementById('ghost-portal');
    const feed = document.getElementById('ghost-feed');
    const status = document.querySelector('.ghost-status');

    if (portal && feed && status) {
        portal.classList.remove('hidden');
        const snapshot = String(data.image || '');
        if (/^[a-z0-9+/=]+$/i.test(snapshot)) {
            feed.src = `data:image/jpeg;base64,${snapshot}`;
        } else {
            portal.classList.add('hidden');
            return;
        }

        if (data.status) {
            status.textContent = data.status;
        }

        // Auto-hide the portal after inactivity
        clearTimeout(ghostPortalTimeout);
        ghostPortalTimeout = setTimeout(() => {
            portal.classList.add('hidden');
        }, 30000); // Increased from 15s to 30s as requested
    }
});

function extractTaskActionCommands(text) {
    const commandPattern = /\/task-action\s+([^\s`]+)\s+(retry|summarize|pause)/gi;
    const found = [];
    let match;
    while ((match = commandPattern.exec(text || "")) !== null) {
        const taskId = match[1];
        const action = match[2].toLowerCase();
        const command = `/task-action ${taskId} ${action}`;
        if (!found.find(item => item.command === command)) {
            found.push({ taskId, action, command });
        }
    }
    return found;
}

function parseActionFromCommand(command) {
    const parts = (command || '').trim().split(/\s+/);
    return parts.length >= 3 ? parts[2].toLowerCase() : '';
}

function confirmTaskActionFromChip(command, onConfirm) {
    const action = parseActionFromCommand(command);
    if (action === 'pause') {
        showModal(
            'Pause Autonomous Retries',
            'This will pause autonomous retries for the mission. You can resume by issuing a retry action later.',
            onConfirm,
            false,
            true,
            'Pause Auto'
        );
        return;
    }
    onConfirm();
}

function normalizeMessageImage(image) {
    if (image && typeof image === 'object') {
        return {
            src: image.src || image.data || image.url || '',
            label: image.label || image.source || 'Tool Image'
        };
    }
    return {
        src: image || '',
        label: 'Tool Image'
    };
}

async function runTaskActionFromChip(command, container) {
    appendMessage(container, 'user', command);
    showTypingIndicator(container, 'Applying action...');

    try {
        const res = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: command,
                session_id: currentSessionId
            })
        });
        const data = await res.json();
        removeTypingIndicator();
        appendMessage(container, 'assistant', data.response || data.error || 'Action processed.');
    } catch (e) {
        removeTypingIndicator();
        appendMessage(container, 'assistant', `Failed to run action: ${e.message}`);
    }
}

export function getCurrentSessionId() {
    return currentSessionId;
}

export async function cancelPendingResponse() {
    if (!pendingChatRequest) return false;
    const sessionId = pendingChatRequest.sessionId;
    pendingChatRequest = null;
    removeTypingIndicator();
    document.getElementById('send-btn')?.removeAttribute('disabled');
    document.getElementById('cancel-chat-btn')?.classList.add('hidden');
    try {
        await fetch(`/chat/${encodeURIComponent(sessionId)}/cancel`, { method: 'POST' });
    } catch (e) {
        // The local UI is released even if the request already completed.
    }
    return true;
}

export function setCurrentSessionId(id) {
    currentSessionId = id;
    persistActiveSession(id);
}

export function getPersistedSessionId() {
    try {
        return localStorage.getItem(ACTIVE_SESSION_STORAGE_KEY) || '';
    } catch (e) {
        return '';
    }
}

export async function restoreActiveSession(container) {
    const sessionId = getPersistedSessionId();
    if (!sessionId) return false;
    const loaded = await loadChatSession(sessionId, container);
    if (!loaded) {
        setCurrentSessionId(null);
        renderChatHome(container);
    }
    return loaded;
}

async function loadSessionsLegacy(listElement, onSessionSelected) {
    if (!listElement) return;
    listElement.innerHTML = '<div class="loading">Loading chats...</div>';
    try {
        const res = await fetch('/api/sessions');
        const data = await res.json();
        if (data.success) {
            listElement.innerHTML = '';
            if (!data.sessions || data.sessions.length === 0) {
                listElement.innerHTML = '<div style="padding:10px; color:#666;">No active chats. Start a new one!</div>';
            } else {
                data.sessions.forEach(s => {
                    const el = document.createElement('div');
                    el.className = `session-item ${s.id === currentSessionId ? 'active' : ''}`;
                    el.innerHTML = `
                        <div class="session-title">${escapeHtml(s.title)}</div>
                        <div class="session-meta">
                            <span>${new Date(s.updated_at * 1000).toLocaleDateString()}</span>
                            <button type="button" class="btn-delete-session" data-id="${escapeHtml(s.id)}" aria-label="Delete chat ${escapeHtml(s.title)}">🗑️</button>
                        </div>
                    `;
                    el.addEventListener('click', () => {
                        if (onSessionSelected) onSessionSelected(s.id);
                    });

                    // Delete Handler
                    el.querySelector('.btn-delete-session').addEventListener('click', async (e) => {
                        e.stopPropagation();
                        showModal(
                            "Delete Chat",
                            `Are you sure you want to delete "${s.title}"?`,
                            async () => {
                                await fetch(`/api/sessions/${s.id}`, { method: 'DELETE' });
                                if (currentSessionId === s.id) {
                                    setCurrentSessionId(null);
                                    renderChatHome(document.getElementById('chat-container'));
                                }
                                loadSessionsLegacy(listElement, onSessionSelected); // Reload
                            },
                            true
                        );
                    });
                    listElement.appendChild(el);
                });
            }
        }
    } catch (e) {
        listElement.innerHTML = 'Error loading sessions';
    }
}

function renderSessionList() {
    if (!sessionListElement) return;

    const query = (document.getElementById('chat-session-search')?.value || '').trim().toLowerCase();
    const showEmpty = Boolean(document.getElementById('show-empty-chats')?.checked);
    const sessions = sessionCache.filter(session => {
        const title = String(session.title || 'Untitled Chat');
        const matchesQuery = !query || title.toLowerCase().includes(query);
        const isEmpty = Number(session.message_count || 0) === 0;
        return matchesQuery && (showEmpty || !isEmpty || session.id === currentSessionId);
    });

    sessionListElement.innerHTML = '';
    if (sessions.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'session-empty-state';
        empty.innerHTML = query
            ? '<strong>No matching chats</strong><span>Try a different search.</span>'
            : '<strong>No conversations yet</strong><span>Start a new chat to begin.</span>';
        sessionListElement.appendChild(empty);
        return;
    }

    sessions.forEach(session => {
        const title = String(session.title || 'Untitled Chat');
        const el = document.createElement('div');
        el.className = `session-item ${session.id === currentSessionId ? 'active' : ''}`;
        el.setAttribute('role', 'button');
        el.setAttribute('tabindex', '0');
        el.setAttribute('aria-label', `Open chat ${title}`);
        const updatedAt = Number(session.updated_at || 0);
        const dateText = updatedAt ? new Date(updatedAt * 1000).toLocaleDateString() : 'Unknown date';
        el.innerHTML = `
            <div class="session-title">${escapeHtml(title)}</div>
            <div class="session-meta">
                <span>${escapeHtml(dateText)}</span>
                <button type="button" class="btn-delete-session" data-id="${escapeHtml(session.id)}" aria-label="Delete chat ${escapeHtml(title)}">🗑️</button>
            </div>
        `;

        const openSession = () => {
            setCurrentSessionId(session.id);
            renderSessionList();
            sessionSelectedCallback?.(session.id);
        };
        el.addEventListener('click', openSession);
        el.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openSession();
            }
        });

        el.querySelector('.btn-delete-session')?.addEventListener('click', event => {
            event.stopPropagation();
            showModal(
                'Delete Chat',
                `Are you sure you want to delete "${title}"?`,
                async () => {
                    await fetch(`/api/sessions/${session.id}`, { method: 'DELETE' });
                    if (currentSessionId === session.id) {
                        setCurrentSessionId(null);
                        renderChatHome(document.getElementById('chat-container'));
                    }
                    loadSessions(sessionListElement, sessionSelectedCallback);
                },
                true
            );
        });
        sessionListElement.appendChild(el);
    });
}

export async function loadSessions(listElement, onSessionSelected) {
    if (!listElement) return;
    sessionListElement = listElement;
    sessionSelectedCallback = onSessionSelected;
    listElement.innerHTML = '<div class="loading">Loading chats...</div>';

    const searchInput = document.getElementById('chat-session-search');
    const showEmptyToggle = document.getElementById('show-empty-chats');
    if (searchInput && !searchInput.dataset.bound) {
        searchInput.addEventListener('input', renderSessionList);
        searchInput.dataset.bound = 'true';
    }
    if (showEmptyToggle && !showEmptyToggle.dataset.bound) {
        showEmptyToggle.addEventListener('change', renderSessionList);
        showEmptyToggle.dataset.bound = 'true';
    }

    try {
        const res = await fetch('/api/sessions');
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Unable to load chats');
        sessionCache = Array.isArray(data.sessions) ? data.sessions : [];
        renderSessionList();
    } catch (e) {
        listElement.innerHTML = '<div class="session-empty-state"><strong>Chats unavailable</strong><span>Try refreshing the page.</span></div>';
    }
}

export async function loadChatSession(sessionId, container) {
    setCurrentSessionId(sessionId);
    container.innerHTML = '<div class="loading">Loading history...</div>';

    try {
        const res = await fetch(`/api/sessions/${sessionId}`);
        const data = await res.json();
        container.innerHTML = '';

        if (data.success && data.session) {
            if (data.session.history && data.session.history.length > 0) {
                data.session.history.forEach(msg => {
                    appendMessage(container, msg.role, msg.content, msg.images, msg.metadata);
                });
            } else {
                renderChatHome(container);
            }
            return true;
        }
        setCurrentSessionId(null);
        container.innerHTML = '';
        return false;
    } catch (e) {
        container.innerHTML = 'Error loading chat history.';
        return false;
    }
}

export function appendMessage(container, role, text, images = null, metadata = null) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;
    const messageText = String(text ?? '');
    msgDiv.dataset.rawText = messageText;
    let avatarText = role === 'user' ? '👤' : 'AI';

    let imagesHtml = '';
    if (images && images.length > 0) {
        images.forEach((image, index) => {
            const normalizedImage = normalizeMessageImage(image);
            let src = normalizedImage.src;
            if (!src) return;
            if (!src.startsWith('data:image')) {
                src = `data:image/png;base64,${src}`;
            }
            if (!/^data:image\/(?:png|jpeg|jpg|gif|webp);base64,[a-z0-9+/=]+$/i.test(src)) {
                return;
            }

            if (role === 'assistant') {
                const label = escapeHtml(normalizedImage.label || 'Tool Image');
                imagesHtml += `
                    <div class="assistant-live-feed-card ${index === 0 ? 'primary' : ''}">
                        <div class="assistant-live-feed-label">${label}</div>
                        <img src="${src}" class="assistant-live-feed-image" alt="${label} preview">
                    </div>
                `;
            } else {
                imagesHtml += `<div class="user-uploaded-image"><img src="${src}" style="max-width: 200px; border-radius: 10px; margin-bottom: 5px;"></div>`;
            }
        });
    }

    let parts = messageText.split(/(```html-dynamic[\s\S]*?```)/g);
    let finalHtml = "";

    parts.forEach(part => {
        if (part.startsWith("```html-dynamic") && part.endsWith("```")) {
            let rawHtml = part.replace(/^```html-dynamic\s*/, "").replace(/```$/, "");
            let cleanHtml = (typeof DOMPurify !== 'undefined') ? DOMPurify.sanitize(rawHtml) : "<i>(DOMPurify missing)</i>";
            finalHtml += `<div class="dynamic-html-wrapper">${cleanHtml}</div>`;
        } else {
            if (typeof marked !== 'undefined') {
                const renderedMarkdown = marked.parse(part);
                finalHtml += (typeof DOMPurify !== 'undefined')
                    ? DOMPurify.sanitize(renderedMarkdown)
                    : escapeHtml(part).replace(/\n/g, '<br>');
            } else {
                finalHtml += escapeHtml(part).replace(/\n/g, '<br>');
            }
        }
    });

    const actionCommands = role === 'assistant' ? extractTaskActionCommands(messageText) : [];
    const actionChipsHtml = actionCommands.length > 0
        ? `<div class="task-action-chips">${actionCommands.map(item => `<button class="task-action-chip" data-command="${escapeHtml(item.command)}">${item.action.toUpperCase()} · ${escapeHtml(item.taskId.slice(0, 8))}</button>`).join('')}</div>`
        : '';

    const modelInfo = metadata?.model || {};
    const memorySources = Array.isArray(metadata?.memory?.rag_sources)
        ? metadata.memory.rag_sources.filter(source => source && source.text)
        : [];
    const modelLabel = modelInfo.provider || modelInfo.model
        ? `${modelInfo.provider || 'provider'}${modelInfo.model ? ` · ${modelInfo.model}` : ''}`
        : '';
    const metadataHtml = role === 'assistant' && metadata && (modelLabel || metadata.memory)
        ? `<details class="message-context-details">
                <summary>Execution details${modelLabel ? ` · ${escapeHtml(modelLabel)}` : ''}</summary>
                <div class="message-context-body">
                    ${modelLabel ? `<span>Model: ${escapeHtml(modelLabel)}</span>` : ''}
                    <span>Memory retrieved: ${memorySources.length}</span>
                    ${memorySources.length ? `<ul>${memorySources.map(source => `<li>${escapeHtml(source.text)}</li>`).join('')}</ul>` : ''}
                </div>
           </details>`
        : '';
    const messageActions = role === 'assistant' && messageText.trim()
        ? `<div class="message-actions">
                <button type="button" class="message-action-btn" data-message-action="copy">Copy</button>
                <button type="button" class="message-action-btn" data-message-action="retry">Retry</button>
           </div>`
        : '';

    msgDiv.innerHTML = `<div class="avatar">${avatarText}</div><div class="content">${imagesHtml}${finalHtml}${actionChipsHtml}${metadataHtml}${messageActions}</div>`;
    
    // Apply Highlight.js and Copy Buttons
    msgDiv.querySelectorAll('pre code').forEach((block) => {
        if (typeof hljs !== 'undefined') {
            hljs.highlightElement(block);
        }
        
        const pre = block.parentElement;
        pre.style.position = 'relative';
        
        const copyBtn = document.createElement('button');
        copyBtn.className = 'btn-copy-code';
        copyBtn.innerHTML = 'Copy';
        copyBtn.addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText(block.innerText);
                copyBtn.innerHTML = 'Copied!';
                copyBtn.classList.add('copied');
                setTimeout(() => {
                    copyBtn.innerHTML = 'Copy';
                    copyBtn.classList.remove('copied');
                }, 2000);
            } catch (err) {
                console.error('Failed to copy text: ', err);
            }
        });
        pre.appendChild(copyBtn);
    });

    container.appendChild(msgDiv);

    if (actionCommands.length > 0) {
        msgDiv.querySelectorAll('.task-action-chip').forEach(btn => {
            btn.addEventListener('click', () => confirmTaskActionFromChip(btn.dataset.command, () => runTaskActionFromChip(btn.dataset.command, container)));
        });
    }

    const copyButton = msgDiv.querySelector('[data-message-action="copy"]');
    copyButton?.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(messageText);
            copyButton.textContent = 'Copied';
            setTimeout(() => { copyButton.textContent = 'Copy'; }, 1500);
        } catch (e) {
            copyButton.textContent = 'Copy failed';
        }
    });

    const retryButton = msgDiv.querySelector('[data-message-action="retry"]');
    retryButton?.addEventListener('click', () => {
        const userMessages = [...container.querySelectorAll('.message.user')];
        const previousUser = userMessages[userMessages.length - 1];
        const input = document.getElementById('chat-input');
        if (!previousUser || !input) return;
        input.value = previousUser.dataset.rawText || '';
        input.focus();
        document.getElementById('send-btn')?.click();
    });

    container.scrollTop = container.scrollHeight;

}

export async function sendMessage(inputEl, container, editor, contextData = {}) {
    const message = inputEl.value.trim();
    const images = contextData.images || [];

    if (!message && images.length === 0) return;

    // Disable send controls while in-flight so the user can't double-submit
    const sendBtn = document.getElementById('send-btn');
    const cancelBtn = document.getElementById('cancel-chat-btn');
    if (sendBtn) sendBtn.disabled = true;
    if (cancelBtn) {
        cancelBtn.classList.remove('hidden');
        cancelBtn.onclick = cancelPendingResponse;
    }

    // The welcome card is useful only before the conversation starts. Remove
    // it at the moment the user submits their first message so it never
    // competes with the actual conversation on a small screen.
    dismissChatHome(container);

    // Display
    appendMessage(container, 'user', message, [...images]);
    inputEl.value = '';

    // Prepare images
    let imagesToSend = images.map(img => {
        if (img.includes(',')) return img.split(',')[1];
        return img;
    });

    showTypingIndicator(container);

    // Context from editor
    let context = {};
    if (editor && contextData.currentFilePath) {
        context.currentFile = {
            path: contextData.currentFilePath,
            project: contextData.currentProject,
            content: editor.getValue()
        };
    }
    if (contextData.terminalOutput) {
        context.terminalOutput = contextData.terminalOutput;
    }

    try {
        const res = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message,
                images: imagesToSend,
                context: context,
                session_id: currentSessionId
            })
        });
        const data = await res.json();

        // Update Session ID if new (applies whether the reply is sync or deferred)
        if (data.session_id && currentSessionId !== data.session_id) {
            setCurrentSessionId(data.session_id);
            if (contextData.onSessionChanged) contextData.onSessionChanged();
        }

        if (res.status === 202 && data.accepted) {
            // AI work was handed off to a background task. Leave the typing
            // indicator and disabled send button up until 'chat_response' fires.
            pendingChatRequest = { sessionId: data.session_id, container };
            return;
        }

        removeTypingIndicator();

        if (data.success || data.response) {
            // Cleanup ephemeral
            document.querySelectorAll('.app-layout .thought-bubble').forEach(el => el.remove());
            document.querySelectorAll('.app-layout .message.status-log').forEach(el => el.remove());

            lastResponseHash = cyrb53(data.response);
            appendMessage(container, 'assistant', data.response, data.images, data.metadata);
            notifyIfHidden("AI Assistant", data.response);

            if (sendBtn) sendBtn.disabled = false;
            cancelBtn?.classList.add('hidden');
            return data.response;
        }

        if (sendBtn) sendBtn.disabled = false;
        cancelBtn?.classList.add('hidden');
    } catch (e) {
        removeTypingIndicator();
        appendMessage(container, 'assistant', 'Error sending.');
        if (sendBtn) sendBtn.disabled = false;
        cancelBtn?.classList.add('hidden');
    }
}
