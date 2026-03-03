
// static/js/modules/voice.js

let recognition = null;
let isListening = false;
let autoSpeakEnabled = false;

// Callbacks
let onResultCallback = null;

export function initVoice(onResult) {
    onResultCallback = onResult;
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        recognition = new SpeechRecognition();
        recognition.continuous = false;
        recognition.interimResults = false;
        recognition.lang = 'en-US';

        const micBtn = document.getElementById('mic-btn');
        const chatInput = document.getElementById('chat-input');

        recognition.onstart = () => {
            isListening = true;
            if (micBtn) micBtn.classList.add('listening');
            if (chatInput) chatInput.placeholder = "Listening...";
        };

        recognition.onend = () => {
            isListening = false;
            if (micBtn) micBtn.classList.remove('listening');
            if (chatInput) chatInput.placeholder = "Instructions...";
        };

        recognition.onresult = (event) => {
            const result = event.results[0];
            const transcript = result[0].transcript;
            if (!result.isFinal || !transcript.trim()) return;

            if (onResultCallback) onResultCallback(transcript);
        };

        recognition.onerror = (event) => {
            console.warn("Speech Recognition Error:", event.error);
            isListening = false;
            if (micBtn) micBtn.classList.remove('listening');
        };
    }
}

export function toggleListening() {
    if (!recognition) return;
    if (isListening) recognition.stop();
    else recognition.start();
}

export function setAutoSpeak(enabled) {
    autoSpeakEnabled = enabled;
}

export function getAutoSpeak() {
    return autoSpeakEnabled;
}

export function speakText(text) {
    if (!text) return;

    const cleanText = text.replace(/```[\s\S]*?```/g, "Code block omitted.")
        .replace(/`([^`]+)`/g, "$1")
        .replace(/\*/g, "");

    fetch('/api/speak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: cleanText.substring(0, 1000) })
    })
        .then(response => {
            if (!response.ok) throw new Error("TTS Failed");
            return response.blob();
        })
        .then(blob => {
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audio.play();
            audio.onended = () => URL.revokeObjectURL(url);
        })
        .catch(err => {
            console.error("TTS Error, fallback:", err);
            if ('speechSynthesis' in window) {
                const utterance = new SpeechSynthesisUtterance(cleanText);
                window.speechSynthesis.speak(utterance);
            }
        });
}
