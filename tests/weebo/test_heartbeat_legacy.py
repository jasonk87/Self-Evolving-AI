import asyncio
import os
from datetime import datetime

import pytest

from weebo.proactive.heartbeat import in_quiet_hours
from tests.weebo.conftest import drain

pytestmark = pytest.mark.asyncio


async def test_quiet_hours_wrap_midnight():
    assert in_quiet_hours("23:00", "07:00", datetime(2026, 1, 1, 23, 30))
    assert in_quiet_hours("23:00", "07:00", datetime(2026, 1, 1, 6, 59))
    assert not in_quiet_hours("23:00", "07:00", datetime(2026, 1, 1, 12, 0))
    assert in_quiet_hours("13:00", "14:00", datetime(2026, 1, 1, 13, 30))
    assert not in_quiet_hours("09:00", "09:00", datetime(2026, 1, 1, 9, 0))


async def test_budget_gates_background_work(app, fake_engine):
    ok, _ = app.heartbeat.budget()
    assert ok
    fake_engine.rate_limits = {"primary": {"usedPercent": 95}}
    ok, reason = app.heartbeat.budget()
    assert not ok and "95%" in reason
    fake_engine.rate_limits = {"primary": {"usedPercent": 5}}
    app.settings.update({"autonomy.max_background_turns_per_day": 1})
    app.count_background_turn("x")
    assert not app.heartbeat.budget()[0]
    app.settings.update({"autonomy.proactive": False, "autonomy.max_background_turns_per_day": 10})
    assert app.heartbeat.budget() == (False, "Proactive mode is off.")


async def test_apply_dream_is_bounded_and_safe(app):
    keep, _ = app.memory.remember("User's name is Sam", "fact", 5, pinned=True)
    dup, _ = app.memory.remember("User drinks tea every morning", "preference", 2)
    app.settings.update({"evolution.mode": "propose"})
    result = {
        "new_memories": [{"text": f"User fact number {i} about hobbies", "kind": "fact", "importance": 9} for i in range(15)],
        "updates": [{"id": keep["id"], "text": "should not change pinned"}],
        "delete_ids": [keep["id"], dup["id"], "mem_missing"],
        "episode": "Sam and Weebo set up a reminder and planned the week together.",
        "insights": ["Offer a weekly planning routine on Sundays."],
        "message_to_user": "Want me to set up that Sunday planning routine?",
        "improvements": [{"title": "Add weekly planner", "description": "A routine that plans the week.", "rationale": "Asked twice."}],
    }
    applied = app.heartbeat._apply_dream(result)
    await drain(20)
    assert applied["added"] == 10 and applied["deleted"] == 1 and applied["proposals"] == 1
    assert app.store.get_memory(keep["id"])["text"] == "User's name is Sam"
    assert app.store.get_memory(dup["id"])["status"] == "archived"
    desk = app.conversations.desk()
    assert any("Sunday planning" in m["content"] for m in app.store.list_messages(desk["id"]))
    assert app.store.list_proposals()[0]["title"] == "Add weekly planner"


async def test_brief_only_inside_window(app, monkeypatch):
    app.settings.update({"autonomy.daily_brief_time": "08:30", "autonomy.quiet_hours_start": "23:00",
                         "autonomy.quiet_hours_end": "07:00"})

    class FakeNow(datetime):
        current = datetime(2026, 10, 3, 9, 0)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    import weebo.proactive.heartbeat as hb
    monkeypatch.setattr(hb, "datetime", FakeNow)
    assert app.heartbeat._brief_due()
    FakeNow.current = datetime(2026, 10, 3, 13, 0)
    assert not app.heartbeat._brief_due()
    FakeNow.current = datetime(2026, 10, 3, 8, 0)
    assert not app.heartbeat._brief_due()


# ---------------------------------------------------------------- legacy bridge
async def test_legacy_requirements_and_presentation(app, monkeypatch):
    from weebo.legacy.bridge import CATALOG, LegacyBridge, missing_requirements
    monkeypatch.delenv("OPENWEATHER_API_KEY", raising=False)
    assert missing_requirements("get_weather") == ["OPENWEATHER_API_KEY"]
    monkeypatch.setenv("OPENWEATHER_API_KEY", "x")
    assert missing_requirements("get_weather") == []
    text = LegacyBridge.describe_catalog()
    assert all(name in text for name in CATALOG)
    bridge = LegacyBridge(app)
    text, ok, extras = bridge._present("generate_bar_chart_html", {"success": True, "result": "```html\n<div>chart</div>\n```"})
    assert ok and extras["html"].strip() == "<div>chart</div>"
    text, ok, _ = bridge._present("google_search", {"success": False, "error": "quota"})
    assert not ok and "quota" in text


