from types import SimpleNamespace

import pytest

from tests.weebo.conftest import drain
from weebo.brain.persona import developer_instructions


@pytest.fixture
def persona_app(tmp_path, monkeypatch):
    monkeypatch.setattr("weebo.brain.persona.paths.workspace_dir", lambda: tmp_path)
    return SimpleNamespace(settings={"user.name": "Test User"})


@pytest.mark.parametrize("scope", ["chat", "agent"])
def test_native_capability_guidance_routes_to_installed_skill(persona_app, scope):
    guidance = developer_instructions(persona_app, scope)
    assert "Test User" in guidance
    assert guidance.index("discover available tool metadata") < guidance.index("Read its entire `SKILL.md`")
    assert guidance.index("Read its entire `SKILL.md`") < guidance.index('await import("@oai/sky")')
    assert guidance.index('await import("@oai/sky")') < guidance.index("await sky.list_apps()")
    assert "installed skill and available tool metadata" in guidance
    assert "restrictions permit it" in guidance
    assert "through `node_repl`" in guidance
    # Formatting the persona must preserve the skill's JavaScript initializer.
    assert '''if (!globalThis.sky) {
  const { sky } = await import("@oai/sky");
  globalThis.sky = sky;
}''' in guidance
    assert "read-only capability probe" in guidance
    assert "Read any additional documentation the installed skill requires" in guidance


@pytest.mark.parametrize("scope", ["chat", "agent"])
def test_native_capability_guidance_limits_claims_and_fallbacks(persona_app, scope):
    guidance = developer_instructions(persona_app, scope)
    assert '"Native computer APIs are disabled" applies to that interface' in guidance
    assert "does not establish that the separate native plugin is unavailable" in guidance
    assert "never bypass a disabled interface or build a custom input/helper transport" in guidance
    assert "discovery (apps/windows listed), observation (window state/screenshots read), and input (clicks/typing) separately" in guidance
    assert "successful import alone does not verify app discovery" in guidance
    assert "verifies discovery only, not observation or input" in guidance
    assert "Preserve successful probes from the current session with their scope" in guidance
    assert "clicks and typing remain untested unless actually tested" in guidance
    assert "Do not click or type merely to test capability" in guidance
    assert "no native tool exists, skill reading is blocked, or initialization/probing fails" in guidance
    assert "report the exact observed limitation and which stage failed" in guidance
    assert "Leave machine configuration and user Codex settings unchanged" in guidance
    assert "Do not guess plugin versions, runtime paths, named pipes, or machine-specific app ids" in guidance
    assert "Learned computer-use procedures should reference the currently installed skill" in guidance


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["chat", "agent"])
async def test_native_guidance_reaches_codex_threads(app, fake_engine, scope):
    """Only the fake engine receives guidance; no desktop API is executed."""
    app.settings.update({"agents.auto_followup": False})
    task = None
    if scope == "chat":
        conv = app.conversations.create()
        await app.conversations.send(conv["id"], "Can you discover native Windows apps?")
    else:
        task = await app.agents.start("Discover apps", "Check native Windows app discovery")
        await drain(10)
    assert fake_engine.threads[0]["developerInstructions"] == developer_instructions(app, scope)
    assert "await sky.list_apps()" in fake_engine.threads[0]["developerInstructions"]
    turn = fake_engine.turns[0]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"])
    if task:
        await app.agents.wait(task["id"], timeout=2)


def test_background_persona_does_not_request_desktop_probes(persona_app):
    guidance = developer_instructions(persona_app, "background")
    assert "quiet background mind" in guidance
    assert "sky.list_apps" not in guidance
