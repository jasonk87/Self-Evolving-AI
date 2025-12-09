document.addEventListener('DOMContentLoaded', () => {
    const chatHistory = document.getElementById('chat-history');
    const userInput = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');
    const contextPanel = document.getElementById('context-panel');
    const toggleContext = document.getElementById('toggle-context');

    // Initialize SocketIO
    // Assuming socket.io.js is loaded and 'io' is available globally
    const socket = io();

    socket.on('connect', () => {
        console.log('Connected to WebSocket server');
        appendSystemMessage('Connected to real-time event stream.');
    });

    socket.on('log_event', (data) => {
        console.log('Log Event:', data);
        // If we want to show logs in the "Terminal" tab, we can do it here.
        // For now, let's just log to console or maybe append to the terminal view if it exists.
        const terminalView = document.querySelector('#tab-terminal .terminal-view');
        if (terminalView && data.message) {
            const line = document.createElement('div');
            line.classList.add('line');
            line.textContent = `[${new Date().toLocaleTimeString()}] ${data.message}`;
            terminalView.appendChild(line);
            terminalView.scrollTop = terminalView.scrollHeight;
        }
    });

    socket.on('project_update', (data) => {
        console.log('Project Update:', data);
        updateTelemetryUI(data);
    });

    socket.on('disconnect', () => {
        console.log('Disconnected from WebSocket server');
        appendSystemMessage('Disconnected from event stream.');
    });

    // Auto-resize textarea
    userInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = (this.scrollHeight) + 'px';
    });

    // Handle Enter key
    userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    sendBtn.addEventListener('click', sendMessage);

    async function sendMessage() {
        const message = userInput.value.trim();
        if (!message) return;

        // Add user message
        appendMessage('user', message);
        userInput.value = '';
        userInput.style.height = 'auto';

        // Show typing indicator
        const loadingId = appendSystemMessage('Thinking...');

        try {
            const response = await fetch('/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ message: message })
            });

            const data = await response.json();

            // Remove loading message
            const loadingMsg = document.getElementById(loadingId);
            if (loadingMsg) loadingMsg.remove();

            if (data.success) {
                appendMessage('ai', data.response);
            } else {
                appendMessage('error', data.error || data.response || "An error occurred.");
            }

        } catch (error) {
            console.error('Error:', error);
            const loadingMsg = document.getElementById(loadingId);
            if (loadingMsg) loadingMsg.remove();
            appendMessage('error', 'Network error or server unavailable.');
        }
    }

    function appendMessage(sender, text) {
        const msgDiv = document.createElement('div');
        msgDiv.classList.add('message', `${sender}-message`);

        const bubble = document.createElement('div');
        bubble.classList.add('bubble');
        // Simple formatting for newlines
        bubble.innerText = text;

        msgDiv.appendChild(bubble);
        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function appendSystemMessage(text) {
        const id = 'sys-' + Date.now();
        const msgDiv = document.createElement('div');
        msgDiv.classList.add('message', 'system-message');
        msgDiv.id = id;

        const bubble = document.createElement('div');
        bubble.classList.add('bubble');
        bubble.innerText = text;

        msgDiv.appendChild(bubble);
        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
        return id;
    }

    // Context Panel Toggle
    if (toggleContext && contextPanel) {
        toggleContext.addEventListener('click', () => {
            contextPanel.classList.toggle('collapsed');
            toggleContext.innerText = contextPanel.classList.contains('collapsed') ? '‹' : '›';
        });
    }

    // Tabs
    const tabs = document.querySelectorAll('.tab-btn');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            const tabName = tab.dataset.tab;
            document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
            const target = document.getElementById(`tab-${tabName}`);
            if (target) {
                target.classList.remove('hidden');
            }
        });
    });

    function updateTelemetryUI(payload) {
        // Payload: { project: "ProjectName", data: { ... } }
        // We will display this in a dedicated section in the right sidebar.
        // If "Telemetry" tab doesn't exist, we can add it or repurpose an existing one.
        // For now, let's update the "Memory" tab or create a new "Telemetry" tab dynamically if possible,
        // or just append to a specific container if we update the HTML.
        // But since we can't easily change HTML structure from here without being invasive,
        // let's try to find a container or create one.

        // Let's use the 'tab-memory' for Telemetry/Game State for now, or add a new tab if we want to follow requirements strictly.
        // Requirement: "Update a new "Game State" or "Telemetry" section in the Right Sidebar"

        let telemetryTabBtn = document.querySelector('.tab-btn[data-tab="telemetry"]');
        if (!telemetryTabBtn) {
            // Create Telemetry Tab Button
            const tabsContainer = document.querySelector('.tabs');
            telemetryTabBtn = document.createElement('button');
            telemetryTabBtn.classList.add('tab-btn');
            telemetryTabBtn.dataset.tab = 'telemetry';
            telemetryTabBtn.textContent = 'Telemetry';
            telemetryTabBtn.addEventListener('click', () => {
                document.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active'));
                telemetryTabBtn.classList.add('active');
                document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
                document.getElementById('tab-telemetry').classList.remove('hidden');
            });
            tabsContainer.appendChild(telemetryTabBtn);

            // Create Telemetry Tab Content
            const contextPanel = document.getElementById('context-panel');
            const telemetryContent = document.createElement('div');
            telemetryContent.id = 'tab-telemetry';
            telemetryContent.classList.add('tab-content', 'hidden');

            const header = document.createElement('h3');
            header.textContent = 'Live Project Telemetry';
            telemetryContent.appendChild(header);

            const contentArea = document.createElement('div');
            contentArea.id = 'telemetry-data-area';
            telemetryContent.appendChild(contentArea);

            contextPanel.appendChild(telemetryContent);
        }

        // Update Content
        const contentArea = document.getElementById('telemetry-data-area');
        if (contentArea) {
            // simple JSON pretty print for now, or formatted key-values
            contentArea.innerHTML = '';

            const projectHeader = document.createElement('div');
            projectHeader.style.fontWeight = 'bold';
            projectHeader.style.marginBottom = '10px';
            projectHeader.textContent = `Project: ${payload.project}`;
            contentArea.appendChild(projectHeader);

            const dataList = document.createElement('ul');
            dataList.style.listStyle = 'none';
            dataList.style.padding = '0';

            for (const [key, value] of Object.entries(payload.data)) {
                const li = document.createElement('li');
                li.style.marginBottom = '5px';
                li.innerHTML = `<span style="opacity: 0.7;">${key}:</span> <strong>${value}</strong>`;
                dataList.appendChild(li);
            }
            contentArea.appendChild(dataList);

            // Flash effect to show update
            contentArea.style.transition = 'background-color 0.2s';
            contentArea.style.backgroundColor = 'rgba(255, 255, 255, 0.1)';
            setTimeout(() => {
                contentArea.style.backgroundColor = 'transparent';
            }, 200);
        }
    }
});