async def test_legacy_tool_refuses_unknown_and_unconfigured(app, monkeypatch):
    monkeypatch.delenv("OPENWEATHER_API_KEY", raising=False)
    app.legacy.status = "ready"
    app.legacy.tool_system = type("TS", (), {"_tool_registry": {"get_weather": {}}})()
    app.legacy._ready.set()
    text, ok, _ = await app.legacy.run("rm_rf", {})
    assert not ok and "not an available" in text
    text, ok, _ = await app.legacy.run("get_weather", {"location": "Paris"})
    assert not ok and "OPENWEATHER_API_KEY" in text


# ---------------------------------------------------------------- legacy LLM layer on Codex
async def test_codex_provider_routes_through_installed_backend(monkeypatch):
    from ai_assistant.core.llm import codex_provider
    calls = []

    async def backend(prompt, images=None, json_mode=False, task_name="unknown"):
        calls.append((prompt, json_mode, task_name))
        return "ok from codex"

    monkeypatch.setattr(codex_provider, "_backend", None)
    monkeypatch.setattr(codex_provider, "_backend_loop", None)
    codex_provider.install_backend(backend, asyncio.get_running_loop())
    try:
        assert codex_provider.codex_enabled()
        out = await codex_provider.CodexProvider().generate_response(
            "Summarize", system_instruction="Be brief", history=[{"role": "user", "content": "earlier"}])
        assert out == "ok from codex"
        prompt = calls[0][0]
        assert "Instructions:\nBe brief" in prompt and "USER: earlier" in prompt and "Request:\nSummarize" in prompt
        sync_out = await asyncio.to_thread(codex_provider.codex_generate_sync, "hello", json_mode=True)
        assert sync_out == "ok from codex" and calls[-1][1] is True
    finally:
        monkeypatch.setattr(codex_provider, "_backend", None)
        monkeypatch.setattr(codex_provider, "_backend_loop", None)
        os.environ.pop("WEEBO_LLM_BACKEND", None)


async def test_legacy_router_prefers_codex_when_enabled(monkeypatch):
    from ai_assistant.core.llm.router import ModelRouter
    monkeypatch.setenv("WEEBO_LLM_BACKEND", "codex")
    provider, model, mode, endpoint = ModelRouter().get_route("coding")
    assert provider.provider_name == "codex" and model == "codex"


# ---------------------------------------------------------------- Tailscale helper management
async def test_remote_status_without_helper(app):
    status = app.remote.status()
    assert status["enabled"] is False and status["state"] == "off" and status["origin"] is None
    app.settings.update({"server.tailnet": True})
    assert app.remote.status()["state"] == "stopped"


async def test_process_image_and_stale_status(app):
    import json as _json
    from weebo.remote import process_image
    assert process_image(os.getpid())  # this test process is alive
    assert process_image(999_999_999) is None
    app.remote.status_path.write_text(_json.dumps({"state": "ready", "origin": "https://x.ts.net", "pid": os.getpid()}))
    # A live pid that isn't the helper binary must not count as the helper.
    assert app.remote.running_pid() is None and app.remote.owners() == set()


async def test_adopt_identity_moves_existing_node(tmp_path):
    from weebo.remote import adopt_identity
    source = tmp_path / ".tailscale-blackbird"
    (source / "node").mkdir(parents=True)
    (source / "node" / "tailscaled.state").write_text("{}")
    target = tmp_path / "weebo-tailnet"
    target.mkdir()
    assert adopt_identity(source, target) is True
    assert (target / "node" / "tailscaled.state").exists()
    assert not source.exists() and (tmp_path / ".tailscale-blackbird.moved-to-weebo").exists()
    assert adopt_identity(tmp_path / ".tailscale-blackbird.moved-to-weebo", target) is False  # never overwrites
