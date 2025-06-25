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
    processingIndicator.style.transform = '';
    processingIndicator.style.top = '';
    processingIndicator.style.left = '';
    processingIndicator.style.bottom = '';
    processingIndicator.style.right = '';

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
            body: JSON.stringify({
                message: messageText,
                user_id: "user_static_test_01"
            }),
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

    const maxX = parentRect.width - currentScaledWidth;
    const maxY = parentRect.height - currentScaledHeight;

    let targetX = Math.random() * Math.max(0, maxX);
    let targetY = Math.random() * Math.max(0, maxY);

    targetX = Math.max(0, Math.min(targetX, maxX));
    targetY = Math.max(0, Math.min(targetY, maxY));

    console.log(`Animating Idle (top/left + scale): Scale=${randomScale.toFixed(2)}, TargetX=${targetX.toFixed(2)}, TargetY=${targetY.toFixed(2)} (maxX:${maxX.toFixed(2)}, maxY:${maxY.toFixed(2)}) ScaledW:${currentScaledWidth.toFixed(2)}`);

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

    // Ensure global consts are assigned if not already (due to script loading order)
    // This is a fallback; ideally, scope these within DOMContentLoaded or pass as parameters.
    if (!window.chatLogArea) window.chatLogArea = document.getElementById('chatLogArea');
    if (!window.userInput) window.userInput = document.getElementById('userInput');
    if (!window.sendButton) window.sendButton = document.getElementById('sendButton');
    if (!window.aiCoreStatusText) window.aiCoreStatusText = document.getElementById('aiCoreStatusText');
    if (!window.processingIndicator) window.processingIndicator = document.getElementById('processingIndicator');
    if (!window.aiCoreDisplay) window.aiCoreDisplay = document.getElementById('aiCoreDisplay');

    // New Help Menu elements - ensure these are also globally accessible if needed by functions outside this event
    if (!window.helpButton) window.helpButton = document.getElementById('helpButton');
    if (!window.helpMenuPopup) window.helpMenuPopup = document.getElementById('helpMenuPopup');


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

    // Help Menu Logic
    if (helpButton && helpMenuPopup && userInput) {
        helpButton.addEventListener('click', function(event) {
            event.stopPropagation();
            const isHidden = helpMenuPopup.style.display === 'none' || helpMenuPopup.style.display === '';
            if (isHidden) {
                const buttonRect = helpButton.getBoundingClientRect();
                helpMenuPopup.style.display = 'block';
                const menuRect = helpMenuPopup.getBoundingClientRect();

                let leftPosition = buttonRect.left;
                if (leftPosition + menuRect.width > window.innerWidth - 20) {
                    leftPosition = buttonRect.right - menuRect.width;
                }

                helpMenuPopup.style.left = `${Math.max(10, leftPosition)}px`;
                helpMenuPopup.style.bottom = `${window.innerHeight - buttonRect.top + 10}px`;
                helpMenuPopup.style.top = 'auto';
            } else {
                helpMenuPopup.style.display = 'none';
            }
        });

        helpMenuPopup.addEventListener('click', function(event) {
            event.stopPropagation();
            const target = event.target.closest('li[data-command]'); // Handle click on li or its children
            if (target) {
                let command = target.dataset.command;
                userInput.value = command;
                userInput.focus();

                if (command.endsWith(' ')) {
                    // If command expects an argument, place cursor at the end
                    userInput.setSelectionRange(command.length, command.length);
                    // Optionally, you could show the data-placeholder in some way here too
                }
                helpMenuPopup.style.display = 'none';
            }
        });
    } else {
        console.error("Help menu button or popup not found at DOMContentLoaded!");
    }

    if (processingIndicator && aiCoreDisplay && aiCoreStatusText) {
        setWeiboState('idle');
    } else {
        console.error("One or more core elements for Weibo state are missing. Cannot set initial state.");
        if(aiCoreStatusText) aiCoreStatusText.textContent = "UI Error: Core elements missing.";
    }
});

// Global listeners for closing the help menu - MUST be outside DOMContentLoaded if helpMenuPopup is global
document.addEventListener('click', function(event) {
    // Ensure helpMenuPopup and helpButton are resolved before using them
    const currentHelpMenuPopup = window.helpMenuPopup || document.getElementById('helpMenuPopup');
    const currentHelpButton = window.helpButton || document.getElementById('helpButton');

    if (currentHelpMenuPopup && currentHelpMenuPopup.style.display === 'block') {
        if (currentHelpButton && !currentHelpButton.contains(event.target) && !currentHelpMenuPopup.contains(event.target)) {
            currentHelpMenuPopup.style.display = 'none';
        }
    }
});

document.addEventListener('keydown', function(event) {
    const currentHelpMenuPopup = window.helpMenuPopup || document.getElementById('helpMenuPopup');
    if (event.key === 'Escape' && currentHelpMenuPopup && currentHelpMenuPopup.style.display === 'block') {
        currentHelpMenuPopup.style.display = 'none';
    }
});
