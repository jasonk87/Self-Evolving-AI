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
let projectDisplayArea = null; // For Project Display Area


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

function appendToChatLog(text, sender) {
    if (!chatLogArea) return;
    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message');

    if (sender === 'user') {
        messageDiv.classList.add('user-message');
        messageDiv.textContent = text;
        chatLogArea.appendChild(messageDiv);
    } else {
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
                chatLogArea.scrollTop = chatLogArea.scrollHeight;
                setTimeout(typeCharacter, typingSpeed);
            } else {
                messageDiv.classList.remove('typing');
                if (aiCoreStatusText) aiCoreStatusText.textContent = 'Response complete. Standing by.';
                setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle');
            }
        }
        typeCharacter();
    }
    chatLogArea.scrollTop = chatLogArea.scrollHeight;
}

async function sendMessage() {
    if (!userInput || !aiCoreStatusText) return;
    const messageText = userInput.value.trim();
    if (messageText === '') return;

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
        if (data.project_area_html && projectDisplayArea) {
            projectDisplayArea.innerHTML = data.project_area_html;
        } else if (data.project_area_html && !projectDisplayArea) {
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
    projectDisplayArea = document.getElementById('projectDisplayArea'); // Assign projectDisplayArea


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
            const target = event.target.closest('li[data-command]');
            if (target) {
                let command = target.dataset.command;
                userInput.value = command;
                userInput.focus();
                if (command.endsWith(' ')) {
                    userInput.setSelectionRange(command.length, command.length);
                }
                helpMenuPopup.style.display = 'none';
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
