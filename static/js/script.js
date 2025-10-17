// Globally scoped constants for DOM elements
// These will be assigned once DOMContentLoaded fires.
let chatLogArea = null;
let userInput = null;
let sendButton = null;
let suggestionsList = null;
let activeTasksList = null;
let reflectionsList = null;

document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded and parsed. Initializing script logic.");

    chatLogArea = document.getElementById('chatLogArea');
    userInput = document.getElementById('userInput');
    sendButton = document.getElementById('sendButton');
    suggestionsList = document.getElementById('suggestionsList');
    activeTasksList = document.getElementById('activeTasksList');
    reflectionsList = document.getElementById('reflectionsList');
    const controlPanelToggle = document.getElementById('controlPanelToggle');
    const controlPanel = document.querySelector('.control-panel');

    if (controlPanelToggle && controlPanel) {
        controlPanelToggle.addEventListener('click', () => {
            controlPanel.classList.toggle('expanded');
            if (controlPanel.classList.contains('expanded')) {
                fetchAndDisplaySuggestions();
                fetchAndDisplayActiveTasks();
                fetchAndDisplayReflections();
            }
        });
    }

    if (sendButton && userInput) {
        sendButton.addEventListener('click', sendMessage);
        userInput.addEventListener('keypress', function(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                sendMessage();
            }
        });
    }
});

async function fetchAndDisplaySuggestions() {
    try {
        const response = await fetch('/api/suggestions?status=pending');
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${response.statusText}`);
        }
        const suggestions = await response.json();
        renderSuggestions(suggestions);
    } catch (error) {
        console.error("Failed to fetch suggestions:", error);
    }
}

function renderSuggestions(suggestions) {
    if (!suggestionsList) return;
    suggestionsList.innerHTML = '';
    suggestions.forEach(suggestion => {
        const listItem = document.createElement('li');
        listItem.className = 'suggestion-item';
        listItem.innerHTML = `
            <h5>${suggestion.title || 'Suggestion'}</h5>
            <p>${suggestion.description}</p>
            <div class="suggestion-buttons">
                <button class="approve-btn" data-id="${suggestion.suggestion_id}">Approve</button>
                <button class="deny-btn" data-id="${suggestion.suggestion_id}">Deny</button>
            </div>
        `;
        suggestionsList.appendChild(listItem);
    });

    document.querySelectorAll('.approve-btn').forEach(button => {
        button.addEventListener('click', (e) => {
            const suggestionId = e.target.dataset.id;
            handleSuggestionAction(suggestionId, 'approve');
        });
    });

    document.querySelectorAll('.deny-btn').forEach(button => {
        button.addEventListener('click', (e) => {
            const suggestionId = e.target.dataset.id;
            handleSuggestionAction(suggestionId, 'deny');
        });
    });
}

async function handleSuggestionAction(suggestionId, action) {
    const reason = prompt(`Enter a reason for ${action}ing this suggestion (optional):`);
    try {
        const response = await fetch(`/api/suggestions/${suggestionId}/${action}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ reason: reason }),
        });
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${response.statusText}`);
        }
        fetchAndDisplaySuggestions(); // Refresh the list
    } catch (error) {
        console.error(`Failed to ${action} suggestion:`, error);
    }
}

async function fetchAndDisplayActiveTasks() {
    try {
        const response = await fetch('/api/status/active_tasks');
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${response.statusText}`);
        }
        const tasks = await response.json();
        renderActiveTasks(tasks);
    } catch (error) {
        console.error("Failed to fetch active tasks:", error);
    }
}

function renderActiveTasks(tasks) {
    if (!activeTasksList) return;
    activeTasksList.innerHTML = '';
    tasks.forEach(task => {
        const listItem = document.createElement('li');
        listItem.className = 'task-item';
        listItem.innerHTML = `
            <h5>${task.description}</h5>
            <p>Status: ${task.status}</p>
        `;
        activeTasksList.appendChild(listItem);
    });
}

async function fetchAndDisplayReflections() {
    try {
        const response = await fetch('/api/reflections');
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${response.statusText}`);
        }
        const reflections = await response.json();
        renderReflections(reflections);
    } catch (error) {
        console.error("Failed to fetch reflections:", error);
    }
}

function renderReflections(reflections) {
    if (!reflectionsList) return;
    reflectionsList.innerHTML = '';
    reflections.forEach(reflection => {
        const listItem = document.createElement('li');
        listItem.className = 'reflection-item';
        listItem.innerHTML = `
            <h5>${reflection.goal_description}</h5>
            <p>Status: ${reflection.status}</p>
        `;
        reflectionsList.appendChild(listItem);
    });
}

function appendToChatLog(text, sender) {
    if (!chatLogArea) return;

    // Remove the welcome message if it exists
    const welcomeMessage = document.querySelector('.welcome-message');
    if (welcomeMessage) {
        welcomeMessage.remove();
    }

    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message', `${sender}-message`);
    messageDiv.textContent = text;
    chatLogArea.appendChild(messageDiv);
    chatLogArea.scrollTop = chatLogArea.scrollHeight;
}

async function sendMessage() {
    if (!userInput) return;
    const messageText = userInput.value.trim();
    if (messageText === '') return;

    appendToChatLog(messageText, 'user');
    userInput.value = '';

    try {
        const response = await fetch('/chat_api', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', },
            body: JSON.stringify({
                message: messageText,
                user_id: "user_static_test_01"
            }),
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.chat_response || `Server error: ${response.status} ${response.statusText}`);
        }

        if (data.chat_response) {
            appendToChatLog(data.chat_response, 'ai');
        }

    } catch (error) {
        console.error('Failed to send message or parse response:', error);
        appendToChatLog(`Error: ${error.message}`, 'ai');
    }
}
