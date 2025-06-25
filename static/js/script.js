const chatLogArea = document.getElementById('chatLogArea');
const userInput = document.getElementById('userInput');
const sendButton = document.getElementById('sendButton');
const aiCoreStatusText = document.getElementById('aiCoreStatusText');
const processingIndicator = document.getElementById('processingIndicator');
const aiCoreDisplay = document.getElementById('aiCoreDisplay');

let idleAnimationTimeoutId = null;
let isWeiboWorkingInBackground = false;

function setWeiboState(state) {
    const currentState = getCurrentWeiboState();
    processingIndicator.classList.remove('idle', 'weibo-working-idle', 'weibo-thinking', 'weibo-talking');
    if (state) {
        processingIndicator.classList.add(state);
    }

    if (state.includes('idle')) {
        if (!isIdleAnimationRunning() || currentState !== state) {
            startIdleAnimation();
        }
    } else {
        stopIdleAnimation();
    }
}

function appendToChatLog(text, sender) {
    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message');
    if (sender === 'user') {
        messageDiv.classList.add('user-message');
        messageDiv.textContent = text;
    } else {
        messageDiv.classList.add('ai-message');
        messageDiv.textContent = text;
    }
    chatLogArea.appendChild(messageDiv);
    chatLogArea.scrollTop = chatLogArea.scrollHeight;
}

async function sendMessage() {
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
            body: JSON.stringify({ message: messageText }),
        });

        setWeiboState('weibo-talking');

        if (!response.ok) {
            let errorMsg = `Error: ${response.status} ${response.statusText}`;
            try {
                const errorData = await response.json();
                if (errorData && errorData.error) {
                    errorMsg = `Error: ${errorData.error}`;
                } else if (errorData && errorData.response && typeof errorData.response === 'string') {
                    errorMsg = errorData.response;
                }
            } catch (e) { /* Ignore if error response is not JSON */ }
            appendToChatLog(errorMsg, 'ai');
            aiCoreStatusText.textContent = 'Error processing directive.';
            setTimeout(() => setWeiboState(isWeiboWorkingInBackground ? 'weibo-working-idle' : 'idle'), 500);
            return;
        }

        const data = await response.json();
        if (data.response) {
            appendToChatLog(data.response, 'ai');
            aiCoreStatusText.textContent = 'Directive processed. Standing by.';
            setTimeout(() => setWeiboState(isWeiboWorkingInBackground ? 'weibo-working-idle' : 'idle'), 500);
        } else if (data.error) {
            appendToChatLog(`Error: ${data.error}`, 'ai');
            aiCoreStatusText.textContent = 'Error from AI.';
            setTimeout(() => setWeiboState(isWeiboWorkingInBackground ? 'weibo-working-idle' : 'idle'), 500);
        } else {
            appendToChatLog('Received an empty or unexpected response.', 'ai');
            aiCoreStatusText.textContent = 'Unexpected response received.';
            setTimeout(() => setWeiboState(isWeiboWorkingInBackground ? 'weibo-working-idle' : 'idle'), 500);
        }

    } catch (error) {
        console.error('Failed to send message:', error);
        appendToChatLog(`Connection error: ${error.message}`, 'ai');
        aiCoreStatusText.textContent = 'Connection Error. System Offline?';
        setWeiboState(isWeiboWorkingInBackground ? 'weibo-working-idle' : 'idle');
    }
}

function animateIdleWeibo() {
    if (!processingIndicator || !aiCoreDisplay) { // Ensure elements exist
        console.warn("Processing indicator or AI core display not found. Cannot animate.");
        return;
    }
    if (!processingIndicator.classList.contains('idle') && !processingIndicator.classList.contains('weibo-working-idle')) {
        return;
    }

    const parentRect = aiCoreDisplay.getBoundingClientRect();
    const indicatorStyle = window.getComputedStyle(processingIndicator);
    const indicatorWidth = parseFloat(indicatorStyle.width);
    const indicatorHeight = parseFloat(indicatorStyle.height);

    if (parentRect.width === 0 || parentRect.height === 0) {
         console.warn("aiCoreDisplay has no dimensions, cannot animate idle Weibo.");
         idleAnimationTimeoutId = setTimeout(animateIdleWeibo, 5000);
         return;
    }

    const maxX = parentRect.width - indicatorWidth;
    const maxY = parentRect.height - indicatorHeight;

    const targetX = Math.max(0, Math.random() * maxX);
    const targetY = Math.max(0, Math.random() * maxY);

    const randomScale = 0.8 + Math.random() * 0.4;

    processingIndicator.style.transform = `translate(${targetX}px, ${targetY}px) scale(${randomScale})`;

    const randomDelay = 3000 + Math.random() * 4000;
    idleAnimationTimeoutId = setTimeout(animateIdleWeibo, randomDelay);
}

