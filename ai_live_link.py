
import asyncio
import os
import json
import base64
import logging
import threading
import traceback
import time
from datetime import datetime
from typing import List, Optional
import audioop # For Kill Switch RMS calculation

import websockets
import pyaudio
import mss
from PIL import Image
import io
import aiohttp

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai_live_link")
fh = logging.FileHandler('live_debug.log')
fh.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)

from dotenv import load_dotenv
load_dotenv()
import ai_assistant.config as config
from ai_assistant.llm_interface.gemini_client import invoke_split_brain_async

# --- Configuration ---
GEMINI_API_KEY = config._get_api_key() or os.environ.get("GEMINI_API_KEY")
model = "models/gemini-2.0-flash-exp"
uri = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={GEMINI_API_KEY}"

# Audio Settings
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 24000  # Gemini Native Rate favorable
CHUNK = 4096 # Increased from 512 to reduce WS message frequency (Rate Limit optimization)

# Debrief Prompt
DEBRIEF_PROMPT = """
Summarize the technical decisions, code fixes, and insights from this live session into bullet points for the long-term knowledge base.
Focus on:
- High-level architectural changes discussed.
- Specific code modifications made or suggested.
- Key technical insights or debugging discoveries.
"""

class LiveSession:
    def __init__(self, on_save_callback=None, system_instruction=None):
        self._running = False
        self._ws = None
        self.audio = pyaudio.PyAudio()
        
        # Log Audio Devices for Debugging
        logger.info("Listing Audio Devices:")
        for i in range(self.audio.get_device_count()):
            dev = self.audio.get_device_info_by_index(i)
            logger.info(f"Dev {i}: {dev['name']} (In:{dev['maxInputChannels']} Out:{dev['maxOutputChannels']})")
            
        self.stream_in = None
        self.stream_out = None
        self.transcript: List[str] = []
        self._stop_event = asyncio.Event()
        self.status = "idle" # idle, listening, speaking
        self.on_save_callback = on_save_callback
        self.system_instruction = system_instruction
        
        # Auto-Drive State
        self.auto_drive = False
        self.voice_kill_threshold = 1500 # RMS amplitude to trigger kill switch

    async def run(self):
        """Main asyncio loop for the session."""
        if not GEMINI_API_KEY:
            logger.error("GEMINI_API_KEY not found in environment.")
            return

        self._running = True
        self.status = "connecting"
        logger.info(f"Connecting to Gemini Live API: {uri}")

        try:
            async with websockets.connect(uri) as ws:
                self._ws = ws
                self.status = "connected"
                logger.info("Connected to Gemini Live API.")
                
                # Send Initial Setup
                await self._send_setup_message()

                # Start Media Tasks
                await asyncio.gather(
                    self._audio_input_loop(),
                    self._video_input_loop(),
                    self._receive_loop(),
                    self._keep_alive() # Optional, depending on WS behavior
                )
        except Exception as e:
            logger.error(f"Live Session Error: {e}")
            traceback.print_exc()
        finally:
            self._cleanup()
            await self._generate_debrief()
            self.status = "stopped"

    async def _send_setup_message(self):
        setup_msg = {
            "setup": {
                "model": model,
                "generation_config": {
                    "response_modalities": ["AUDIO"], # AUDIO only for stability
                    "speech_config": {
                        "voice_config": {"prebuilt_voice_config": {"voice_name": "Aoede"}} 
                    }
                }
            }
        }
        
        if self.system_instruction:
            # Append Auto-Drive Instructions
            auto_drive_instr = "\n\nCAPABILITY: Auto-Drive. If the user says 'Auto-Drive' or 'Play the game', you must enter Continuous Play mode. Execute moves continuously. I will prompt you with 'Move complete. Scan the screen.' after each action."
            
            setup_msg["setup"]["system_instruction"] = {
                "parts": [{"text": self.system_instruction + auto_drive_instr}]
            }
            
        await self._ws.send(json.dumps(setup_msg))

    async def _audio_input_loop(self):
        """Reads mic audio and sends to Gemini. Handles Kill Switch."""
        self.stream_in = self.audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK
        )
        
        logger.info("Audio Input Loop Started.")
        try:
            while self._running:
                data = self.stream_in.read(CHUNK, exception_on_overflow=False)
                if not data:
                    break

                # --- Kill Switch Check ---
                if self.auto_drive:
                    rms = audioop.rms(data, 2) # Width=2 for paInt16
                    if rms > self.voice_kill_threshold:
                        logger.info(f"KILL SWITCH TRIGGERED (RMS: {rms})")
                        self.auto_drive = False
                        # Optional: Send a text signal to AI to stop/acknowledge
                        # await self._send_text("USER INTERRUPTED! STOP AUTO-DRIVE.") 

                # Send RealtimeInput
                msg = {
                    "realtime_input": {
                        "media_chunks": [
                            {
                                "mime_type": "audio/pcm",
                                "data": base64.b64encode(data).decode("utf-8")
                            }
                        ]
                    }
                }
                await self._ws.send(json.dumps(msg))
                await asyncio.sleep(0) # Yield
        except Exception as e:
            logger.error(f"Audio Input Error: {e}")

    async def _video_input_loop(self):
        """Captures screen and sends to Gemini every 1 second."""
        sct = mss.mss()
        monitor = sct.monitors[1] # Primary monitor
        
        logger.info("Video Input Loop Started.")
        try:
            while self._running:
                # Capture
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                
                # Resize to 640x360
                img = img.resize((640, 360))
                
                # Convert to JPEG bytes
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=50)
                image_bytes = buf.getvalue()
                
                # Send RealtimeInput
                msg = {
                    "realtime_input": {
                        "media_chunks": [
                            {
                                "mime_type": "image/jpeg",
                                "data": base64.b64encode(image_bytes).decode("utf-8")
                            }
                        ]
                    }
                }
                await self._ws.send(json.dumps(msg))
                
                await asyncio.sleep(4.0) # Reduced to 0.25 FPS to save quota
        except Exception as e:
            logger.error(f"Video Input Error: {e}")

    async def _receive_loop(self):
        """Receives audio and text from Gemini."""
        self.stream_out = self.audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            output=True
        )
        
        logger.info("Receive Loop Started.")
        try:
            async for message in self._ws:
                if not self._running:
                    break
                    
                data = json.loads(message)
                
                # Handle Audio Output
                if "serverContent" in data:
                    model_turn = data["serverContent"].get("modelTurn")
                    if model_turn:
                        parts = model_turn.get("parts", [])
                        for part in parts:
                            # Inline Data (Audio)
                            if "inlineData" in part:
                                mime = part["inlineData"].get("mimeType")
                                if "audio" in mime:
                                    audio_bytes = base64.b64decode(part["inlineData"]["data"])
                                    self._play_audio(audio_bytes)
                                    self.status = "speaking"
                            
                            # Text / Function Calls (if configured)
                            if "text" in part:
                                text_chunk = part["text"]
                                self.transcript.append(f"AI: {text_chunk}")
                                
                                # Check for Activation Triggers in text response as backup
                                # (Ideally simple keyword spotting on user audio would be faster, but strict logic requested)
                                if "auto-drive" in text_chunk.lower() or "auto drive" in text_chunk.lower():
                                    if "start" in text_chunk.lower() or "activating" in text_chunk.lower():
                                        self.auto_drive = True
                                        logger.info("Auto-Drive Activated by AI Response")

                    # Turn Complete?
                    if data["serverContent"].get("turnComplete"):
                        self.status = "listening" # Back to listening
                        
                        # --- AUTO-DRIVE LOOP HOOK ---
                        if self.auto_drive:
                            logger.info("Auto-Drive Active: Triggering next move loop...")
                            await asyncio.sleep(1.5) # Wait for UI animation/Move execution simulation
                            
                            # Send Re-Prompt to force next move
                            prompt_msg = {
                                "client_content": {
                                    "turns": [
                                        {
                                            "role": "user",
                                            "parts": [{"text": "Move complete. Scan the screen. What is the next move?"}]
                                        }
                                    ],
                                    "turn_complete": True
                                }
                            }
                            await self._ws.send(json.dumps(prompt_msg))
                            logger.info("Sent Auto-Drive Re-Prompt.")

        except Exception as e:
            logger.error(f"Receive Loop Error: {e}")

    def _play_audio(self, audio_data):
        if self.stream_out:
            self.stream_out.write(audio_data)

    async def _keep_alive(self):
        while self._running:
            await asyncio.sleep(10)
            # Websocket lib handles ping/pong usually, this is just a placeholder if needed.

    def _cleanup(self):
        self._running = False
        if self.stream_in:
            self.stream_in.stop_stream()
            self.stream_in.close()
        if self.stream_out:
            self.stream_out.stop_stream()
            self.stream_out.close()
        self.audio.terminate()
        logger.info("Live Session Cleaned Up.")

    async def _generate_debrief(self):
        """Generates a text summary of the session."""
        logger.info("Generating Session Debrief...")
        if not self.transcript:
            logger.info("No transcript to debrief.")
            return

        full_text = "\n".join(self.transcript)
        
        full_text = "\n".join(self.transcript)
        
        try:
            summary, _ = await invoke_split_brain_async(
                DEBRIEF_PROMPT + "\n\nTRANSCRIPT:\n" + full_text,
                context_text="Generating Live Session Debrief"
            )
            
            logger.info(f"Debrief Generated:\n{summary}")
            
            # Invoke Callback if present
            if self.on_save_callback:
                try:
                    self.on_save_callback(summary)
                    logger.info("Debrief saved via callback.")
                except Exception as cb_e:
                    logger.error(f"Callback error: {cb_e}")

            # Backup to file
            with open("live_session_debriefs.txt", "a") as f:
                f.write(f"\n--- Debrief {datetime.now()} ---\n{summary}\n")
                
        except Exception as e:
            logger.error(f"Debrief Request Error: {e}")

# --- Global Manager ---
current_session: Optional[LiveSession] = None
session_thread: Optional[threading.Thread] = None

def start_live_mode(on_save_callback=None, system_instruction=None):
    global current_session, session_thread
    if current_session and current_session._running:
        logger.warning("Live Mode already running.")
        return

    current_session = LiveSession(on_save_callback=on_save_callback, system_instruction=system_instruction)
    
    def run_loop():
        asyncio.run(current_session.run())

    session_thread = threading.Thread(target=run_loop, daemon=True)
    session_thread.start()

def stop_live_mode():
    global current_session
    if current_session:
        current_session._running = False
        # Thread will exit after loops break

def get_status():
    if current_session:
        return current_session.status
    return "inactive"
