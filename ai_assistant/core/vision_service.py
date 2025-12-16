
import os
import base64
import logging
import asyncio
import json
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright
from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async
import ai_assistant.config as config

logger = logging.getLogger(__name__)

class VisionService:
    """
    The Optic Nerve.
    Provides capabilities to capture screenshots of web pages (files or URLs)
    and analyze them using the Gemini multimodal model.
    """

    def __init__(self):
        pass

    def _get_hud_script_path(self):
        return os.path.join(os.path.dirname(__file__), 'hud_injector.js')

    async def _inject_hud(self, page):
        """Injects the Visual HUD if Ghost Mode is enabled."""
        if config.GHOST_MODE:
            try:
                hud_path = self._get_hud_script_path()
                if os.path.exists(hud_path):
                    await page.add_init_script(path=hud_path)
                    logger.info("VisionService: HUD injected for Ghost Mode.")
                else:
                    logger.warning(f"VisionService: HUD script not found at {hud_path}")
            except Exception as e:
                logger.error(f"VisionService: Failed to inject HUD: {e}")

    async def capture_page_screenshot(self, file_path_or_url: str) -> Optional[str]:
        """
        Captures a screenshot of the given file path or URL.
        Returns the base64 encoded string of the PNG screenshot.
        """
        playwright = None
        browser = None
        try:
            playwright = await async_playwright().start()

            headless_mode = not config.GHOST_MODE
            slow_mo = config.BROWSER_SLOW_MO if config.GHOST_MODE else 0

            browser = await playwright.chromium.launch(headless=headless_mode, slow_mo=slow_mo)

            if config.GHOST_MODE:
                page = await browser.new_page(viewport={'width': 1280, 'height': 720})
            else:
                page = await browser.new_page()

            await self._inject_hud(page)

            # Handle local files specifically if needed, or assume standard URL structure
            target_url = file_path_or_url
            if not target_url.startswith("http") and not target_url.startswith("file://"):
                # If it's a local file path, ensure it's absolute and add file:// protocol
                abs_path = os.path.abspath(target_url)
                target_url = f"file://{abs_path}"

            logger.info(f"VisionService: Navigating to {target_url}")
            await page.goto(target_url, wait_until="networkidle", timeout=10000)

            # Additional small wait to ensure rendering settles
            await asyncio.sleep(1)

            screenshot_bytes = await page.screenshot(type="png", full_page=True)
            base64_screenshot = base64.b64encode(screenshot_bytes).decode('utf-8')

            return base64_screenshot

        except Exception as e:
            logger.error(f"VisionService: Error capturing screenshot for {file_path_or_url}: {e}")
            return None
        finally:
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()

    async def scrape_page_text(self, url: str) -> Optional[str]:
        """
        Navigates to the URL and extracts the visible text content from the body.
        Useful for reading documentation or articles efficiently.
        """
        playwright = None
        browser = None
        try:
            playwright = await async_playwright().start()

            headless_mode = not config.GHOST_MODE
            slow_mo = config.BROWSER_SLOW_MO if config.GHOST_MODE else 0

            browser = await playwright.chromium.launch(headless=headless_mode, slow_mo=slow_mo)

            if config.GHOST_MODE:
                page = await browser.new_page(viewport={'width': 1280, 'height': 720})
            else:
                page = await browser.new_page()

            logger.info(f"VisionService: Scraping text from {url}")

            await self._inject_hud(page)

            await page.goto(url, wait_until="domcontentloaded", timeout=30000) # 30s timeout default, can be overridden by caller if we passed it

            # Extract text
            text_content = await page.inner_text("body")
            return text_content

        except Exception as e:
            logger.error(f"VisionService: Error scraping text from {url}: {e}")
            return None
        finally:
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()

    async def click_element(self, page, selector: str):
        """
        Performs a human-like click on an element.
        Should be used when an active page object is available.
        """
        try:
            if config.GHOST_MODE:
                # 1. Move mouse to element to trigger Ghost Cursor
                locator = page.locator(selector).first
                box = await locator.bounding_box()
                if box:
                    x = box['x'] + box['width'] / 2
                    y = box['y'] + box['height'] / 2
                    await page.mouse.move(x, y)

                # 2. Highlight target using element handle (works with complex selectors)
                await locator.evaluate("el => window.highlightTargetElement(el)")

                # 3. Wait for visual effect
                await page.wait_for_timeout(300)

            # 4. Perform Click
            await page.click(selector)

        except Exception as e:
            logger.error(f"VisionService: Error clicking element {selector}: {e}")
            raise

    async def type_text(self, page, selector: str, text: str):
        """
        Performs a human-like typing action.
        """
        try:
            if config.GHOST_MODE:
                 # 1. Move mouse to element
                locator = page.locator(selector).first
                box = await locator.bounding_box()
                if box:
                    x = box['x'] + box['width'] / 2
                    y = box['y'] + box['height'] / 2
                    await page.mouse.move(x, y)

                # 2. Highlight target
                await locator.evaluate("el => window.highlightTargetElement(el)")
                await page.wait_for_timeout(300)

            # 3. Click to focus
            await page.click(selector)

            # 4. Type text
            await page.type(selector, text, delay=50 if config.GHOST_MODE else 0)

        except Exception as e:
            logger.error(f"VisionService: Error typing text into {selector}: {e}")
            raise

    async def analyze_visuals(self, image_data: str, context: str) -> Dict[str, Any]:
        """
        Analyzes the provided base64 image data using Gemini.
        Returns a JSON dictionary with status, issues, and suggestions.
        """
        if not image_data:
            return {"status": "ERROR", "issues": ["No image data provided"], "suggestion": None}

        system_prompt = (
            f"You are a UI/UX QA Expert. Analyze this screenshot of a web application.\n"
            f"Context: {context}\n"
            f"Check for:\n"
            f"1. Layout correctness (alignment, spacing, centering).\n"
            f"2. Visual bugs (overlapping text, broken images, invisible text, contrast issues).\n"
            f"3. Responsiveness issues (if apparent).\n"
            f"4. Aesthetic quality (is it professional?).\n\n"
            f"Return a valid JSON object ONLY, with this structure:\n"
            f"{{\n"
            f"  \"status\": \"PASS\" or \"FAIL\",\n"
            f"  \"issues\": [\"list of specific visual defects found\"],\n"
            f"  \"suggestion\": \"Specific actionable advice to fix the issues\"\n"
            f"}}\n"
            f"Do not include markdown formatting (```json) in the response, just the raw JSON string."
        )

        try:
            response_text = await invoke_gemini_model_async(
                prompt=system_prompt,
                model_name="gemini-2.0-flash-exp",
                images=[image_data],
                temperature=0.2 # Low temperature for analytical task
            )

            if response_text:
                # Clean up potential markdown formatting if Gemini adds it despite instructions
                cleaned_text = response_text.strip()
                if cleaned_text.startswith("```json"):
                    cleaned_text = cleaned_text[7:]
                if cleaned_text.endswith("```"):
                    cleaned_text = cleaned_text[:-3]

                return json.loads(cleaned_text.strip())
            else:
                return {"status": "ERROR", "issues": ["LLM analysis returned empty response"], "suggestion": None}

        except json.JSONDecodeError:
            logger.error(f"VisionService: Failed to parse JSON from LLM response: {response_text}")
            return {"status": "ERROR", "issues": ["Invalid JSON response from QA Agent"], "suggestion": str(response_text)}
        except Exception as e:
            logger.error(f"VisionService: Error analyzing visuals: {e}")
            return {"status": "ERROR", "issues": [str(e)], "suggestion": None}
