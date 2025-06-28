// Globally scoped constants for DOM elements
// These will be assigned once DOMContentLoaded fires.
let chatLogArea = null;
let userInput = null;
let sendButton = null;
let aiCoreStatusText = null;
let processingIndicator = null;
let aiCoreDisplay = null;
let matrixScrollEffect = null;
let helpButton = null;
let helpMenuPopup = null;
let statusPanel = null;
let statusPanelToggle = null;
let activeTasksList = null;
let refreshActiveTasksBtn = null;
let recentNotificationsList = null;
let refreshNotificationsBtn = null;
let projectDisplayArea = null;
let projectDisplayLoadingIndicator = null; // New global for loading indicator


let idleAnimationTimeoutId = null;
let isWeiboWorkingInBackground = false;
let matrixAnimationId = null;
let matrixColumns = [];

const MATRIX_CHARACTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ﾊﾐﾋｰｳｼﾅﾓﾆｻﾜﾂｵﾘｱﾎﾃﾏｹﾒｴｶｷﾑﾕﾗｾﾈｽﾀﾇﾍｦｲｸｺｿﾁﾄﾉﾌﾔﾖﾙﾚﾛﾝ";
const MATRIX_COLUMN_FONT_SIZE = 10;
const MATRIX_COLUMN_WIDTH = 15;
const MATRIX_SPAWN_INTERVAL = 100;
let lastMatrixSpawnTime = 0;


function setWeiboState(state) {
    if (!processingIndicator) {
        console.error("setWeiboState: processingIndicator not found!");
        return;
    }

    processingIndicator.classList.remove('idle', 'weibo-working-idle', 'weibo-thinking', 'weibo-talking', 'weibo-in-work-zone');
    processingIndicator.style.transform = '';
    processingIndicator.style.top = '';
    processingIndicator.style.left = '';
    processingIndicator.style.bottom = '';
    processingIndicator.style.right = '';

    if (matrixScrollEffect) {
        if (state === 'background-processing') {
            matrixScrollEffect.style.display = 'block';
            startMatrixAnimation();
        } else {
            matrixScrollEffect.style.display = 'none';
            stopMatrixAnimation();
        }
    }

    switch (state) {
        case 'idle':
            processingIndicator.classList.add('idle');
            startIdleAnimation();
            break;
        case 'background-processing':
            processingIndicator.classList.add('weibo-in-work-zone');
            processingIndicator.classList.add('weibo-working-idle');
            stopIdleAnimation();
            break;
        case 'weibo-thinking':
            processingIndicator.classList.add('weibo-thinking');
            stopIdleAnimation();
            break;
        case 'weibo-talking':
            processingIndicator.classList.add('weibo-talking');
            stopIdleAnimation();
            break;
        default:
            console.warn("Unknown Weibo state requested:", state, ". Reverting to idle.");
            processingIndicator.classList.add('idle');
            startIdleAnimation();
            break;
    }
}

async function fetchAndDisplayInteractiveTasks() {
    if (!chatLogArea) return;

    appendToChatLog("Fetching active tasks...", 'system-help');
    setWeiboState('weibo-thinking');

    try {
        const response = await fetch('/api/status/active_tasks'); // Using existing endpoint for active tasks
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ error: `HTTP error ${response.status}: ${response.statusText}` }));
            throw new Error(errorData.error || `Failed to fetch tasks. Server responded with ${response.status}`);
        }
        const tasks = await response.json();

        if (tasks && tasks.length > 0) {
            const tasksContainer = document.createElement('div');
            tasksContainer.className = 'tasks-list-container interactive-list'; // For styling

            let htmlContent = `<div class="tasks-header">Found ${tasks.length} active task(s):</div>`;
            tasks.forEach((task) => {
                const descSnippet = (task.description || 'N/A').substring(0, 150) +
                                    ((task.description && task.description.length > 150) ? '...' : '');
                const reasonText = task.status_reason ? `<div class="task-reason">Reason: ${task.status_reason}</div>` : '';
                const currentStepText = task.current_step_description ? `<div class="task-current-step">Current Step: ${task.current_step_description}</div>` : '';

                let actionButtonsHTML = `
                    <button class="view-task-plan-btn" data-task-id="${task.task_id}">View Plan</button>
                    <button class="archive-task-btn" data-task-id="${task.task_id}">Archive</button>`;

                if (task.status && task.status.toLowerCase() !== 'completed' && task.status.toLowerCase() !== 'failed' && task.status.toLowerCase() !== 'archived') {
                    actionButtonsHTML += ` <button class="complete-task-btn" data-task-id="${task.task_id}">Mark Complete</button>`;
                }

                htmlContent += `
                    <div class="task-item" id="task-item-${task.task_id}">
                        <div class="task-id">ID: ${task.task_id || 'N/A'}</div>
                        <div class="task-type">Type: ${task.task_type || 'N/A'}</div>
                        <div class="task-description">Desc: ${descSnippet}</div>
                        <div class="task-status">Status: <span class="status-value">${task.status || 'N/A'}</span></div>
                        ${currentStepText}
                        ${reasonText}
                        <div class="task-actions">
                            ${actionButtonsHTML}
                        </div>
                    </div>`;
            });
            tasksContainer.innerHTML = htmlContent;

            const messageDiv = document.createElement('div');
            messageDiv.classList.add('message', 'ai-message', 'interactive-list');
            messageDiv.appendChild(tasksContainer);
            chatLogArea.appendChild(messageDiv);
            chatLogArea.scrollTop = chatLogArea.scrollHeight;

        } else {
            appendToChatLog('No active tasks found.', 'ai');
        }
    } catch (error) {
        console.error("Failed to fetch interactive tasks:", error);
        appendToChatLog(`Error fetching tasks: ${error.message}`, 'ai');
    } finally {
        setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle');
    }
}

