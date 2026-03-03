
// static/js/modules/terminal.js
import { socket } from './socket_client.js';

let isWaitingForTerminal = false;

export function setWaitingForTerminal(val) {
    isWaitingForTerminal = val;
}

export function getWaitingForTerminal() {
    return isWaitingForTerminal;
}

export function handleTerminalResponse(responseText, outputDiv) {
    if (!outputDiv) return;

    // Remove "Processing..." line
    if (outputDiv.lastChild && outputDiv.lastChild.textContent === 'Processing...') {
        outputDiv.removeChild(outputDiv.lastChild);
    }

    const outputLine = document.createElement('div');
    outputLine.className = 'line output';
    outputLine.innerHTML = responseText.replace(/\n/g, '<br>');
    outputDiv.appendChild(outputLine);
    outputDiv.scrollTop = outputDiv.scrollHeight;
}

const aiSuggestions = [
    "I noticed an error in the terminal. Do you want me to see if I can help?",
    "That command didn't work as expected. Should I investigate?",
    "It looks like something went wrong. Want me to take a look?",
    "Error detected. Do you want me to try and fix it?"
];

function showAiAssistanceSuggestion(originalCmd, errorContext, outputDiv, currentSessionId) {
    if (!outputDiv) return;

    const suggestion = aiSuggestions[Math.floor(Math.random() * aiSuggestions.length)];
    const div = document.createElement('div');
    div.className = 'line system ai-suggestion';
    div.style.marginTop = '10px';
    div.style.padding = '10px';
    div.style.backgroundColor = 'rgba(255, 255, 255, 0.05)';
    div.style.borderRadius = '5px';
    div.style.borderLeft = '3px solid var(--accent-color)';

    div.innerHTML = `
        <div style="margin-bottom:8px;">🤖 ${suggestion}</div>
        <button class="btn-xs" style="padding: 4px 8px; cursor: pointer; background: var(--accent-color); border: none; color: white; border-radius: 4px;">Yes, help me fix this</button>
    `;

    const btn = div.querySelector('button');
    btn.onclick = () => {
        div.remove();
        const message = `I ran the command \`${originalCmd}\` and it failed. Here is the output:\n\`\`\`\n${errorContext}\n\`\`\`\nCan you help me fix this?`;

        if (socket) {
            isWaitingForTerminal = true;
            socket.emit('message', { session_id: currentSessionId, message: message });

            const loading = document.createElement('div');
            loading.className = 'line system';
            loading.innerText = 'AI Analysis running...';
            outputDiv.appendChild(loading);
        }
    };

    outputDiv.appendChild(div);
    outputDiv.scrollTop = outputDiv.scrollHeight;
}

export async function sendTerminalCommand(inputEl, outputDiv, currentProject, currentSessionId) {
    if (!inputEl) return;
    const cmd = inputEl.value.trim();
    if (!cmd) return;

    // Display
    const cmdLine = document.createElement('div');
    cmdLine.className = 'line command';
    cmdLine.innerText = `$ ${cmd}`;
    if (outputDiv) {
        outputDiv.appendChild(cmdLine);
        outputDiv.scrollTop = outputDiv.scrollHeight;
    }
    inputEl.value = '';
    isWaitingForTerminal = false;

    // Loading
    let loadingId = 'term-loading-' + Date.now();
    if (outputDiv) {
        const loading = document.createElement('div');
        loading.id = loadingId;
        loading.className = 'line system';
        loading.innerText = 'Executing...';
        outputDiv.appendChild(loading);
        outputDiv.scrollTop = outputDiv.scrollHeight;
    }

    try {
        const res = await fetch('/api/terminal/exec', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: cmd, project_name: currentProject })
        });
        const data = await res.json();

        // Remove loading
        const loadingEl = document.getElementById(loadingId);
        if (loadingEl) loadingEl.remove();

        if (data.success) {
            if (data.stdout) {
                const out = document.createElement('div');
                out.className = 'line output';
                out.innerHTML = data.stdout.replace(/\n/g, '<br>');
                outputDiv.appendChild(out);
            }
            if (data.stderr) {
                const err = document.createElement('div');
                err.className = 'line output error';
                err.style.color = '#ff6b6b';
                err.innerHTML = data.stderr.replace(/\n/g, '<br>');
                outputDiv.appendChild(err);
            }
            outputDiv.scrollTop = outputDiv.scrollHeight;

            if (data.returncode !== 0 || (data.stderr && data.stderr.trim().length > 0)) {
                showAiAssistanceSuggestion(cmd, `Command failed with code ${data.returncode}.\nStderr: ${data.stderr}\nStdout: ${data.stdout}`, outputDiv, currentSessionId);
            }
        } else {
            const err = document.createElement('div');
            err.className = 'line error';
            err.innerText = "Execution Error: " + (data.error || "Unknown error");
            outputDiv.appendChild(err);
            showAiAssistanceSuggestion(cmd, "Execution Error: " + data.error, outputDiv, currentSessionId);
            outputDiv.scrollTop = outputDiv.scrollHeight;
        }
    } catch (e) {
        const loadingEl = document.getElementById(loadingId);
        if (loadingEl) loadingEl.remove();

        if (outputDiv) {
            const err = document.createElement('div');
            err.className = 'line error';
            err.innerText = "Network Error: " + e.message;
            outputDiv.appendChild(err);
            outputDiv.scrollTop = outputDiv.scrollHeight;
        }
    }
}
