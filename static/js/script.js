const chatLogArea = document.getElementById('chatLogArea');
const userInput = document.getElementById('userInput');
const sendButton = document.getElementById('sendButton');
const aiCoreStatusText = document.getElementById('aiCoreStatusText');
const processingIndicator = document.getElementById('processingIndicator');
const aiCoreDisplay = document.getElementById('aiCoreDisplay');

let idleAnimationTimeoutId = null;
let isWeiboWorkingInBackground = false;

function setWeiboState(state) {
    if (!processingIndicator) {
        console.error("setWeiboState: processingIndicator not found!");
        return;
    }

    processingIndicator.classList.remove('idle', 'weibo-working-idle', 'weibo-thinking', 'weibo-talking', 'weibo-in-work-zone');
    // Clear any inline styles that might conflict, especially transform from old idle or top/left from this new idle
    processingIndicator.style.transform = '';
    processingIndicator.style.top = '';
    processingIndicator.style.left = '';
    processingIndicator.style.bottom = '';
    processingIndicator.style.right = '';

    switch (state) {
        case 'idle':
            processingIndicator.classList.add('idle');
            // CSS for .idle now sets bottom:20px, left:20px, transform:scale(0.8)
            // Start JS animation which will override top/left for drifting.
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
    } else {
        messageDiv.classList.add('ai-message');
        messageDiv.textContent = text;
    }
    chatLogArea.appendChild(messageDiv);
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
            setTimeout(() => setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle'), 500);
            return;
        }

        const data = await response.json();
        if (data.response) {
            appendToChatLog(data.response, 'ai');
            aiCoreStatusText.textContent = 'Directive processed. Standing by.';
            setTimeout(() => setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle'), 500);
        } else if (data.error) {
            appendToChatLog(`Error: ${data.error}`, 'ai');
            aiCoreStatusText.textContent = 'Error from AI.';
            setTimeout(() => setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle'), 500);
        } else {
            appendToChatLog('Received an empty or unexpected response.', 'ai');
            aiCoreStatusText.textContent = 'Unexpected response received.';
            setTimeout(() => setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle'), 500);
        }

    } catch (error) {
        console.error('Failed to send message:', error);
        appendToChatLog(`Connection error: ${error.message}`, 'ai');
        aiCoreStatusText.textContent = 'Connection Error. System Offline?';
        setWeiboState(isWeiboWorkingInBackground ? 'background-processing' : 'idle');
    }
}

function animateIdleWeibo() {
    if (!processingIndicator || !aiCoreDisplay) {
        console.warn("Processing indicator or AI core display not found for animateIdleWeibo.");
        return;
    }
    if (!processingIndicator.classList.contains('idle')) {
        return;
    }

    const parentRect = aiCoreDisplay.getBoundingClientRect();
    const indicatorStyle = window.getComputedStyle(processingIndicator);
    const indicatorWidth = parseFloat(indicatorStyle.width); // Current width (could be scaled by CSS)
    const indicatorHeight = parseFloat(indicatorStyle.height); // Current height

    if (!parentRect || parentRect.width === 0 || parentRect.height === 0) {
         console.warn("aiCoreDisplay has no dimensions or not found, cannot animate idle Weibo.");
         idleAnimationTimeoutId = setTimeout(animateIdleWeibo, 5000);
         return;
    }

    // Calculate max X and Y for the TOP-LEFT corner of the indicator
    const maxX = parentRect.width - indicatorWidth;
    const maxY = parentRect.height - indicatorHeight;

    let targetX = Math.random() * Math.max(0, maxX);
    let targetY = Math.random() * Math.max(0, maxY);

    // Clamp values to ensure they are within bounds
    targetX = Math.max(0, Math.min(targetX, maxX));
    targetY = Math.max(0, Math.min(targetY, maxY));

    console.log(`Animating Idle (top/left): TargetX=${targetX.toFixed(2)}, TargetY=${targetY.toFixed(2)} (maxX:${maxX.toFixed(2)}, maxY:${maxY.toFixed(2)}) ParentW:${parentRect.width.toFixed(2)}, ParentH:${parentRect.height.toFixed(2)} IndiW:${indicatorWidth.toFixed(2)}`);

    // Use top/left for positioning, remove translate from transform
    processingIndicator.style.left = `${targetX}px`;
    processingIndicator.style.top = `${targetY}px`;
    // Keep existing scale from .idle class or apply a new one if we reintroduce random scaling
    // For now, rely on .idle class's transform: scale(0.8)
    // To re-enable random scaling with top/left:
    // const randomScale = 0.7 + Math.random() * 0.6;
    // processingIndicator.style.transform = `scale(${randomScale})`;

    const randomDelay = 3000 + Math.random() * 4000;
    idleAnimationTimeoutId = setTimeout(animateIdleWeibo, randomDelay);
}

function startIdleAnimation() {
    stopIdleAnimation();
    setTimeout(() => {
        if (processingIndicator && aiCoreDisplay) {
            const currentDisplayState = getCurrentWeiboState();
            if (currentDisplayState === 'idle') {
                // Ensure base CSS for .idle (bottom, left, scale) is applied before first JS animation
                // by forcing a reflow or ensuring CSS has taken effect.
                // The setWeiboState already clears inline transform, so CSS should apply.
                animateIdleWeibo();
            }
        } else {
            console.warn("Cannot start idle animation: elements not ready.");
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

document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded and parsed. Initializing script logic.");

    // Re-assign global consts (ensure they are defined if script loaded early)
    if (!window.chatLogArea) window.chatLogArea = document.getElementById('chatLogArea');
    if (!window.userInput) window.userInput = document.getElementById('userInput');
    if (!window.sendButton) window.sendButton = document.getElementById('sendButton');
    if (!window.aiCoreStatusText) window.aiCoreStatusText = document.getElementById('aiCoreStatusText');
    if (!window.processingIndicator) window.processingIndicator = document.getElementById('processingIndicator');
    if (!window.aiCoreDisplay) window.aiCoreDisplay = document.getElementById('aiCoreDisplay');


    if (aiCoreDisplay) {
        const rect = aiCoreDisplay.getBoundingClientRect();
        const styles = window.getComputedStyle(aiCoreDisplay);
        console.log("aiCoreDisplay Info:",
            "Width:", rect.width, "Height:", rect.height,
            "Top:", rect.top, "Left:", rect.left,
            "Computed Position:", styles.position);
    } else {
        console.error("aiCoreDisplay element NOT FOUND at DOMContentLoaded!");
    }

    if (processingIndicator) {
        const rect = processingIndicator.getBoundingClientRect();
        const styles = window.getComputedStyle(processingIndicator);
        console.log("Initial processingIndicator Info:",
            "Width:", rect.width, "Height:", rect.height,
            "Top:", rect.top, "Left:", rect.left,
            "Computed Position:", styles.position,
            "Computed Top:", styles.top, "Computed Left:", styles.left, "Computed Bottom:", styles.bottom, "Computed Right:", styles.right,
            "OffsetTop:", processingIndicator.offsetTop, "OffsetLeft:", processingIndicator.offsetLeft);
    } else {
        console.error("processingIndicator element NOT FOUND at DOMContentLoaded!");
    }

    if (userInput && sendButton) {
        userInput.focus();
        sendButton.addEventListener('click', sendMessage);
        userInput.addEventListener('keypress', function(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                sendMessage();
            }
        });
    } else {
        console.error("userInput or sendButton element NOT FOUND at DOMContentLoaded!");
    }

    if (processingIndicator && aiCoreDisplay && aiCoreStatusText) {
        setWeiboState('idle');
    } else {
        console.error("One or more core elements for Weibo state are missing. Cannot set initial state.");
        if(aiCoreStatusText) aiCoreStatusText.textContent = "UI Error: Core elements missing.";
    }
});
