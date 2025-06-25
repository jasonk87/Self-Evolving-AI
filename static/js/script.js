const chatLogArea = document.getElementById('chatLogArea');
const userInput = document.getElementById('userInput');
const sendButton = document.getElementById('sendButton');
const aiCoreStatusText = document.getElementById('aiCoreStatusText');
const processingIndicator = document.getElementById('processingIndicator');
const aiCoreDisplay = document.getElementById('aiCoreDisplay');

let idleAnimationTimeoutId = null;
let isWeiboWorkingInBackground = false;

function setWeiboState(state) {
    // const currentState = getCurrentWeiboState(); // Not strictly needed here anymore with the switch
    if (!processingIndicator) {
        console.error("setWeiboState: processingIndicator not found!");
        return;
    }

    // Clear all potential state classes and inline transform
    processingIndicator.classList.remove('idle', 'weibo-working-idle', 'weibo-thinking', 'weibo-talking', 'weibo-in-work-zone');
    processingIndicator.style.transform = ''; // Clear inline transforms to let CSS classes take full effect

    switch (state) {
        case 'idle':
            processingIndicator.classList.add('idle');
            startIdleAnimation();
            break;
        case 'background-processing': // New state for being in the work zone
            processingIndicator.classList.add('weibo-in-work-zone'); // Positions it
            processingIndicator.classList.add('weibo-working-idle'); // For the distinct glow
            stopIdleAnimation(); // No general drifting in the work zone
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
    // Only animate if in 'idle' state (not 'weibo-working-idle' if that's meant to be static in work zone)
    // OR if 'weibo-working-idle' is also meant to drift (current plan is static in zone)
    if (!processingIndicator.classList.contains('idle')) { // Only drift if in pure 'idle'
        return;
    }

    const parentRect = aiCoreDisplay.getBoundingClientRect();
    const baseIndicatorWidth = 80;
    const baseIndicatorHeight = 80;

    if (!parentRect || parentRect.width === 0 || parentRect.height === 0) {
         console.warn("aiCoreDisplay has no dimensions or not found, cannot animate idle Weibo.");
         idleAnimationTimeoutId = setTimeout(animateIdleWeibo, 5000);
         return;
    }

    const randomScale = 0.7 + Math.random() * 0.6;
    const currentScaledWidth = baseIndicatorWidth * randomScale;
    const currentScaledHeight = baseIndicatorHeight * randomScale;

    const minX = currentScaledWidth / 2;
    const maxX = parentRect.width - (currentScaledWidth / 2);

    const minY = currentScaledHeight / 2;
    const maxY = parentRect.height - (currentScaledHeight / 2);

    const targetableWidth = Math.max(0, maxX - minX);
    // Removed duplicate const targetableWidth = Math.max(0, maxX - minX);
    const targetableHeight = Math.max(0, maxY - minY);

    let dTargetX = minX + (Math.random() * targetableWidth);
    let dTargetY = minY + (Math.random() * targetableHeight);

    // Aggressive clamping
    dTargetX = Math.max(minX, Math.min(dTargetX, maxX));
    dTargetY = Math.max(minY, Math.min(dTargetY, maxY));

    // DEBUG: Forcing scale to 1 during idle to isolate translation issues
    const fixedScale = 1;
    console.log(`Animating Idle: Scale=${fixedScale.toFixed(2)} (was ${randomScale.toFixed(2)}), TargetX=${dTargetX.toFixed(2)} (minX:${minX.toFixed(2)}, maxX:${maxX.toFixed(2)}), TargetY=${dTargetY.toFixed(2)} (minY:${minY.toFixed(2)}, maxY:${maxY.toFixed(2)}) ParentW:${parentRect.width.toFixed(2)}, ParentH:${parentRect.height.toFixed(2)} ScaledW:${currentScaledWidth.toFixed(2)}`);

    processingIndicator.style.transform = `translate(${dTargetX}px, ${dTargetY}px) scale(${fixedScale})`;

    const randomDelay = 3000 + Math.random() * 4000;
    idleAnimationTimeoutId = setTimeout(animateIdleWeibo, randomDelay);
}

function startIdleAnimation() {
    stopIdleAnimation();
    setTimeout(() => {
        if (processingIndicator && aiCoreDisplay) {
            const currentDisplayState = getCurrentWeiboState();
            // Only start random drift if in 'idle' state.
            // 'background-processing' (work zone) should be static or have its own animation.
            if (currentDisplayState === 'idle') {
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
    if (processingIndicator.classList.contains('weibo-in-work-zone')) return 'background-processing'; // Represents being in the work zone
    if (processingIndicator.classList.contains('idle')) return 'idle'; // Pure idle (drifting)
    // Note: weibo-working-idle is now just for glow, not a primary state for positioning/animation loop
    return null;
}

function toggleBackgroundWork() {
    isWeiboWorkingInBackground = !isWeiboWorkingInBackground;
    const currentLogicalState = getCurrentWeiboState();

    // If user is actively interacting, don't change Weibo's main state,
    // but we can still update the text and log the background status.
    // The visual glow change for background work will apply if she returns to an idle-type state.
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

    // Re-assign global consts if they were defined outside and are null due to script loading order
    // This is a fallback, ideally they are defined here or passed into functions.
    if (!chatLogArea) chatLogArea = document.getElementById('chatLogArea');
    if (!userInput) userInput = document.getElementById('userInput');
    if (!sendButton) sendButton = document.getElementById('sendButton');
    if (!aiCoreStatusText) aiCoreStatusText = document.getElementById('aiCoreStatusText');
    if (!processingIndicator) processingIndicator = document.getElementById('processingIndicator');
    if (!aiCoreDisplay) aiCoreDisplay = document.getElementById('aiCoreDisplay');

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

    if (userInput && sendButton) { // Ensure buttons/inputs exist before adding listeners
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
