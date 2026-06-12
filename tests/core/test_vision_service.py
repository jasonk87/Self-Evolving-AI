
import pytest
from unittest.mock import patch, AsyncMock
import sys

# We need to mock VisionService dependencies that might cause issues in test environment
# specifically playwright.
# BUT, we want to test the class itself.
# We can't mock the whole module.

# However, the previous error `TypeError: object MagicMock can't be used in 'await' expression`
# suggests that `ai_assistant.core.vision_service` was ALREADY mocked by my previous test setup
# where I did `sys.modules["ai_assistant.core.vision_service"] = MagicMock()` in `test_orchestrator_images.py`.
# Since pytest runs in the same process, `sys.modules` modifications persist!

# I need to reload the module or remove the mock from sys.modules for THIS test file.

if "ai_assistant.core.vision_service" in sys.modules:
    del sys.modules["ai_assistant.core.vision_service"]

from ai_assistant.core.vision_service import VisionService

@pytest.mark.asyncio
async def test_capture_page_screenshot_success():
    with patch("ai_assistant.core.vision_service.async_playwright") as mock_async_playwright:
        mock_playwright_obj = AsyncMock()
        mock_async_playwright.return_value.start.return_value = mock_playwright_obj

        mock_browser = AsyncMock()
        mock_playwright_obj.chromium.launch.return_value = mock_browser

        mock_page = AsyncMock()
        mock_browser.new_page.return_value = mock_page
        mock_page.screenshot.return_value = b"fake_screenshot_bytes"

        # Mock start() as an awaitable
        async def mock_start():
            return mock_playwright_obj
        mock_async_playwright.return_value.start = mock_start

        service = VisionService()
        result = await service.capture_page_screenshot("http://example.com")

        assert result is not None
        import base64
        expected = base64.b64encode(b"fake_screenshot_bytes").decode('utf-8')
        assert result == expected

@pytest.mark.asyncio
async def test_capture_page_screenshot_failure():
    with patch("ai_assistant.core.vision_service.async_playwright") as mock_async_playwright:
        async def mock_start_fail():
             raise Exception("Browser failed")
        mock_async_playwright.return_value.start = mock_start_fail

        service = VisionService()
        result = await service.capture_page_screenshot("http://example.com")

        assert result is None

@pytest.mark.asyncio
async def test_analyze_visuals_success():
    with patch("ai_assistant.core.vision_service.invoke_gemini_model_async", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = '{"status": "PASS", "issues": [], "suggestion": null}'

        service = VisionService()
        result = await service.analyze_visuals("base64data", "context")

        assert result["status"] == "PASS"
        assert result["issues"] == []

@pytest.mark.asyncio
async def test_analyze_visuals_json_cleanup():
    with patch("ai_assistant.core.vision_service.invoke_gemini_model_async", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = '```json\n{"status": "FAIL", "issues": ["overlap"], "suggestion": "fix"}\n```'

        service = VisionService()
        result = await service.analyze_visuals("base64data", "context")

        assert result["status"] == "FAIL"
        assert result["issues"] == ["overlap"]

@pytest.mark.asyncio
async def test_analyze_visuals_error():
    with patch("ai_assistant.core.vision_service.invoke_gemini_model_async", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.side_effect = Exception("API Error")

        service = VisionService()
        result = await service.analyze_visuals("base64data", "context")

        assert result["status"] == "ERROR"
        assert "API Error" in result["issues"][0]