async function fetchAndShowProject(taskId) {
    if (!chatLogArea || !projectDisplayArea) return;

    appendToChatLog(`Fetching output for project task ${taskId}...`, 'system-help');
    setWeiboState('weibo-thinking');

    if (projectDisplayLoadingIndicator && projectDisplayArea) {
        toggleProjectDisplay(true); // Ensure area is visible
        projectDisplayLoadingIndicator.style.display = 'flex'; // Show loading indicator
    }

    try {
        const response = await fetch(`/api/project_output/${taskId}`);
        const data = await response.json(); // Try to parse JSON first, as backend sends structured errors/success

        if (!response.ok) {
            throw new Error(data.error || `Failed to fetch project output. Server responded with ${response.status}`);
        }

        if (data.success && data.html_content) {
            appendToChatLog(`Displaying project: ${data.project_name || taskId}`, 'ai');
            // toggleProjectDisplay(true); // Already called above if indicator was shown
            const iframe = projectDisplayArea.querySelector('iframe#projectDisplayIframe');
            if (iframe) {
                iframe.srcdoc = data.html_content;
            } else {
                console.error("Project display iframe not found!");
                appendToChatLog("Error: Could not find the project display area to show the content.", 'ai');
            }
        } else {
            appendToChatLog(data.error || `Could not retrieve displayable output for project task ${taskId}.`, 'ai');
        }
    } catch (error) {
        console.error(`Failed to fetch or display project ${taskId}:`, error);
        appendToChatLog(`Error fetching project ${taskId}: ${error.message}`, 'ai');
    } finally {
        if (projectDisplayLoadingIndicator) {
            // Use a small timeout to ensure content has a chance to render, similar to sendMessage
            setTimeout(() => {
                projectDisplayLoadingIndicator.style.display = 'none';
            }, 100);
        }
        setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle');
    }
}


async function handleTaskAction(taskId, actionType, params = {}) {
    if (!chatLogArea) return;

    let apiUrl = '';
    let method = 'GET';
    let body = null;
    const actionTypeDisplay = actionType.replace('_', ' '); // For messages

    appendToChatLog(`${actionTypeDisplay.charAt(0).toUpperCase() + actionTypeDisplay.slice(1)} task ${taskId}...`, 'system-help');
    setWeiboState('weibo-thinking');

    switch (actionType) {
        case 'view_plan':
            apiUrl = `/api/tasks/${taskId}/plan`;
            method = 'GET';
            break;
        case 'complete':
            apiUrl = `/api/tasks/${taskId}/complete`;
            method = 'POST';
            body = JSON.stringify({ reason: params.reason });
            break;
        case 'archive':
            apiUrl = `/api/tasks/${taskId}/archive`;
            method = 'POST';
            body = JSON.stringify({ reason: params.reason });
            break;
        default:
            console.error("Invalid task action type:", actionType);
            appendToChatLog(`Error: Unknown task action '${actionType}'.`, 'ai');
            setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle');
            return;
    }

    try {
        const fetchOptions = {
            method: method,
            headers: {
                'Content-Type': 'application/json', // Needed even for GET if server expects it for errors
            }
        };
        if (body) {
            fetchOptions.body = body;
        }

        const response = await fetch(apiUrl, fetchOptions);
        const data = await response.json(); // Attempt to parse JSON for all responses

        if (!response.ok) {
            throw new Error(data.error || `Server error ${response.status} for ${actionTypeDisplay}`);
        }

        if (data.success) {
            let successMessage = data.message || `Task ${actionTypeDisplay} successful.`;
            if (actionType === 'view_plan' && data.plan) {
                // Format and display the plan
                let planText = `Plan for Task ${taskId}:\n`;
                if (Array.isArray(data.plan) && data.plan.length > 0) {
                    data.plan.forEach((step, index) => {
                        if (typeof step === 'string') {
                            planText += `${index + 1}. ${step}\n`;
                        } else if (typeof step === 'object' && step.description) { // Assuming step objects
                            planText += `${index + 1}. ${step.description}\n`;
                        }
                    });
                } else if (typeof data.plan === 'string') { // Simple plan string
                    planText = data.plan;
                } else {
                    planText += "No detailed plan steps available or plan is in an unrecognized format.";
                }
                appendToChatLog(planText, 'ai');
            } else {
                appendToChatLog(successMessage, 'ai');
            }

            // UI Update for complete/archive
            if (actionType === 'complete' || actionType === 'archive') {
                const taskItemElement = document.getElementById(`task-item-${taskId}`);
                if (taskItemElement) {
                    const statusElement = taskItemElement.querySelector('.task-status .status-value');
                    if (statusElement && data.task && data.task.status) { // If backend returns updated task
                        statusElement.textContent = data.task.status;
                    } else { // Generic update if no specific task data returned for archive/complete
                        statusElement.textContent = actionType.charAt(0).toUpperCase() + actionType.slice(1); // e.g., "Completed"
                    }

                    // Remove all action buttons for completed/archived tasks
                    const actionButtonsElement = taskItemElement.querySelector('.task-actions');
                    if (actionButtonsElement) {
                        actionButtonsElement.innerHTML = `<span class="task-action-finalized">(${actionTypeDisplay}d)</span>`;
                    }
                }
            }
        } else {
            appendToChatLog(data.error || `Failed to ${actionTypeDisplay} task.`, 'ai');
        }

    } catch (error) {
        console.error(`Error during task ${actionType} for ${taskId}:`, error);
        appendToChatLog(`Error: ${error.message}`, 'ai');
    } finally {
        setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle');
    }
}


async function handleSuggestionAction(suggestionId, action, reason) {
    if (!chatLogArea) return;
    if (!['approve', 'deny'].includes(action)) {
        console.error("Invalid action for suggestion:", action);
        return;
    }

    const apiUrl = `/api/suggestions/${suggestionId}/${action}`;
    const systemMessage = `${action.charAt(0).toUpperCase() + action.slice(1)}ing suggestion ${suggestionId}...`;
    appendToChatLog(systemMessage, 'system-help');
    setWeiboState('weibo-thinking');

    try {
        const response = await fetch(apiUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ reason: reason }),
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || `Server error ${response.status}`);
        }

        if (data.success) {
            appendToChatLog(data.message || `Suggestion ${action}d successfully.`, 'ai');
            // Update the UI for the specific suggestion
            const suggestionItemElement = document.getElementById(`suggestion-item-${suggestionId}`);
            if (suggestionItemElement) {
                const statusElement = suggestionItemElement.querySelector('.suggestion-status .status-value');
                if (statusElement && data.suggestion && data.suggestion.status) {
                    statusElement.textContent = data.suggestion.status;
                }
                // Remove action buttons as it's no longer pending
                const actionButtonsElement = suggestionItemElement.querySelector('.suggestion-actions');
                if (actionButtonsElement) {
                    actionButtonsElement.remove();
                }
                // Optionally, add reason if it was updated
                if (data.suggestion && data.suggestion.reason) {
                    let reasonDiv = suggestionItemElement.querySelector('.suggestion-reason');
                    if (!reasonDiv) {
                        reasonDiv = document.createElement('div');
                        reasonDiv.className = 'suggestion-reason';
                        // Insert after status or at the end of details
                        statusElement.closest('.suggestion-status').insertAdjacentElement('afterend', reasonDiv);
                    }
                    reasonDiv.textContent = `Reason: ${data.suggestion.reason}`;
                }
            }
        } else {
            appendToChatLog(data.error || `Failed to ${action} suggestion.`, 'ai');
        }

    } catch (error) {
        console.error(`Error during suggestion ${action}:`, error);
        appendToChatLog(`Error: ${error.message}`, 'ai');
    } finally {
        setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle');
    }
}

