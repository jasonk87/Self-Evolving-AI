import os
import hashlib
import base64
import json
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

# We use the built-in tool system
from ai_assistant.tools.tool_system import tool_system_instance

class TakeWebpageScreenshotSchema(BaseModel):
    url: str = Field(description="The full HTTP/HTTPS URL of the website to visit.")
    width: int = Field(default=1280, description="Viewport width.")
    height: int = Field(default=800, description="Viewport height.")

async def take_webpage_screenshot(url: str, width: int = 1280, height: int = 800) -> Dict[str, Any]:
    """
    Navigates to a webpage using a headless browser, takes a screenshot, and returns the base64 image data.
    This provides the agent with visual context of what a webpage looks like and acts as a Picture-in-Picture feed.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"success": False, "error": "Playwright is not installed. Run: pip install playwright && playwright install"}

    try:
        # Create screenshots dir if it doesn't exist
        os.makedirs("ai_assistant/core/data/screenshots", exist_ok=True)
        filename = f"ai_assistant/core/data/screenshots/screenshot_{hashlib.md5(url.encode()).hexdigest()}.png"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": width, "height": height})

            # Navigate to the URL
            await page.goto(url, wait_until="networkidle", timeout=15000)

            # Take the screenshot
            await page.screenshot(path=filename)
            await browser.close()

        with open(filename, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')

        return {
            "success": True,
            "result": f"Successfully captured screenshot of {url} and saved to {filename}",
            "base64_image": encoded_string,
            "filename": filename
        }

    except Exception as e:
        return {"success": False, "error": f"Failed to capture screenshot: {str(e)}"}

tool_system_instance.register_tool(
    tool_name="take_webpage_screenshot",
    description="Takes a live screenshot of a website to visually inspect its contents or layout. Returns a base64 encoded image.",
    module_path=__name__,
    function_name_in_module="take_webpage_screenshot",
    func_callable=take_webpage_screenshot,
    pydantic_model=TakeWebpageScreenshotSchema
)
