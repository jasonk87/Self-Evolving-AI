import asyncio
import sys
import types

from ai_assistant.custom_tools import search_tools


def test_google_custom_search_uses_visualization_when_auto_pip(monkeypatch):
    monkeypatch.setattr(search_tools.config, "GHOST_MODE", False)
    monkeypatch.setattr(search_tools.config, "AUTO_WEB_PIP", True)
    monkeypatch.setattr(search_tools, "GOOGLE_API_KEY", None)
    monkeypatch.setattr(search_tools, "GOOGLE_CSE_ID", None)

    class FakeVision:
        def __init__(self):
            self.calls = []

        async def capture_page_screenshot(self, url):
            self.calls.append(url)
            return "b64-image"

    fake = FakeVision()
    monkeypatch.setitem(sys.modules, "ai_assistant.core.vision_service", types.SimpleNamespace(VisionService=lambda: fake))

    result = asyncio.run(search_tools.google_custom_search("openai"))

    assert result["images"] == ["b64-image"]
    assert any("bing.com/search" in url for url in fake.calls)