function appendToChatLog(text, sender) {
    if (!chatLogArea) return;
    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message');

    if (sender === 'user') {
        messageDiv.classList.add('user-message');
        messageDiv.textContent = text;
        chatLogArea.appendChild(messageDiv);
    } else if (sender === 'ai') {
        messageDiv.classList.add('ai-message');
        messageDiv.classList.add('typing');

        const contentSpan = document.createElement('span');
        contentSpan.classList.add('ai-message-content');
        messageDiv.appendChild(contentSpan);
        chatLogArea.appendChild(messageDiv);

        let i = 0;
        const typingSpeed = 30;

        function typeCharacter() {
            if (i < text.length) {
                contentSpan.textContent += text.charAt(i);
                i++;
                if (i % 10 === 0 || i === text.length) { // Update scroll less frequently
                    chatLogArea.scrollTop = chatLogArea.scrollHeight;
                }
                setTimeout(typeCharacter, typingSpeed);
            } else {
                messageDiv.classList.remove('typing');
                // Ensure scroll to bottom one last time after typing finishes
                chatLogArea.scrollTop = chatLogArea.scrollHeight;
                if (aiCoreStatusText) aiCoreStatusText.textContent = 'Response complete. Standing by.';
                setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle');
            }
        }
        typeCharacter();
    } else if (sender === 'system-help') {
        messageDiv.classList.add('system-help-message');
        messageDiv.textContent = text; // No typing animation for help text
        chatLogArea.appendChild(messageDiv);
        // No need to change Weibo state for system-help messages
    }
    // Ensure scroll to bottom for user and system-help messages immediately
    if (sender === 'user' || sender === 'system-help') {
        chatLogArea.scrollTop = chatLogArea.scrollHeight;
    }
}

async function sendMessage() {
    if (!userInput || !aiCoreStatusText) return;
    const messageText = userInput.value.trim();
    if (messageText === '') return;

    // Client-side command handling
    const lowerMessageText = messageText.toLowerCase();

    if (lowerMessageText === '/toggle_project_display') {
        appendToChatLog(messageText, 'user');
        userInput.value = '';
        window.toggleProjectDisplay();
        return;
    }

    const suggestionsListMatch = lowerMessageText.match(/^\/suggestions list (pending|approved|denied|all)$/);
    if (suggestionsListMatch) {
        appendToChatLog(messageText, 'user');
        userInput.value = '';
        const status = suggestionsListMatch[1];
        fetchAndDisplaySuggestions(status);
        return;
    }

    if (lowerMessageText === '/tasks list') {
        appendToChatLog(messageText, 'user');
        userInput.value = '';
        fetchAndDisplayInteractiveTasks();
        return;
    }

    const showProjectMatch = lowerMessageText.match(/^\/show_project\s+([\w-]+)$/);
    if (showProjectMatch) {
        appendToChatLog(messageText, 'user');
        userInput.value = '';
        const taskId = showProjectMatch[1];
        fetchAndShowProject(taskId);
        return;
    }

    appendToChatLog(messageText, 'user');
    userInput.value = '';

    setWeiboState('weibo-thinking');
    aiCoreStatusText.textContent = 'Processing directive...';

    try {
        const response = await fetch('/chat_api', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', },
            body: JSON.stringify({
                message: messageText,
                user_id: "user_static_test_01"
            }),
        });

        // Data structure from backend: {success: bool, chat_response: str|null, project_area_html: str|null}
        const data = await response.json();

        setWeiboState('weibo-talking'); // Start "talking" animation

        if (!response.ok) { // HTTP error (e.g., 500 from server, network issue already caught by outer catch)
            let errorMsg = data.chat_response || `Server error: ${response.status} ${response.statusText}`;
            appendToChatLog(errorMsg, 'ai'); // This will handle state change after typing
            if (aiCoreStatusText) aiCoreStatusText.textContent = 'Error processing directive.';
            return;
        }

        // Handle project area HTML first
        if (data.project_area_html && projectDisplayArea && projectDisplayLoadingIndicator) {
            toggleProjectDisplay(true); // Show project display area if not already visible

            projectDisplayLoadingIndicator.style.display = 'flex'; // Show loading indicator

            const iframe = projectDisplayArea.querySelector('iframe#projectDisplayIframe');
            if (iframe) {
                // Setting srcdoc is quite fast. For more complex loading,
                // an iframe.onload event might be used, but can be tricky with srcdoc.
                iframe.srcdoc = data.project_area_html;
                // Hide indicator shortly after. If content is very complex and takes time to render,
                // this might hide too soon. A more robust solution might involve postMessage from iframe.
                setTimeout(() => {
                    projectDisplayLoadingIndicator.style.display = 'none';
                }, 100); // Small delay to allow immediate rendering pass
            } else {
                console.warn("#projectDisplayIframe not found. Cannot display project content.");
                projectDisplayLoadingIndicator.style.display = 'none'; // Hide indicator if iframe fails
                appendToChatLog("Error: Could not find the project display iframe.", 'ai');
            }
        } else if (data.project_area_html && (!projectDisplayArea || !projectDisplayLoadingIndicator)) {
            console.error("Project display area or loading indicator not found, cannot show project_area_html.");
        }
        // This handles the case where project mode was active but AI sends no new HTML.
        // No change to loading indicator here as no new content is being loaded.
        else if (!data.project_area_html && document.body.classList.contains('project-mode-active')) {
            // This condition implies: no new project HTML from AI, but project mode was previously active.
            // We might want to leave it open if the user manually opened it.
            // Or, if AI explicitly "closes" a project, it should send a null/empty project_area_html
            // and potentially a chat message indicating closure.
            // For now, if AI sends no project HTML, we don't automatically close the display
            // if it was already open. The user can close it with the toggle.
            // However, if the AI's response *implies* closure, then:
            // toggleProjectDisplay(false);
            // For now, this path does nothing to the display if it's already open.
        } else if (data.project_area_html && !projectDisplayArea) { // Error case
            console.error("Project display area HTML received, but #projectDisplayArea element not found!");
        }


        // Handle chat response
        if (data.chat_response) {
            appendToChatLog(data.chat_response, 'ai');
            // appendToChatLog (for AI) now handles setting aiCoreStatusText and reverting Weibo state
        } else if (data.success === false) {
            // Operation failed backend-side, but no specific chat response from AI
            appendToChatLog('An operation failed, but no specific message was returned.', 'ai');
        } else if (!data.chat_response && !data.project_area_html && data.success === true) {
            // Successful operation but no output for chat or project area (e.g., a silent tool success)
            appendToChatLog('Request processed successfully with no specific output.', 'ai');
        }
        // If only project_area_html was provided and no chat_response,
        // appendToChatLog won't be called for 'ai', so we need to ensure Weibo state resets.
        if (!data.chat_response && (data.project_area_html || data.success)) {
             if (aiCoreStatusText) aiCoreStatusText.textContent = 'Directive processed. Standing by.';
             setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle');
        }


    } catch (error) { // Catches network errors for fetch, or JSON parsing errors
        console.error('Failed to send message or parse response:', error);
        // Ensure Weibo talking animation stops and state resets
        setWeiboState('idle'); // Or background-processing if applicable
        if (aiCoreStatusText) aiCoreStatusText.textContent = 'Connection Error or Invalid Response.';
        appendToChatLog(`Error: ${error.message}`, 'ai');
    }
}

