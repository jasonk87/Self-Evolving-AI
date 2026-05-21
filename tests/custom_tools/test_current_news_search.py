import asyncio
import json
from datetime import date

from ai_assistant import config
from ai_assistant.custom_tools import my_extra_tools


def test_current_news_query_is_date_enriched():
    enriched = my_extra_tools._enrich_current_news_query(
        "top news today",
        today=date(2026, 5, 21),
    )

    assert enriched == "top news today May 21, 2026"


def test_current_news_query_with_existing_date_is_not_duplicated():
    query = "top news today May 21, 2026"

    assert my_extra_tools._enrich_current_news_query(query, today=date(2026, 5, 21)) == query


def test_process_search_results_adds_freshness_rules(monkeypatch):
    captured = {}

    def fake_invoke(prompt, **kwargs):
        captured["prompt"] = prompt
        return "freshness guarded"

    monkeypatch.setattr(my_extra_tools, "_format_search_date", lambda value=None: "May 21, 2026")
    monkeypatch.setattr(my_extra_tools, "invoke_gemini_model", fake_invoke)

    result = my_extra_tools.process_search_results(
        "what is on the news today?",
        json.dumps([{"title": "Headline", "href": "https://example.com", "body": "May 21, 2026 update"}]),
    )

    assert result == "freshness guarded"
    assert "Today's date is May 21, 2026" in captured["prompt"]
    assert "Do not make up information" in captured["prompt"]
    assert "do not summarize it as today's news" in captured["prompt"]


def test_search_duckduckgo_uses_date_enriched_day_limited_query(monkeypatch):
    calls = []

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def text(self, query, **kwargs):
            calls.append((query, kwargs))
            return [{"title": "Today", "href": "https://example.com", "body": "May 21, 2026 headline"}]

    monkeypatch.setattr(config, "AUTO_WEB_PIP", False, raising=False)
    monkeypatch.setattr(config, "GHOST_MODE", False, raising=False)
    monkeypatch.setattr("duckduckgo_search.DDGS", FakeDDGS)
    monkeypatch.setattr(my_extra_tools, "_format_search_date", lambda value=None: "May 21, 2026")

    result = asyncio.run(my_extra_tools.search_duckduckgo(query="top news today"))

    assert calls[0][0] == "top news today May 21, 2026"
    assert calls[0][1]["timelimit"] == "d"
    assert result["query_used"] == "top news today May 21, 2026"
    assert json.loads(result["results"])[0]["title"] == "Today"
