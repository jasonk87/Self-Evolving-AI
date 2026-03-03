
import requests
import logging
from ai_assistant.config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL_ID

logger = logging.getLogger(__name__)

def generate_speech(text: str) -> bytes:
    """
    Generates speech from text using ElevenLabs API.
    Returns the audio content as bytes (MP3).
    """
    if not ELEVENLABS_API_KEY:
        logger.error("ElevenLabs API Key not found.")
        return None

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY
    }

    data = {
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.5
        }
    }

    try:
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 200:
            return response.content
        else:
            logger.error(f"ElevenLabs API Error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error calling ElevenLabs API: {e}")
        return None