function animateIdleWeibo() {
    if (!processingIndicator || !aiCoreDisplay) {
        return;
    }
    if (!processingIndicator.classList.contains('idle')) {
        return;
    }

    const parentRect = aiCoreDisplay.getBoundingClientRect();
    const baseIndicatorWidth = 80;
    const baseIndicatorHeight = 80;

    if (!parentRect || parentRect.width === 0 || parentRect.height === 0) {
         idleAnimationTimeoutId = setTimeout(animateIdleWeibo, 5000);
         return;
    }

    const randomScale = 0.7 + Math.random() * 0.6;
    const currentScaledWidth = baseIndicatorWidth * randomScale;
    const currentScaledHeight = baseIndicatorHeight * randomScale;

    const maxX = parentRect.width - currentScaledWidth;
    const maxY = parentRect.height - currentScaledHeight;

    let targetX = Math.random() * Math.max(0, maxX);
    let targetY = Math.random() * Math.max(0, maxY);

    targetX = Math.max(0, Math.min(targetX, maxX));
    targetY = Math.max(0, Math.min(targetY, maxY));

    // console.log(`Animating Idle (top/left + scale): Scale=${randomScale.toFixed(2)}, TargetX=${targetX.toFixed(2)}, TargetY=${targetY.toFixed(2)} (maxX:${maxX.toFixed(2)}, maxY:${maxY.toFixed(2)}) ScaledW:${currentScaledWidth.toFixed(2)}`);

    processingIndicator.style.left = `${targetX}px`;
    processingIndicator.style.top = `${targetY}px`;
    processingIndicator.style.transform = `scale(${randomScale})`;

    const randomDelay = 3000 + Math.random() * 4000;
    idleAnimationTimeoutId = setTimeout(animateIdleWeibo, randomDelay);
}

function startIdleAnimation() {
    stopIdleAnimation();
    setTimeout(() => {
        if (processingIndicator && aiCoreDisplay) {
            const currentDisplayState = getCurrentWeiboState();
            if (currentDisplayState === 'idle') {
                animateIdleWeibo();
            }
        }
    }, 100);
}
function stopIdleAnimation() {
    clearTimeout(idleAnimationTimeoutId);
    idleAnimationTimeoutId = null;
}

function isIdleAnimationRunning() {
    return idleAnimationTimeoutId !== null;
}

function getCurrentWeiboState() {
    if (!processingIndicator) return null;
    if (processingIndicator.classList.contains('weibo-thinking')) return 'weibo-thinking';
    if (processingIndicator.classList.contains('weibo-talking')) return 'weibo-talking';
    if (processingIndicator.classList.contains('weibo-in-work-zone')) return 'background-processing';
    if (processingIndicator.classList.contains('idle')) return 'idle';
    return null;
}

function toggleBackgroundWork() {
    isWeiboWorkingInBackground = !isWeiboWorkingInBackground;
    const currentLogicalState = getCurrentWeiboState();

    if (currentLogicalState === 'idle' || currentLogicalState === 'background-processing') {
        setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle');
    }

    if (aiCoreStatusText) {
        aiCoreStatusText.textContent = isWeiboWorkingInBackground ? "Weibo is processing background tasks..." : "AI Core Systems Nominal";
    }
    console.log("Background work toggled:", isWeiboWorkingInBackground);
}

// --- Matrix Animation Logic ---
// ... (Matrix functions remain unchanged) ...
function createMatrixColumn() {
    if (!matrixScrollEffect || !aiCoreDisplay) return null;
    const column = document.createElement('div');
    column.classList.add('matrix-column');
    const parentWidth = aiCoreDisplay.clientWidth;
    column.style.left = `${Math.random() * (parentWidth - MATRIX_COLUMN_WIDTH)}px`;
    column.style.top = `-${Math.random() * 200}px`;
    column.style.opacity = '0';
    const streamLength = 10 + Math.floor(Math.random() * 20);
    for (let i = 0; i < streamLength; i++) {
        const char = MATRIX_CHARACTERS[Math.floor(Math.random() * MATRIX_CHARACTERS.length)];
        const span = document.createElement('span');
        span.textContent = char;
        if (i === 0) span.classList.add('matrix-highlight');
        column.appendChild(span);
    }
    matrixScrollEffect.appendChild(column);
    matrixColumns.push(column);
    setTimeout(() => { column.style.opacity = '0.3'; }, 50);
    return column;
}

