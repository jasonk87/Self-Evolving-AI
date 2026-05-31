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


def test_process_search_results_accepts_structured_search_payload(monkeypatch):
    captured = {}

    def fake_invoke(prompt, **kwargs):
        captured["prompt"] = prompt
        return "structured payload accepted"

    monkeypatch.setattr(my_extra_tools, "invoke_gemini_model", fake_invoke)

    result = my_extra_tools.process_search_results(
        "three bedroom house for rent Smiths Grove KY",
        {"results": json.dumps([{"title": "Listing", "href": "https://example.com", "body": "$1,200/mo, 3 bds"}])},
    )

    assert result == "structured payload accepted"
    assert "$1,200/mo, 3 bds" in captured["prompt"]


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
    monkeypatch.setattr("ddgs.DDGS", FakeDDGS)
    monkeypatch.setattr(my_extra_tools, "_format_search_date", lambda value=None: "May 21, 2026")

    result = asyncio.run(my_extra_tools.search_duckduckgo(query="top news today"))

    assert calls[0][0] == "top news today May 21, 2026"
    assert calls[0][1]["timelimit"] == "d"
    assert result["query_used"] == "top news today May 21, 2026"
    assert json.loads(result["results"])[0]["title"] == "Today"


def test_search_web_uses_google_as_primary_provider(monkeypatch):
    calls = []

    async def fake_google(query, num_results=5):
        calls.append((query, num_results))
        return {"results": json.dumps([{"title": "Google", "href": "https://example.com", "body": "Result"}]), "images": []}

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("DuckDuckGo fallback should not run when Google returns results.")

    monkeypatch.setattr(my_extra_tools, "search_google_custom_search", fake_google)
    monkeypatch.setattr(my_extra_tools, "search_duckduckgo", fail_if_called)

    result = asyncio.run(my_extra_tools.search_web(query="fast lookup"))

    assert calls == [("fast lookup", 5)]
    assert result["provider"] == "google"
    assert json.loads(result["results"])[0]["title"] == "Google"


def test_search_web_falls_back_to_duckduckgo(monkeypatch):
    async def fake_google(query, num_results=5):
        return {"results": "[]", "images": []}

    async def fake_duckduckgo(*args, **kwargs):
        return {"results": json.dumps([{"title": "Fallback"}]), "images": [], "query_used": kwargs["query"]}

    monkeypatch.setattr(my_extra_tools, "search_google_custom_search", fake_google)
    monkeypatch.setattr(my_extra_tools, "search_duckduckgo", fake_duckduckgo)

    result = asyncio.run(my_extra_tools.web_search(query="fallback lookup"))

    assert result["provider"] == "duckduckgo"
    assert json.loads(result["results"])[0]["title"] == "Fallback"
