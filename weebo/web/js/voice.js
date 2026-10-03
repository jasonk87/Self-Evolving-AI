// Voice: speech-to-text for the composer and text-to-speech for Weebo's replies.
// Adapted from Weebo 1.x's voice module: same Web Speech recognition flow, but
// DOM-agnostic, and speech now uses the browser's own voices so it works offline
// and lets Weebo's mouth move while it talks.
import { director } from "./avatar.js";
import { plainText } from "./markdown.js";

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let listening = false;
let settings = { speak_replies: false, voice_name: "", rate: 1.05, pitch: 1.25 };

export const voiceSupport = { stt: Boolean(Recognition), tts: "speechSynthesis" in window };

export function configureVoice(next) {
  settings = { ...settings, ...(next || {}) };
}

export function listVoices() {
  return voiceSupport.tts ? speechSynthesis.getVoices() : [];
}

export function startListening({ onPartial, onFinal, onState }) {
  if (!Recognition) return false;
  if (listening) { recognition?.stop(); return true; }
  recognition = new Recognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = navigator.language || "en-US";
  let failure = null; // onerror is followed by onend; report the error once, at the end
  recognition.onstart = () => { listening = true; onState?.(true); director.setBase("listening"); };
  recognition.onend = () => { listening = false; onState?.(false, failure); };
  recognition.onerror = (event) => { failure = event.error || "error"; };
  recognition.onresult = (event) => {
    let interim = "", final = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const transcript = event.results[i][0].transcript;
      if (event.results[i].isFinal) final += transcript; else interim += transcript;
    }
    if (interim) onPartial?.(interim);
    if (final.trim()) onFinal?.(final.trim());
  };
  recognition.start();
  return true;
}

export function isListening() { return listening; }

export function speak(markdown, { force = false } = {}) {
  if (!voiceSupport.tts || (!settings.speak_replies && !force)) return;
  const text = plainText(markdown).slice(0, 1200);
  if (!text) return;
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = Number(settings.rate) || 1;
  utterance.pitch = Number(settings.pitch) || 1;
  const voice = listVoices().find((v) => v.name === settings.voice_name);
  if (voice) utterance.voice = voice;
  utterance.onstart = () => director.flash("talking", 60000);
  utterance.onend = () => director.flash("happy", 900);
  speechSynthesis.speak(utterance);
}

export function stopSpeaking() {
  if (voiceSupport.tts) speechSynthesis.cancel();
}