function animateMatrix(timestamp) {
    if (!matrixScrollEffect || matrixScrollEffect.style.display === 'none') {
        matrixAnimationId = null;
        return;
    }
    if (timestamp - lastMatrixSpawnTime > MATRIX_SPAWN_INTERVAL) {
        if (matrixColumns.length < 50) createMatrixColumn();
        lastMatrixSpawnTime = timestamp;
    }
    const parentHeight = aiCoreDisplay.clientHeight;
    for (let i = matrixColumns.length - 1; i >= 0; i--) {
        const column = matrixColumns[i];
        let top = parseFloat(column.style.top || 0);
        top += 1 + Math.random() * 2;
        if (top > parentHeight) {
            column.remove();
            matrixColumns.splice(i, 1);
        } else {
            column.style.top = `${top}px`;
            const spans = column.getElementsByTagName('span');
            if (spans.length > 0 && Math.random() < 0.05) {
                 const charIndexToChange = Math.floor(Math.random() * spans.length);
                 spans[charIndexToChange].textContent = MATRIX_CHARACTERS[Math.floor(Math.random() * MATRIX_CHARACTERS.length)];
                 if (charIndexToChange === 0) spans[charIndexToChange].classList.add('matrix-highlight');
            }
        }
    }
    matrixAnimationId = requestAnimationFrame(animateMatrix);
}

function startMatrixAnimation() {
    if (matrixAnimationId || !matrixScrollEffect) return;
    matrixScrollEffect.style.display = 'block';
    lastMatrixSpawnTime = performance.now();
    matrixAnimationId = requestAnimationFrame(animateMatrix);
}

function stopMatrixAnimation() {
    if (matrixAnimationId) cancelAnimationFrame(matrixAnimationId);
    matrixAnimationId = null;
    if (matrixScrollEffect) {
        matrixScrollEffect.innerHTML = '';
        matrixScrollEffect.style.display = 'none';
    }
    matrixColumns = [];
}

async function fetchAndDisplaySuggestions(status = 'all') {
    if (!chatLogArea) return;

    let apiUrl = '/api/suggestions';
    if (status && status !== 'all') {
        apiUrl += `?status=${encodeURIComponent(status)}`;
    }

    appendToChatLog(`Fetching ${status} suggestions...`, 'system-help');
    setWeiboState('weibo-thinking'); // Optional: show AI is "working"

    try {
        const response = await fetch(apiUrl);
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ error: `HTTP error ${response.status}: ${response.statusText}` }));
            throw new Error(errorData.error || `Failed to fetch suggestions. Server responded with ${response.status}`);
        }
        const suggestions = await response.json();

        if (suggestions && suggestions.length > 0) {
            const suggestionsContainer = document.createElement('div');
            suggestionsContainer.className = 'suggestions-list-container'; // For styling the whole block

            let htmlContent = `<div class="suggestions-header">Found ${suggestions.length} ${status} suggestion(s):</div>`;
            suggestions.forEach((sug) => {
                const descSnippet = (sug.description || 'N/A').substring(0, 150) +
                                    ((sug.description && sug.description.length > 150) ? '...' : '');
                const reasonText = sug.reason ? `<div class="suggestion-reason">Reason: ${sug.reason}</div>` : '';

                // Buttons are only added if status is 'pending'
                let actionButtons = '';
                if (sug.status && sug.status.toLowerCase() === 'pending') {
                    actionButtons = `
                        <div class="suggestion-actions">
                            <button class="approve-suggestion-btn" data-suggestion-id="${sug.suggestion_id}">Approve</button>
                            <button class="deny-suggestion-btn" data-suggestion-id="${sug.suggestion_id}">Deny</button>
                        </div>`;
                }

                htmlContent += `
                    <div class="suggestion-item" id="suggestion-item-${sug.suggestion_id}">
                        <div class="suggestion-id">ID: ${sug.suggestion_id || 'N/A'}</div>
                        <div class="suggestion-title">Title: ${sug.title || 'N/A'}</div>
                        <div class="suggestion-description">Desc: ${descSnippet}</div>
                        <div class="suggestion-status">Status: <span class="status-value">${sug.status || 'N/A'}</span></div>
                        ${reasonText}
                        ${actionButtons}
                    </div>`;
            });
            suggestionsContainer.innerHTML = htmlContent;

            // Append as a rich HTML message to chatLogArea
            // This requires appendToChatLog to handle HTML content for 'ai' sender type,
            // or a new function to append raw HTML.
            // For now, let's assume appendToChatLog can take simple HTML string for 'ai' messages.
            // A better way would be to create a message div and set its innerHTML.
            const messageDiv = document.createElement('div');
            messageDiv.classList.add('message', 'ai-message', 'interactive-list'); // Add 'interactive-list' for specific styling
            messageDiv.appendChild(suggestionsContainer);
            chatLogArea.appendChild(messageDiv);
            chatLogArea.scrollTop = chatLogArea.scrollHeight;

        } else {
            appendToChatLog(`No ${status} suggestions found.`, 'ai');
        }
    } catch (error) {
        console.error(`Failed to fetch ${status} suggestions:`, error);
        appendToChatLog(`Error fetching ${status} suggestions: ${error.message}`, 'ai');
    } finally {
        setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle'); // Reset AI state
    }
}


