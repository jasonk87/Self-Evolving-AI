
import os
import base64
import asyncio
import logging
from typing import Optional, Dict, Any, List
import pyautogui
from io import BytesIO
from PIL import Image

from ai_assistant.core.vision_service import VisionService
from ai_assistant import config

logger = logging.getLogger(__name__)

async def take_screenshot(mode: str = "desktop", url: Optional[str] = None) -> Dict[str, Any]:
    """
    Captures a screenshot of the host desktop or a specific web page.
    
    Args:
        mode (str): Capture mode. Options are "desktop" (host screen) or "web" (requires url).
        url (str, optional): The URL to capture if mode is "web".
        
    Returns:
        dict: A dictionary containing status and the base64 encoded image in an 'images' list.
    """
    try:
        if mode == "desktop":
            logger.info("Capturing desktop screenshot using pyautogui...")
            # Take screenshot using pyautogui
            # We use a thread since pyautogui might block or not be fully async-friendly in some setups
            def _capture_desktop():
                screenshot = pyautogui.screenshot()
                buffered = BytesIO()
                screenshot.save(buffered, format="PNG")
                return base64.b64encode(buffered.getvalue()).decode('utf-8')

            b64_image = await asyncio.to_thread(_capture_desktop)
            return {
                "status": "success",
                "message": "Desktop screenshot captured successfully.",
                "images": [b64_image]
            }

        elif mode == "web":
            if not url:
                return {"status": "error", "message": "URL is required for 'web' mode."}
            
            logger.info(f"Capturing web screenshot for URL: {url} using VisionService...")
            vision_service = VisionService()
            b64_image = await vision_service.capture_page_screenshot(url)
            
            if b64_image:
                return {
                    "status": "success",
                    "message": f"Web screenshot for {url} captured successfully.",
                    "images": [b64_image]
                }
            else:
                return {"status": "error", "message": f"Failed to capture screenshot for {url}."}

        else:
            return {"status": "error", "message": f"Invalid mode: {mode}. Use 'desktop' or 'web'."}

    except Exception as e:
        logger.error(f"Error in take_screenshot tool: {e}")
        return {"status": "error", "message": str(e)}