function startIdleAnimation() {
    stopIdleAnimation();
    setTimeout(() => {
        // Check again if in correct state, as state might have changed during timeout
        // Also ensure elements are present before trying to animate.
        if (processingIndicator && aiCoreDisplay) {
            const currentDisplayState = getCurrentWeiboState();
            if (currentDisplayState === 'idle' || currentDisplayState === 'weibo-working-idle') {
                animateIdleWeibo();
            }
        } else {
            console.warn("Cannot start idle animation: elements not ready.");
        }
    }, 100); // Small delay to ensure DOM is ready.
}
function stopIdleAnimation() {
    clearTimeout(idleAnimationTimeoutId);
    idleAnimationTimeoutId = null;
}

function isIdleAnimationRunning() {
    return idleAnimationTimeoutId !== null;
}

function getCurrentWeiboState() {
    if (!processingIndicator) return null; // Guard clause
    if (processingIndicator.classList.contains('weibo-thinking')) return 'weibo-thinking';
    if (processingIndicator.classList.contains('weibo-talking')) return 'weibo-talking';
    if (processingIndicator.classList.contains('weibo-working-idle')) return 'weibo-working-idle';
    if (processingIndicator.classList.contains('idle')) return 'idle';
    return null;
}

function toggleBackgroundWork() {
    isWeiboWorkingInBackground = !isWeiboWorkingInBackground;
    const currentLogicalState = getCurrentWeiboState();

    if (currentLogicalState === 'idle' || currentLogicalState === 'weibo-working-idle') {
        setWeiboState(isWeiboWorkingInBackground ? 'weibo-working-idle' : 'idle');
    }
    if (aiCoreStatusText) { // Guard clause
        aiCoreStatusText.textContent = isWeiboWorkingInBackground ? "Weibo is working on background tasks..." : "AI Core Systems Nominal";
    }
    console.log("Background work toggled:", isWeiboWorkingInBackground);
}
// Removed 'B' key listener. To test toggleBackgroundWork, call it from the console.

// --- Initial Diagnostics & Setup ---
// Wrapped in DOMContentLoaded to ensure elements are available
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded and parsed. Initializing script logic.");

    // Re-assign consts here as they might not be available when script is just parsed
    const localChatLogArea = document.getElementById('chatLogArea');
    const localUserInput = document.getElementById('userInput');
    const localSendButton = document.getElementById('sendButton');
    const localAiCoreStatusText = document.getElementById('aiCoreStatusText');
    const localProcessingIndicator = document.getElementById('processingIndicator');
    const localAiCoreDisplay = document.getElementById('aiCoreDisplay');

    // Assign to global consts if you need them globally, or pass as params.
    // For simplicity of this refactor, I'll assume the global consts are okay for now
    // but ideally, they'd be scoped or passed.
    // This is a bit of a hack due to the direct script porting.
    if (!chatLogArea && localChatLogArea) chatLogArea = localChatLogArea;
    if (!userInput && localUserInput) userInput = localUserInput;
    // ... and so on for other global consts if they were declared outside DOMContentLoaded

    if (localAiCoreDisplay) {
        const rect = localAiCoreDisplay.getBoundingClientRect();
        const styles = window.getComputedStyle(localAiCoreDisplay);
        console.log("aiCoreDisplay Info:",
            "Width:", rect.width, "Height:", rect.height,
            "Top:", rect.top, "Left:", rect.left,
            "Computed Position:", styles.position);
    } else {
        console.error("aiCoreDisplay element NOT FOUND at DOMContentLoaded!");
    }

    if (localProcessingIndicator) {
        const rect = localProcessingIndicator.getBoundingClientRect();
        const styles = window.getComputedStyle(localProcessingIndicator);
        console.log("Initial processingIndicator Info:",
            "Width:", rect.width, "Height:", rect.height,
            "Top:", rect.top, "Left:", rect.left,
            "Computed Position:", styles.position,
            "Computed Top:", styles.top, "Computed Left:", styles.left, "Computed Bottom:", styles.bottom, "Computed Right:", styles.right,
            "OffsetTop:", localProcessingIndicator.offsetTop, "OffsetLeft:", localProcessingIndicator.offsetLeft);
    } else {
        console.error("processingIndicator element NOT FOUND at DOMContentLoaded!");
    }

    if (localUserInput) {
        localUserInput.focus();
        localSendButton.addEventListener('click', sendMessage);
        localUserInput.addEventListener('keypress', function(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                sendMessage();
            }
        });
    } else {
        console.error("userInput element NOT FOUND at DOMContentLoaded!");
    }

    if (localProcessingIndicator && localAiCoreDisplay && localAiCoreStatusText) { // Check all required elements
        setWeiboState('idle');
    } else {
        console.error("One or more core elements for Weibo state are missing. Cannot set initial state.");
        if(localAiCoreStatusText) localAiCoreStatusText.textContent = "UI Error: Core elements missing.";
    }
});
