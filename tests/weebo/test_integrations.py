"""Integrations (weather, search, research, calendar, SMS, widgets): offline, with HTTP stubbed."""

from datetime import date

import pytest

from weebo.brain.tools import ToolContext, registry
from weebo.integrations import CATALOG, Integrations, http, missing_requirements, search, widgets

pytestmark = pytest.mark.asyncio

DDG_HTML = """
<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Alpha</a>
<a class="result__snippet">First snippet</a></div>
<div class="result"><a class="result__a" href="https://example.org/b">Beta</a></div>
"""


async def test_catalog_describes_every_integration_and_its_requirements(monkeypatch):
    monkeypatch.delenv("OPENWEATHER_API_KEY", raising=False)
    assert missing_requirements("get_weather") == ["OPENWEATHER_API_KEY"]
    monkeypatch.setenv("OPENWEATHER_API_KEY", "x")
    assert missing_requirements("get_weather") == []
    assert missing_requirements("google_search") == []  # falls back to DuckDuckGo without keys
    text = Integrations.describe_catalog()
    assert all(name in text for name in CATALOG)
    assert "send_text_message" in text and "Asks the user first" in text


async def test_run_validates_name_arguments_and_setup(app, monkeypatch):
    monkeypatch.delenv("OPENWEATHER_API_KEY", raising=False)
    text, ok, _ = await app.integrations.run("rm_rf", {})
    assert not ok and "not an integration" in text
    text, ok, _ = await app.integrations.run("get_weather", {"location": "Paris", "units": "metric"})
    assert not ok and "doesn't take: units" in text
    text, ok, _ = await app.integrations.run("get_weather", {})
    assert not ok and "needs: location" in text
    text, ok, _ = await app.integrations.run("get_weather", {"location": "Paris"})
    assert not ok and "OPENWEATHER_API_KEY" in text


async def test_weather_formats_the_answer(app, monkeypatch):
    monkeypatch.setenv("OPENWEATHER_API_KEY", "key")
    seen = {}

    async def fake_get_json(url, params=None, timeout=15.0):
        seen.update(params)
        return {"cod": 200, "name": "Paris", "sys": {"country": "FR"}, "weather": [{"description": "light rain"}],
                "main": {"temp": 50.0, "feels_like": 47.0, "humidity": 80}, "wind": {"speed": 6}}

    monkeypatch.setattr(http, "get_json", fake_get_json)
    text, ok, _ = await app.integrations.run("get_weather", {"location": "Paris"})
    assert ok and "Paris, FR: light rain, 50°F (10°C)" in text and seen["appid"] == "key"


async def test_outward_actions_need_the_users_ok(app, monkeypatch):
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"):
        monkeypatch.setenv(var, "v")
    sent = []

    async def fake_post(url, data, auth=None, timeout=20.0):
        sent.append(data)
        return {"sid": "SM1"}

    monkeypatch.setattr(http, "post_form", fake_post)
    answers = [False, True]

    async def confirm(title, detail, conversation_id=None, task_id=None, **_):
        return answers.pop(0)

    monkeypatch.setattr(app.interactions, "confirm", confirm)
    args = {"recipient": "+1 (555) 123-4567", "message": "On my way"}
    text, ok, _ = await app.integrations.run("send_text_message", args)
    assert not ok and "declined" in text and not sent
    text, ok, _ = await app.integrations.run("send_text_message", args)
    assert ok and "SM1" in text and sent[0]["To"] == "+15551234567"


async def test_news_queries_are_anchored_to_today():
    assert search.anchor_to_today("latest AI news", date(2026, 10, 7)) == "latest AI news October 7, 2026"
    assert search.anchor_to_today("news from March 2024") == "news from March 2024"
    assert search.anchor_to_today("python dataclasses") == "python dataclasses"


async def test_search_falls_back_to_duckduckgo(app, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    async def fake_get_text(url, params=None, timeout=15.0):
        assert "duckduckgo" in url
        return DDG_HTML

    monkeypatch.setattr(http, "get_text", fake_get_text)
    text, ok, _ = await app.integrations.run("google_search", {"query": "alpha"})
    assert ok and "via duckduckgo" in text and "https://example.com/a" in text and "First snippet" in text


async def test_google_errors_fall_back_too(app, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_CSE_ID", "c")

    async def broken(url, params=None, timeout=15.0):
        raise http.HttpError("quota exceeded")

    async def fake_get_text(url, params=None, timeout=15.0):
        return DDG_HTML

    monkeypatch.setattr(http, "get_json", broken)
    monkeypatch.setattr(http, "get_text", fake_get_text)
    results, provider = await search.search("alpha", 5)
    assert provider == "duckduckgo" and [r["title"] for r in results] == ["Alpha", "Beta"]


async def test_deep_research_reads_pages_and_cites_them(app, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    page = "<html><script>evil()</script><nav>menu</nav><p>" + "Useful fact about alpha. " * 20 + "</p></html>"

    async def fake_get_text(url, params=None, timeout=15.0):
        return DDG_HTML if "duckduckgo" in url else page

    prompts = []

    async def fake_think(prompt, schema=None, **kwargs):
        prompts.append((prompt, kwargs))
        return "Alpha is useful [1]."

    monkeypatch.setattr(http, "get_text", fake_get_text)
    monkeypatch.setattr(app.mind, "think", fake_think)
    text, ok, _ = await app.integrations.run("execute_deep_research", {"query": "what is alpha"})
    assert ok and text.startswith("Alpha is useful [1].") and "[1] https://example.com/a" in text
    prompt, kwargs = prompts[0]
    assert "Useful fact about alpha" in prompt and "evil()" not in prompt and "menu" not in prompt
    assert kwargs["count"] is False  # on behalf of a chat turn, not background budget


async def test_widgets_escape_content_and_validate_input():
    chart = widgets.bar_chart_html(["<b>A</b>", "B"], [3, 6], title="<script>x</script>")
    assert "<b>A</b>" not in chart and "&lt;b&gt;A&lt;/b&gt;" in chart and "<script>" not in chart
    assert "width:100.0%" in chart and "width:50.0%" in chart
    with pytest.raises(widgets.IntegrationError):
        widgets.bar_chart_html(["A"], [-1])
    with pytest.raises(widgets.IntegrationError):
        widgets.bar_chart_html(["A"], [1], colors=["red;background:url(x)"])
    table = widgets.table_html([["Name", "Note"], ["Ann", "<img src=x onerror=alert(1)>"]])
    assert "<th>Name</th>" in table and "<img" not in table


async def test_use_integration_tool_posts_widgets_to_the_chat(app):
    conv = app.conversations.create("Charts")
    ctx = ToolContext(app=app, thread_id="t", conversation_id=conv["id"])
    result = await registry.call(ctx, "use_integration", {
        "name": "generate_bar_chart_html", "arguments": {"labels": ["a", "b"], "values": [1, 2], "title": "T"}})
    assert result.success and "Rendered" in result.text
    widget = [m for m in app.store.list_messages(conv["id"]) if m["kind"] == "widget"]
    assert widget and "bar" in widget[0]["data"]["html"] and widget[0]["data"]["tool"] == "generate_bar_chart_html"
