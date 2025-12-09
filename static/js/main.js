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

            // Load files if Files tab is selected
            if (tabName === 'files') {
                loadProjectsAndFiles();
            }
        });
    });

    // File Tree & Editor Logic
    const fileTreeContainer = document.getElementById('file-tree');
    const editorPanel = document.getElementById('editor-panel');
    const chatPanel = document.getElementById('chat-panel');
    const fileEditor = document.getElementById('file-editor');
    const editorFilename = document.getElementById('editor-filename');
    const closeEditorBtn = document.getElementById('close-editor-btn');
    const saveFileBtn = document.getElementById('save-file-btn');

    let currentProject = null;
    let currentFilePath = null;

    async function loadProjectsAndFiles() {
        if (!fileTreeContainer) return;
        fileTreeContainer.innerHTML = '<div class="loading">Loading projects...</div>';

        try {
            const response = await fetch('/api/projects');
            const data = await response.json();

            if (data.success && data.projects) {
                fileTreeContainer.innerHTML = '';
                if (data.projects.length === 0) {
                    fileTreeContainer.innerHTML = '<div class="empty-state">No projects found.</div>';
                    return;
                }

                const projectsList = document.createElement('ul');
                projectsList.className = 'file-tree-list';

                for (const project of data.projects) {
                    const li = document.createElement('li');
                    li.className = 'project-item';

                    const projectLabel = document.createElement('div');
                    projectLabel.className = 'tree-label project-label';
                    projectLabel.innerHTML = `<span class="icon">📁</span> ${project.name}`;
                    projectLabel.onclick = (e) => toggleProject(e, project.name, li);

                    li.appendChild(projectLabel);
                    projectsList.appendChild(li);
                }
                fileTreeContainer.appendChild(projectsList);
            } else {
                fileTreeContainer.innerHTML = '<div class="error-state">Failed to load projects.</div>';
            }
        } catch (error) {
            console.error('Error loading projects:', error);
            fileTreeContainer.innerHTML = '<div class="error-state">Error loading projects.</div>';
        }
    }

    async function toggleProject(event, projectName, parentLi) {
        event.stopPropagation();
        const existingList = parentLi.querySelector('ul');
        if (existingList) {
            existingList.remove(); // Collapse
            parentLi.classList.remove('expanded');
            return;
        }

        // Expand
        parentLi.classList.add('expanded');
        // Fetch files for project root
        await fetchAndRenderFiles(projectName, '', parentLi);
    }

    async function fetchAndRenderFiles(projectName, path, parentContainer) {
        // Add loading indicator?

        try {
            const url = `/api/files/list?project_name=${encodeURIComponent(projectName)}&path=${encodeURIComponent(path)}`;
            const response = await fetch(url);
            const data = await response.json();

            if (data.success) {
                const ul = document.createElement('ul');
                ul.className = 'file-tree-sublist';

                // Directories
                data.directories.forEach(dir => {
                    const li = document.createElement('li');
                    li.className = 'dir-item';
                    const dirLabel = document.createElement('div');
                    dirLabel.className = 'tree-label dir-label';
                    dirLabel.innerHTML = `<span class="icon">📂</span> ${dir}`;
                    const dirPath = path ? `${path}/${dir}` : dir;

                    dirLabel.onclick = (e) => toggleDirectory(e, projectName, dirPath, li);

                    li.appendChild(dirLabel);
                    ul.appendChild(li);
                });

                // Files
                data.files.forEach(file => {
                    const li = document.createElement('li');
                    li.className = 'file-item';
                    const fileLabel = document.createElement('div');
                    fileLabel.className = 'tree-label file-label';
                    fileLabel.innerHTML = `<span class="icon">📄</span> ${file}`;
                    const filePath = path ? `${path}/${file}` : file;

                    fileLabel.onclick = (e) => openFileInEditor(projectName, filePath);

                    li.appendChild(fileLabel);
                    ul.appendChild(li);
                });

                parentContainer.appendChild(ul);
            } else {
                console.error('Failed to list files:', data.error);
            }
        } catch (error) {
            console.error('Error fetching files:', error);
        }
    }

    async function toggleDirectory(event, projectName, dirPath, parentLi) {
        event.stopPropagation();
        const existingList = parentLi.querySelector('ul');
        if (existingList) {
            existingList.remove();
            parentLi.classList.remove('expanded');
            return;
        }
        parentLi.classList.add('expanded');
        await fetchAndRenderFiles(projectName, dirPath, parentLi);
    }

    async function openFileInEditor(projectName, filePath) {
        try {
            const url = `/api/files/read?project_name=${encodeURIComponent(projectName)}&path=${encodeURIComponent(filePath)}`;
            const response = await fetch(url);
            const data = await response.json();

            if (data.success) {
                currentProject = projectName;
                currentFilePath = filePath;
                editorFilename.textContent = `${projectName}/${filePath}`;
                fileEditor.value = data.content;

                // Show editor, hide chat
                chatPanel.classList.add('hidden');
                editorPanel.classList.remove('hidden');
            } else {
                alert(`Error opening file: ${data.error}`);
            }
        } catch (error) {
            console.error('Error reading file:', error);
            alert('Error reading file.');
        }
    }

    if (closeEditorBtn) {
        closeEditorBtn.addEventListener('click', () => {
            editorPanel.classList.add('hidden');
            chatPanel.classList.remove('hidden');
            currentProject = null;
            currentFilePath = null;
        });
    }

    if (saveFileBtn) {
        saveFileBtn.addEventListener('click', async () => {
            if (!currentProject || !currentFilePath) return;

            const content = fileEditor.value;
            const originalBtnText = saveFileBtn.textContent;
            saveFileBtn.textContent = 'Saving...';
            saveFileBtn.disabled = true;

            try {
                const response = await fetch('/api/files/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        project_name: currentProject,
                        path: currentFilePath,
                        content: content
                    })
                });
                const data = await response.json();

                if (data.success) {
                    // Feedback
                    saveFileBtn.textContent = 'Saved!';
                    setTimeout(() => {
                        saveFileBtn.textContent = originalBtnText;
                        saveFileBtn.disabled = false;
                    }, 1500);
                } else {
                    alert(`Error saving file: ${data.error}`);
                    saveFileBtn.textContent = originalBtnText;
                    saveFileBtn.disabled = false;
                }
            } catch (error) {
                console.error('Error saving file:', error);
                alert('Error saving file.');
                saveFileBtn.textContent = originalBtnText;
                saveFileBtn.disabled = false;
            }
        });
    }

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