// --- Status Panel Logic ---
async function fetchAndDisplayActiveTasks() {
    if (!activeTasksList) {
        return;
    }
    activeTasksList.innerHTML = '<li>Loading tasks...</li>';

    try {
        const response = await fetch('/api/status/active_tasks');
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP error ${response.status}: ${errorText || response.statusText}`);
        }
        const tasks = await response.json();

        activeTasksList.innerHTML = '';
        if (tasks && tasks.length > 0) {
            tasks.forEach(task => {
                const li = document.createElement('li');
                let taskDesc = task.description || 'No description';
                if (taskDesc.length > 40) taskDesc = taskDesc.substring(0, 37) + "...";
                const status = task.status || 'UNKNOWN';
                const type = task.task_type || 'GENERAL';
                li.textContent = `[${type.substring(0,10)}] ${taskDesc} - ${status}`;
                li.title = `ID: ${task.task_id}\nFull Desc: ${task.description}\nStatus: ${status}\nReason: ${task.status_reason || 'N/A'}\nStep: ${task.current_step_description || 'N/A'}`;
                activeTasksList.appendChild(li);
            });
        } else {
            activeTasksList.innerHTML = '<li>No active tasks.</li>';
        }
    } catch (error) {
        console.error("Failed to fetch active tasks:", error);
        activeTasksList.innerHTML = `<li>Error loading tasks.</li>`;
    }
}

async function fetchAndDisplayRecentNotifications() {
    if (!recentNotificationsList) {
        return;
    }
    recentNotificationsList.innerHTML = '<li>Loading notifications...</li>';

    try {
        const response = await fetch('/api/status/notifications');
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP error ${response.status}: ${errorText || response.statusText}`);
        }
        const notifications = await response.json();

        recentNotificationsList.innerHTML = '';
        if (notifications && notifications.length > 0) {
            notifications.forEach(notif => {
                const li = document.createElement('li');
                let summary = notif.summary_message || 'No summary';
                if (summary.length > 45) summary = summary.substring(0, 42) + "...";
                const type = notif.event_type || 'INFO';
                let tsDisplay = 'Unknown time';
                if (notif.timestamp) {
                    try {
                        const d = new Date(notif.timestamp);
                        tsDisplay = `${d.toLocaleDateString(undefined, {month:'2-digit', day:'2-digit'})} ${d.toLocaleTimeString(undefined, {hour:'2-digit', minute:'2-digit'})}`;
                    } catch (e) { /* ignore date parsing error */ }
                }
                li.textContent = `[${tsDisplay} | ${type}] ${summary}`;
                li.title = `ID: ${notif.notification_id}\nType: ${type}\nFull Summary: ${notif.summary_message}\nStatus: ${notif.status}\nTimestamp: ${notif.timestamp}`;
                recentNotificationsList.appendChild(li);
            });
        } else {
            recentNotificationsList.innerHTML = '<li>No new notifications.</li>';
        }
    } catch (error) {
        console.error("Failed to fetch recent notifications:", error);
        recentNotificationsList.innerHTML = '<li>Error loading notifications.</li>';
    }
}


