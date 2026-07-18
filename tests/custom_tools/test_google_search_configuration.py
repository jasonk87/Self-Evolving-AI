from unittest.mock import MagicMock, patch

import pytest

from ai_assistant.custom_tools import search_tools


@pytest.mark.asyncio
async def test_google_search_disables_obsolete_discovery_cache(monkeypatch):
    monkeypatch.setattr(search_tools, "GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(search_tools, "GOOGLE_CSE_ID", "test-cse")

    service = MagicMock()
    service.__enter__.return_value = service
    service.cse.return_value.list.return_value.execute.return_value = {"items": []}

    with patch.object(search_tools, "build", return_value=service) as build:
        result = await search_tools.google_custom_search("weather Glasgow Kentucky")

    assert result["results"] == []
    build.assert_called_once_with(
        "customsearch",
        "v1",
        developerKey="test-key",
        cache_discovery=False,
    )
