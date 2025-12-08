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
        // Find or create the telemetry view in the context panel
        let telemetryView = document.querySelector('#tab-telemetry .telemetry-view');
        if (!telemetryView) {
             // If tab-telemetry content doesn't exist, we might need to handle it or it's handled by HTML structure
             // Assuming I will add the HTML structure in the next step.
             const tabContent = document.getElementById('tab-telemetry');
             if (tabContent) {
                 telemetryView = document.createElement('div');
                 telemetryView.classList.add('telemetry-view');
                 tabContent.innerHTML = ''; // Clear empty state
                 tabContent.appendChild(telemetryView);
             }
        }

        if (telemetryView) {
            // Update the view with the new data
            // We can format it nicely
            const projectBlock = document.getElementById(`telemetry-${data.project}`);
            let htmlContent = `<h3>${data.project}</h3>`;
            htmlContent += `<pre>${JSON.stringify(data.telemetry, null, 2)}</pre>`;

            if (projectBlock) {
                projectBlock.innerHTML = htmlContent;
            } else {
                const newBlock = document.createElement('div');
                newBlock.id = `telemetry-${data.project}`;
                newBlock.classList.add('telemetry-block');
                newBlock.innerHTML = htmlContent;
                telemetryView.appendChild(newBlock);
            }
        }
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
});