document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded and parsed. Initializing script logic.");

    chatLogArea = document.getElementById('chatLogArea');
    userInput = document.getElementById('userInput');
    sendButton = document.getElementById('sendButton');
    aiCoreStatusText = document.getElementById('aiCoreStatusText');
    processingIndicator = document.getElementById('processingIndicator');
    aiCoreDisplay = document.getElementById('aiCoreDisplay');
    matrixScrollEffect = document.getElementById('matrixScrollEffect');
    helpButton = document.getElementById('helpButton');
    helpMenuPopup = document.getElementById('helpMenuPopup');
    statusPanel = document.getElementById('statusPanel');
    statusPanelToggle = document.getElementById('statusPanelToggle');
    activeTasksList = document.getElementById('activeTasksList');
    refreshActiveTasksBtn = document.getElementById('refreshActiveTasksBtn');
    recentNotificationsList = document.getElementById('recentNotificationsList');
    refreshNotificationsBtn = document.getElementById('refreshNotificationsBtn');
    projectDisplayArea = document.getElementById('projectDisplayArea');
    projectDisplayLoadingIndicator = document.querySelector('#projectDisplayArea .loading-indicator-container'); // Assign new element
    const toggleProjectDisplayBtn = document.getElementById('toggleProjectDisplayBtn');


    if (aiCoreDisplay) {
        const rect = aiCoreDisplay.getBoundingClientRect();
        const styles = window.getComputedStyle(aiCoreDisplay);
        console.log("aiCoreDisplay Info:", "Width:", rect.width, "Height:", rect.height, "Top:", rect.top, "Left:", rect.left, "Computed Position:", styles.position);
    } else { console.error("aiCoreDisplay element NOT FOUND!"); }

    if (processingIndicator) {
        const rect = processingIndicator.getBoundingClientRect();
        const styles = window.getComputedStyle(processingIndicator);
        console.log("Initial processingIndicator Info:", "Width:", rect.width, "Height:", rect.height, "Top:", rect.top, "Left:", rect.left, "Computed Position:", styles.position, "OffsetTop:", processingIndicator.offsetTop, "OffsetLeft:", processingIndicator.offsetLeft);
    } else { console.error("processingIndicator element NOT FOUND!"); }

    if (userInput && sendButton) {
        userInput.focus();
        sendButton.addEventListener('click', sendMessage);
        userInput.addEventListener('keypress', function(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                sendMessage();
            }
        });
    } else { console.error("userInput or sendButton element NOT FOUND!"); }

    if (helpButton && helpMenuPopup && userInput) {
        helpButton.addEventListener('click', function(event) {
            console.log("Help button clicked"); // Diagnostic log
            event.stopPropagation();
            const isHidden = helpMenuPopup.style.display === 'none' || helpMenuPopup.style.display === '';
            if (isHidden) {
                const buttonRect = helpButton.getBoundingClientRect();
                helpMenuPopup.style.display = 'block';
                const menuRect = helpMenuPopup.getBoundingClientRect();

                if (window.innerWidth > 768) {
                    let leftPosition = buttonRect.left;
                    if (leftPosition + menuRect.width > window.innerWidth - 20) {
                        leftPosition = buttonRect.right - menuRect.width;
                    }
                    helpMenuPopup.style.left = `${Math.max(10, leftPosition)}px`;
                    helpMenuPopup.style.bottom = `${window.innerHeight - buttonRect.top + 10}px`;
                    helpMenuPopup.style.top = 'auto';
                    helpMenuPopup.style.transform = 'none';
                } else {
                    helpMenuPopup.style.left = '';
                    helpMenuPopup.style.bottom = '';
                    helpMenuPopup.style.top = 'auto';
                }
            } else {
                helpMenuPopup.style.display = 'none';
            }
        });
        helpMenuPopup.addEventListener('click', function(event) {
            event.stopPropagation();
            const targetLi = event.target.closest('li[data-command], li[data-help-action]');

            if (targetLi) {
                const helpAction = targetLi.dataset.helpAction;
                const command = targetLi.dataset.command;
                const infoText = targetLi.dataset.infoText; // New attribute for direct display

                if (helpAction === 'displayInfo' && infoText) {
                    appendToChatLog(infoText, 'system-help'); // New sender type for styling
                    helpMenuPopup.style.display = 'none';
                } else if (command) { // Default or explicit populateInput
                    userInput.value = command;
                    userInput.focus();
                    if (command.endsWith(' ')) {
                        userInput.setSelectionRange(command.length, command.length);
                    }
                    helpMenuPopup.style.display = 'none';
                }
            }
        });
    } else { console.error("Help menu button or popup NOT FOUND!"); }

    if (statusPanel && statusPanelToggle) {
        statusPanelToggle.addEventListener('click', function() {
            const isCollapsed = statusPanel.classList.toggle('collapsed');
            statusPanelToggle.innerHTML = isCollapsed ? '&lt;' : '&gt;';
            statusPanelToggle.setAttribute('title', isCollapsed ? 'Open Status Panel' : 'Close Status Panel');

            if (!isCollapsed) {
                if(activeTasksList) fetchAndDisplayActiveTasks();
                if(recentNotificationsList) fetchAndDisplayRecentNotifications();
            }
        });
    } else { console.error("Status panel or toggle button NOT FOUND!"); }

    if (refreshActiveTasksBtn) {
        refreshActiveTasksBtn.addEventListener('click', fetchAndDisplayActiveTasks);
    } else { console.warn("Refresh active tasks button not found."); }

    if (refreshNotificationsBtn) {
        refreshNotificationsBtn.addEventListener('click', fetchAndDisplayRecentNotifications);
    } else { console.warn("Refresh notifications button not found."); }

    if (processingIndicator && aiCoreDisplay && aiCoreStatusText) {
        setWeiboState('idle');
    } else {
        console.error("One or more core elements for Weibo state are missing. Cannot set initial state.");
        if(aiCoreStatusText) aiCoreStatusText.textContent = "UI Error: Core elements missing.";
    }

    // Fullscreen Toggle for Project Display Area
    const fullscreenToggleBtn = document.getElementById('fullscreenToggleBtn');
    // projectDisplayArea is already globally defined and assigned in DOMContentLoaded

    if (fullscreenToggleBtn && projectDisplayArea) {
        const enterIcon = fullscreenToggleBtn.querySelector('.icon-fullscreen-enter');
        const exitIcon = fullscreenToggleBtn.querySelector('.icon-fullscreen-exit');

        if (enterIcon && exitIcon) {
            fullscreenToggleBtn.addEventListener('click', () => {
                projectDisplayArea.classList.toggle('fullscreen');
                const isFullscreen = projectDisplayArea.classList.contains('fullscreen');
                if (isFullscreen) {
                    enterIcon.style.display = 'none';
                    exitIcon.style.display = 'block';
                    fullscreenToggleBtn.title = "Exit Fullscreen";
                    // Potentially hide other elements like .ai-core-display, .chat-log-area, .input-area-sci-fi
                    // by adding a class to a common parent or directly to them.
                    // For now, CSS will handle z-index and sizing.
                } else {
                    enterIcon.style.display = 'block';
                    exitIcon.style.display = 'none';
                    fullscreenToggleBtn.title = "Toggle Fullscreen";
                }
            });
        } else {
            console.error("Fullscreen toggle icons not found within the button.");
        }
    } else {
        console.error("Fullscreen toggle button or project display area not found for event listener setup.");
    }

    // Pause/Resume for Project Display Iframe
    const pauseBtn = document.getElementById('pauseBtn');
    const resumeBtn = document.getElementById('resumeBtn');
    const projectIframe = document.getElementById('projectDisplayIframe');

    if (pauseBtn && resumeBtn && projectIframe) {
        pauseBtn.addEventListener('click', () => {
            if (projectIframe.contentWindow) {
                projectIframe.contentWindow.postMessage('pause', '*');
                console.log('Sent "pause" message to project iframe.');
                pauseBtn.disabled = true;
                resumeBtn.disabled = false;
            } else {
                console.error("Project iframe contentWindow not accessible to send pause message.");
            }
        });

        resumeBtn.addEventListener('click', () => {
            if (projectIframe.contentWindow) {
                projectIframe.contentWindow.postMessage('resume', '*');
                console.log('Sent "resume" message to project iframe.');
                resumeBtn.disabled = true;
                pauseBtn.disabled = false;
            } else {
                console.error("Project iframe contentWindow not accessible to send resume message.");
            }
        });

        // Consider enabling pauseBtn only when there's "active" content.
        // For now, it's always enabled, and resume is initially disabled.
        // We might need a message from the iframe to indicate it's pausable.

    } else {
        console.error("Pause/Resume buttons or project iframe not found for event listener setup.");
    }

    // Analyze Display Button Logic
    const analyzeDisplayBtn = document.getElementById('analyzeDisplayBtn');
    // projectIframe is already defined above for Pause/Resume

    if (analyzeDisplayBtn && projectIframe) {
        analyzeDisplayBtn.addEventListener('click', async () => {
            const htmlContent = projectIframe.srcdoc;
            if (!htmlContent || htmlContent.trim() === '' || htmlContent.includes("Project Display Area - Content will appear here") || projectDisplayArea.style.display === 'none') {
                appendToChatLog("There is no active project content in the display area to analyze.", 'ai');
                setWeiboState('weibo-talking');
                setTimeout(() => setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle'), 2500);
                return;
            }

            analyzeDisplayBtn.disabled = true;
            const originalButtonTitle = analyzeDisplayBtn.title;
            analyzeDisplayBtn.title = "Analyzing...";

            setWeiboState('weibo-thinking');
            if (aiCoreStatusText) aiCoreStatusText.textContent = 'Analyzing displayed content...';

            try {
                const response = await fetch('/api/analyze_display', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ html_content: htmlContent })
                });

                const data = await response.json();
                setWeiboState('weibo-talking');

                if (response.ok && data.success && data.analysis_text) {
                    appendToChatLog(data.analysis_text, 'ai');
                } else {
                    const errorMsg = data.analysis_text || data.error || "Failed to get analysis from server.";
                    appendToChatLog(`Analysis Error: ${errorMsg}`, 'ai');
                    if (aiCoreStatusText) aiCoreStatusText.textContent = 'Error during analysis.';
                }
            } catch (error) {
                console.error('Error sending content for analysis:', error);
                setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle');
                if (aiCoreStatusText) aiCoreStatusText.textContent = 'Connection error during analysis.';
                appendToChatLog(`Network Error: Could not analyze display content. ${error.message}`, 'ai');
            } finally {
                analyzeDisplayBtn.disabled = false;
                analyzeDisplayBtn.title = originalButtonTitle;
            }
        });
    } else {
        console.error("Analyze Display button or project iframe not found for event listener setup.");
    }

    // --- Project Display Area Toggle Logic ---
    const projectDisplayDefaultText = "<p style='padding: 10px; color: #ccc;'>Project Display Active. No project loaded. Ask the AI to create something or use a command to load a project.</p>";

    function toggleProjectDisplay(forceShow) {
        const isProjectModeCurrentlyActive = document.body.classList.contains('project-mode-active');
        const show = typeof forceShow === 'boolean' ? forceShow : !isProjectModeCurrentlyActive;

        if (show) {
            document.body.classList.add('project-mode-active');
            // projectDisplayArea.classList.add('visible'); // CSS now uses body.project-mode-active #projectDisplayArea

            if (processingIndicator && projectDisplayArea && aiCoreDisplay) {
                if (processingIndicator.parentNode !== projectDisplayArea) {
                    projectDisplayArea.appendChild(processingIndicator);
                }
                processingIndicator.classList.add('weibo-project-corner');
                // Remove other state classes that might conflict with corner positioning/sizing
                processingIndicator.classList.remove('idle', 'weibo-thinking', 'weibo-talking', 'weibo-in-work-zone');
                 // Re-apply logical state if needed, or let corner mode dominate
                const currentLogicalState = getCurrentWeiboState() || 'idle'; // Get state before it was moved
                setWeiboState(currentLogicalState); // This will re-evaluate based on new parent/classes
            }

            // Set default content if no project is actively being loaded by AI
            // Check if iframe content is already set by AI or is the initial placeholder
            const iframe = projectDisplayArea.querySelector('iframe#projectDisplayIframe');
            if (iframe && (!iframe.srcdoc || iframe.srcdoc.includes('Interactive Project Area') || iframe.srcdoc.includes('projectDisplayDefaultText'))) {
                iframe.srcdoc = projectDisplayDefaultText;
            }
            if (toggleProjectDisplayBtn) toggleProjectDisplayBtn.title = "Hide Project Display";

        } else { // Hiding project display
            document.body.classList.remove('project-mode-active');
            // projectDisplayArea.classList.remove('visible');

            if (processingIndicator && aiCoreDisplay) {
                if (processingIndicator.parentNode !== aiCoreDisplay) {
                    aiCoreDisplay.appendChild(processingIndicator);
                }
                processingIndicator.classList.remove('weibo-project-corner');
                // Restore normal Weibo state (e.g., idle, or whatever it was before)
                // setWeiboState needs to be called to re-evaluate its position and animation
                // based on its original container and classes.
                const currentLogicalState = getCurrentWeiboState() || 'idle';
                setWeiboState(currentLogicalState);
            }
            if (toggleProjectDisplayBtn) toggleProjectDisplayBtn.title = "Show Project Display";
        }
    }

    if (toggleProjectDisplayBtn) {
        toggleProjectDisplayBtn.addEventListener('click', () => toggleProjectDisplay());
    } else {
        console.error("Toggle Project Display button not found.");
    }

    // Expose toggleProjectDisplay globally if needed for commands, or handle commands internally
    window.toggleProjectDisplay = toggleProjectDisplay;
    // Call initially to ensure correct default state (hidden)
    // toggleProjectDisplay(false); // Ensure it's hidden by default if CSS isn't enough (CSS should be enough)

    // Delegated event listeners for suggestion action buttons
    if (chatLogArea) {
        chatLogArea.addEventListener('click', function(event) {
            const target = event.target;
            let action = null;
            let suggestionId = null;

            if (target.classList.contains('approve-suggestion-btn')) {
                action = 'approve';
                suggestionId = target.dataset.suggestionId;
            } else if (target.classList.contains('deny-suggestion-btn')) {
                action = 'deny';
                suggestionId = target.dataset.suggestionId;
            }

            if (action && suggestionId) {
                // For now, using a simple prompt. Could be replaced with a modal later.
                const reason = prompt(`Enter reason for ${action}ing suggestion ${suggestionId} (optional):`);
                // User might cancel prompt, in which case reason is null.
                // handleSuggestionAction should be fine with a null reason.
                handleSuggestionAction(suggestionId, action, reason);
            }

            // Event listeners for task actions
            let taskAction = null;
            let taskId = null;
            let taskParams = {};

            if (target.classList.contains('view-task-plan-btn')) {
                taskAction = 'view_plan';
                taskId = target.dataset.taskId;
            } else if (target.classList.contains('complete-task-btn')) {
                taskAction = 'complete';
                taskId = target.dataset.taskId;
                // Optionally prompt for a reason for completion
                taskParams.reason = prompt(`Enter reason for completing task ${taskId} (optional):`) || "Completed by user.";
            } else if (target.classList.contains('archive-task-btn')) {
                taskAction = 'archive';
                taskId = target.dataset.taskId;
                taskParams.reason = prompt(`Enter reason for archiving task ${taskId} (optional):`) || "Archived by user.";
            }

            if (taskAction && taskId) {
                handleTaskAction(taskId, taskAction, taskParams);
            }
        });
    } else {
        console.error("Chat log area not found for attaching suggestion/task action listeners.");
    }
});

document.addEventListener('click', function(event) {
    const currentHelpMenuPopup = helpMenuPopup || document.getElementById('helpMenuPopup');
    const currentHelpButton = helpButton || document.getElementById('helpButton');
    if (currentHelpMenuPopup && currentHelpMenuPopup.style.display === 'block') {
        if (currentHelpButton && !currentHelpButton.contains(event.target) && !currentHelpMenuPopup.contains(event.target)) {
            currentHelpMenuPopup.style.display = 'none';
        }
    }
});

document.addEventListener('keydown', function(event) {
    const currentHelpMenuPopup = helpMenuPopup || document.getElementById('helpMenuPopup');
    if (event.key === 'Escape' && currentHelpMenuPopup && currentHelpMenuPopup.style.display === 'block') {
        currentHelpMenuPopup.style.display = 'none';
    }
});
