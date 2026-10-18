"""Config-file inheritance regressions found by the native PC acceptance check."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from weebo.codex.engine import CodexEngine
from weebo.connectors.check_live_isolation import callable_apps, inventory
from weebo.events import EventBus
from .test_connectors_growth import new_bot
from .conftest import drain

pytestmark = pytest.mark.asyncio


async def test_native_inventory_discovers_remote_plugins_without_exposing_credentials():
    engine = CodexEngine(EventBus())
    engine.status = "ready"
    engine.features.connector_controls = True
    engine.rpc = SimpleNamespace(running=True)
    engine.request = AsyncMock(side_effect=[
        {"config": {"mcp_servers": {"private.server": {"env": {"SECRET": "do-not-output"}}},
                    "plugins": {"calendar@old-marketplace": {"enabled": True}},
                    "apps": {"mail": {"tools": {"send": {"enabled": True}}}}}},
        {"marketplaces": [{"plugins": [
            {"id": "calendar@remote-marketplace", "installed": True},
            {"id": "not-installed@catalog", "installed": False}]}], "marketplaceLoadErrors": []},
        {"data": [{"name": "plugin-sidecar", "tools": {}}, {"name": "codex_apps", "tools": {
            "mail.read": {"_meta": {"connector_id": "mail", "private": "do-not-output"},
                          "annotations": {"readOnlyHint": True}},
            "mail.send": {"_meta": {"connector_id": "mail"}, "annotations": {"readOnlyHint": False}}}}]}])
    result = await engine.connector_inventory("C:/workspace with spaces")
    assert result["plugins"] == ["calendar@old-marketplace", "calendar@remote-marketplace"]
    assert result["mcp"] == ["plugin-sidecar", "private.server"]
    assert result["apps"] == {"mail": ["mail.read", "mail.send", "send"]}
    assert result["read_tools"] == {"mail": ["mail.read"]}
    assert result["runtime_only_mcp"] == ["plugin-sidecar"]
    assert "do-not-output" not in json.dumps(result)
    assert engine.request.await_args_list[1].args == (
        "plugin/list", {"cwds": ["C:/workspace with spaces"], "forceRefetch": False})


@pytest.mark.parametrize("catalog", [{}, {"marketplaces": [], "marketplaceLoadErrors": [{"message": "offline"}]}])
async def test_incomplete_remote_plugin_inventory_blocks_start(catalog):
    engine = CodexEngine(EventBus())
    engine.status = "ready"
    engine.features.connector_controls = True
    engine.rpc = SimpleNamespace(running=True)
    engine.request = AsyncMock(side_effect=[{"config": {}}, catalog])
    with pytest.raises(ValueError, match="inventory is incomplete"):
        await engine.connector_inventory("/workspace")


async def test_empty_scope_explicitly_denies_config_file_and_plugin_grants(app):
    result = app.connectors.thread_config({"data": {"connectors": []}}, {
        "mcp": ["a.server", "another"], "plugins": ["mail@remote"], "apps": {"mail": ["send"]}})["config"]
    assert result["mcp_servers"] == {"a.server": {"enabled": False}, "another": {"enabled": False}}
    assert result["plugins"] == {"mail@remote": {"enabled": False}}
    assert result["apps"]["mail"]["enabled"] is False
    assert result["features.apps"] is False


async def test_assigned_app_does_not_inherit_old_per_tool_grants(app, monkeypatch):
    row = {"kind": "app", "status": "approved", "data": {"app_id": "mail.app", "tools": [
        {"name": "mail.read", "risk": "read"}, {"name": "mail.send", "risk": "contact_people"}]}}
    monkeypatch.setattr(app.connectors, "get", lambda _: row)
    result = app.connectors.thread_config({"data": {"connectors": ["mail"]}}, {
        "apps": {"mail.app": ["mail.old_send", "mail.read"]}})["config"]
    tools = result["apps"]["mail.app"]["tools"]
    assert tools["mail.old_send"]["enabled"] is False
    assert tools["mail.send"]["enabled"] is False
    assert tools["mail.read"]["enabled"] is True
    assert result["features.apps"] is True


async def test_plugin_sidecars_are_disabled_with_a_valid_non_secret_transport(app):
    config = app.connectors.thread_config({"data": {"connectors": []}}, {
        "mcp": ["sidecar"], "runtime_only_mcp": ["sidecar"]})["config"]
    sidecar = config["mcp_servers"]["sidecar"]
    assert sidecar["enabled"] is False
    assert sidecar["command"] and "SystemExit" in sidecar["args"][-1]
    assert "env" not in sidecar and "url" not in sidecar


async def test_native_inventory_requires_complete_runtime_server_discovery():
    engine = CodexEngine(EventBus())
    engine.status = "ready"
    engine.features.connector_controls = True
    engine.rpc = SimpleNamespace(running=True)
    engine.request = AsyncMock(side_effect=[{"config": {}}, {"marketplaces": []}, {"data": None}])
    with pytest.raises(ValueError, match="native tool inventory is incomplete"):
        await engine.connector_inventory("/workspace")


async def test_live_check_uses_effective_callability_not_discovery_visibility():
    assert callable_apps({"apps": [{"id": "allowed", "enabled": True, "callable": True},
                                   {"id": "blocked", "enabled": True, "callable": False}]}) == {"allowed"}
    with pytest.raises(ValueError, match="snapshot unavailable"):
        callable_apps({"data": [{"id": "catalog-app", "isAccessible": True}]})


async def test_live_native_inventory_reads_all_pages():
    rpc = SimpleNamespace(request=AsyncMock(side_effect=[
        {"data": [{"name": "allowed"}], "nextCursor": "page-two"},
        {"data": [{"name": "unexpected"}], "nextCursor": None}]))
    assert [r["name"] for r in await inventory(rpc, "thread")] == ["allowed", "unexpected"]
    assert rpc.request.await_args_list[-1].args[1]["cursor"] == "page-two"


async def test_live_native_inventory_never_passes_truncated_pages():
    rpc = SimpleNamespace(request=AsyncMock(return_value={"data": [], "nextCursor": "stuck"}))
    with pytest.raises(ValueError, match="pagination incomplete"):
        await inventory(rpc, "thread")


async def test_new_inherited_grant_replaces_an_already_loaded_bot_thread(app, fake_engine):
    inherited = {"mcp": [], "apps": {}}
    fake_engine.connector_inventory = AsyncMock(side_effect=lambda cwd: inherited)
    bot = new_bot(app)
    await app.bots.message(bot["id"], "hello", actor="user", hops=0)
    first = fake_engine.turns[-1]
    await fake_engine.finish_turn(first["thread_id"], first["turn_id"])
    inherited["mcp"] = ["newly-installed"]
    await app.bots.message(bot["id"], "hello again", actor="user", hops=0)
    assert fake_engine.turns[-1]["thread_id"] != first["thread_id"]
    assert fake_engine.threads[-1]["config"]["mcp_servers"]["newly-installed"]["enabled"] is False


async def test_owned_agent_continuation_rechecks_changed_native_scope(app, fake_engine):
    inherited = {"mcp": [], "apps": {}}
    fake_engine.connector_inventory = AsyncMock(side_effect=lambda cwd: inherited)
    bot = new_bot(app)
    job = await app.agents.start("Read sample", "Read only", conversation_id=bot["conversation_id"])
    await drain(20)
    first = fake_engine.turns[-1]
    await fake_engine.finish_turn(first["thread_id"], first["turn_id"])
    await drain(20)
    inherited["mcp"] = ["newly-installed"]
    await app.agents.start("Continue", "Read again", conversation_id=bot["conversation_id"], continue_from=job["id"])
    await drain(20)
    assert fake_engine.turns[-1]["thread_id"] != first["thread_id"]
    assert fake_engine.threads[-1]["config"]["mcp_servers"]["newly-installed"]["enabled"] is False
