"""Regression coverage for per-action consent, private connector env, publication and SMS receipts."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from weebo.connectors.launch import connector_environment
from weebo.integrations import http, sms

from .conftest import drain
from .test_connectors_growth import new_bot, ELICIT, onboard

pytestmark = pytest.mark.asyncio


def confirm(message="hello", recipient="+15551234567"):
    return {"title": "Use integration: send_text_message", "reason": json.dumps({"recipient": recipient, "message": message})}


async def test_outward_legacy_always_rule_never_authorizes_a_send(app):
    bot = new_bot(app, autonomy=["read", "act", "contact_people"])
    app.approvals.add_rule(bot["id"], "contact_people", "*", "user")
    info = app.approvals.describe(bot, "weebo/confirm", confirm())
    assert app.approvals.decide(bot, info) == ("ask", None)


async def test_integration_consent_hashes_full_arguments_not_truncated_preview(app, monkeypatch, twilio):
    approvals = []

    async def no(*args, **kwargs):
        approvals.append(kwargs)
        return False

    monkeypatch.setattr(app.interactions, "confirm", no)
    for suffix in ("first", "second"):
        _, ok, _ = await app.integrations.run("send_text_message", {"recipient": "+15551234567",
                                                                    "message": "a" * 5000 + suffix})
        assert not ok
    assert approvals[0]["action_key"] != approvals[1]["action_key"]


async def test_integration_retry_key_ignores_argument_order(app, monkeypatch, twilio):
    requests = []
    bot = new_bot(app)

    async def no(title, detail, **kwargs):
        info = app.approvals.describe(bot, "weebo/confirm", {"title": title, "reason": detail,
                                                           "action_key": kwargs["action_key"]})
        requests.append(info["request_key"])
        return False

    monkeypatch.setattr(app.interactions, "confirm", no)
    args = {"recipient": "+15551234567", "message": "the same approved action"}
    await app.integrations.run("send_text_message", args)
    await app.integrations.run("send_text_message", dict(reversed(list(args.items()))))
    assert requests[0] == requests[1]


async def test_once_consent_bound_to_arguments_and_consumed(app):
    bot = new_bot(app)
    params = confirm()
    await app.approvals.handle(bot, "weebo/confirm", params)
    item = app.approvals.open_items()[0]
    with pytest.raises(ValueError, match="Allow once"):
        await app.approvals.resolve(item["id"], "always")
    with pytest.raises(ValueError, match="Only the user"):
        await app.approvals.resolve(item["id"], "approve", actor="chief")
    await app.approvals.resolve(item["id"], "approve")
    for altered in (confirm("different body"), confirm(recipient="+15550009999")):
        assert (await app.approvals.handle(bot, "weebo/confirm", altered))["decision"] == "decline"
    assert len(app.approvals.open_items()) == 2  # different actions are not deduped together
    assert (await app.approvals.handle(bot, "weebo/confirm", {**params, "turnId": "retry"}))["decision"] == "accept"
    assert (await app.approvals.handle(bot, "weebo/confirm", params))["decision"] == "decline"


async def test_network_escape_cannot_inherit_act_or_old_permission_rule(app):
    bot = new_bot(app, autonomy=["read", "act"])
    app.settings.update({"autonomy.level": "full"})
    approval, mode, policy = app.bots.sandbox(bot, [])
    assert approval != "never" and mode != "danger-full-access" and policy["networkAccess"] is False
    app.approvals.add_rule(bot["id"], "permissions", "*", "user")
    info = app.approvals.describe(bot, "item/commandExecution/requestApproval", {
        "command": "python arbitrary.py", "cwd": "/tmp", "networkApprovalContext": {"host": "example.invalid"}})
    assert info["action"] == "permissions" and app.approvals.decide(bot, info) == ("ask", None)


async def test_connector_act_needs_per_action_approval_even_with_act_autonomy(app):
    row = await onboard(app)
    app.connectors.approve(row["id"], "user", {"place_call": "Businesses only"})
    bot = new_bot(app, connectors=[row["id"]], autonomy=["read", "act"])
    params = {"serverName": "Helpline", "message": "Do something", "mode": "form",
              "_meta": {"tool_name": "place_call"}}
    assert (await app.approvals.handle(bot, ELICIT, params))["action"] == "decline"
    assert app.approvals.open_items()[0]["data"]["per_action"]


async def test_connector_env_excludes_unlisted_keys_and_interpreter_injection(monkeypatch):
    monkeypatch.setenv("WEEBO_TEST_SECRET", "not-for-the-connector")
    monkeypatch.setenv("WEEBO_TEST_ALLOWED", "allowed")
    monkeypatch.setenv("PYTHONPATH", "dangerous-path")
    env = connector_environment(["WEEBO_TEST_ALLOWED"])
    assert env["WEEBO_TEST_ALLOWED"] == "allowed"
    assert "WEEBO_TEST_SECRET" not in env and "PYTHONPATH" not in env
    assert any(k.upper() == "PATH" for k in env)


@pytest.mark.parametrize("bad", ["TOKEN", ["*"], ["A=B"], [42], {"TOKEN": "value"}])
async def test_connector_manifest_rejects_invalid_env_allowlist(app, bad):
    with pytest.raises(ValueError, match="env_names"):
        app.connectors.register({"name": "Env test", "kind": "mcp", "command": [sys.executable], "env_names": bad}, "user")


async def test_runtime_connector_launcher_strips_env_from_actual_child(monkeypatch, tmp_path):
    from weebo.connectors import launch
    monkeypatch.setenv("WEEBO_TEST_SECRET", "not-for-child")
    monkeypatch.setenv("WEEBO_TEST_ALLOWED", "yes")
    script = "import os,json; print(json.dumps({k:os.environ.get(k) for k in ['WEEBO_TEST_ALLOWED','WEEBO_TEST_SECRET']}))"
    result = await asyncio.to_thread(subprocess.run,
        [sys.executable, "-I", launch.__file__, "--env-names", '["WEEBO_TEST_ALLOWED"]', "--cwd", str(tmp_path),
         "--", sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {"WEEBO_TEST_ALLOWED": "yes", "WEEBO_TEST_SECRET": None}


async def test_onboarding_child_receives_only_declared_env(app, monkeypatch):
    from weebo.connectors import registry
    seen = []
    original = registry.MCPClient

    def client(*args, **kwargs):
        seen.append(kwargs["env"])
        return original(*args, **kwargs)

    monkeypatch.setattr(registry, "MCPClient", client)
    monkeypatch.setenv("WEEBO_TEST_SECRET", "private")
    monkeypatch.setenv("WEEBO_TEST_ALLOWED", "public")
    from .test_connectors_growth import manifest
    row = app.connectors.register({**manifest(), "env_names": ["WEEBO_TEST_ALLOWED"]}, "user")
    assert (await app.connectors.test(row["id"]))["status"] == "tested"
    assert seen[0]["WEEBO_TEST_ALLOWED"] == "public" and "WEEBO_TEST_SECRET" not in seen[0]


async def test_native_inventory_is_read_only_allowlist_and_covers_resume(app, fake_engine):
    row = await onboard(app)
    app.connectors.approve(row["id"], "user", {"place_call": "Businesses only"})
    bot = new_bot(app, connectors=[row["id"]])

    async def inventory(cwd):
        return {"mcp": ["unrelated"], "apps": {"other_app": ["send_mail"]}, "plugins": ["mail@tools"]}

    fake_engine.connector_inventory = inventory
    await app.bots.message(bot["id"], "read", actor="user", hops=0)
    config = fake_engine.threads[-1]["config"]
    assert config["mcp_servers"]["unrelated"]["enabled"] is False
    assert config["apps"]["other_app"]["enabled"] is False
    assert config["plugins"]["mail@tools"]["enabled"] is False
    assert config["features.apps"] is False
    assert config["mcp_servers"]["helpline"]["enabled_tools"] == ["lookup"]
    assert config["apps"]["_default"]["enabled"] is False
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"])
    fake_engine.loaded_threads.clear()
    await app.bots.message(bot["id"], "read again", actor="user", hops=0)
    assert fake_engine.threads[-1]["resumed"]
    assert fake_engine.threads[-1]["config"] == config


async def test_unsupported_native_controls_block_bot_without_starting_a_turn(app, fake_engine):
    fake_engine.features.connector_controls = False
    bot = new_bot(app)
    await app.bots.message(bot["id"], "read", actor="user", hops=0)
    assert not fake_engine.threads and not fake_engine.turns
    assert any("isolation is unavailable" in m["content"] for m in app.store.list_messages(bot["conversation_id"]))


async def test_synthetic_probe_requires_thread_scoped_inventory_schema():
    from weebo.connectors.check_isolation import supported
    defs = {"ThreadStartParams": {"properties": {"config": {}, "ephemeral": {}}},
            "ListMcpServerStatusParams": {"properties": {"threadId": {}}},
            "McpServerToolCallParams": {"properties": {"threadId": {}, "server": {}, "tool": {}}}}
    assert supported(defs)
    del defs["ListMcpServerStatusParams"]["properties"]["threadId"]
    assert not supported(defs)


def add_fact(app):
    now = time.time()
    return app.facts.repository.upsert("events", {"external_id": "meeting", "title": "Design review",
        "start": now + 3600, "end": now + 7200, "tz": "UTC", "status": "confirmed"}, source="test:calendar", ttl_seconds=3600)[0]


@pytest.mark.parametrize("trigger", ["brief", "idea"])
async def test_checked_output_hidden_until_review_including_reconnect(app, fake_engine, monkeypatch, trigger):
    add_fact(app)
    gate, entered = asyncio.Event(), asyncio.Event()
    events = []
    app.bus.subscribe("conv.*", lambda topic, data: events.append((topic, data)))

    async def check(text, **kwargs):
        entered.set()
        await gate.wait()
        return [{"claim": "wrong room", "fact": "Room B"}]

    monkeypatch.setattr(app.factcheck, "second_pass", check)
    main = app.conversations.main_id()
    await app.conversations.run_event(main, "write", "", trigger=trigger)
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Design review is in Room A.")
    await asyncio.wait_for(entered.wait(), 2)
    assert not any("Room A" in m["content"] for m in app.store.list_messages(main))
    assert not any(topic in ("conv.delta", "conv.turn.completed") for topic, _ in events)
    gate.set()
    for _ in range(100):
        if app.conversations.session(main).turn is None:
            break
        await asyncio.sleep(.01)
    final = [m for m in app.store.list_messages(main) if m["role"] == "assistant" and m["kind"] == "text"][-1]
    assert final["content"].startswith("> ⚠️ Fact-check:") and "Room B" in final["content"]
    assert final["data"]["publication_check"] == "flagged"
    assert app.conversations.session(main).turn is None


async def test_stop_during_check_never_releases_late_draft(app, fake_engine, monkeypatch):
    add_fact(app)
    entered = asyncio.Event()

    async def check(*a, **kw):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(app.factcheck, "second_pass", check)
    main = app.conversations.main_id()
    await app.conversations.run_event(main, "draft", "", trigger="idea")
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Design review is in Room A.")
    await asyncio.wait_for(entered.wait(), 2)
    assert await app.conversations.interrupt(main)
    await drain(20)
    assert app.conversations.session(main).turn is None
    assert not any("Room A" in m["content"] for m in app.store.list_messages(main))
    assert not fake_engine.interrupts  # it already finished; Stop cancels the checker


async def test_failed_check_labels_output_unverified(app, fake_engine, monkeypatch):
    add_fact(app)

    async def check(*a, **kw):
        raise ValueError("unavailable")

    monkeypatch.setattr(app.factcheck, "second_pass", check)
    main = app.conversations.main_id()
    await app.conversations.run_event(main, "brief", "", trigger="brief")
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Design review is in Room A.")
    await drain(30)
    final = [m for m in app.store.list_messages(main) if m["role"] == "assistant" and m["kind"] == "text"][-1]
    assert final["data"]["publication_check"] == "unverified" and "unverified" in final["content"]


async def test_normal_chat_still_streams(app, fake_engine):
    events = []
    app.bus.subscribe("conv.delta", lambda _, data: events.append(data))
    main = app.conversations.main_id()
    await app.conversations.send(main, "Hello")
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Hello!")
    assert events and events[-1]["delta"] == "Hello!"


@pytest.fixture
def twilio(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-test-token")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15550000001")


@pytest.mark.parametrize("status,outcome", [("delivered", "delivered"), ("undelivered", "failed"), ("failed", "failed")])
async def test_sms_followup_polls_original_message_not_resending(app, monkeypatch, twilio, status, outcome):
    posted, fetched = [], []

    async def post(url, data, **kwargs):
        posted.append(data)
        return {"sid": "SMtest", "status": "queued"}

    async def get(url, **kwargs):
        fetched.append((url, kwargs))
        return {"sid": "SMtest", "status": status}

    monkeypatch.setattr(http, "post_form", post)
    monkeypatch.setattr(http, "get_json", get)
    result = await sms.send_text_message(app, "+15551234567", "hello")
    assert "not confirmed" in result.text
    row = app.store.kv_get(sms.PENDING_KEY)["SMtest"]
    await sms.check_deliveries(app, row["next_check"] - 1)
    assert not fetched
    await sms.check_deliveries(app, row["next_check"])
    assert len(posted) == len(fetched) == 1 and fetched[0][0].endswith("/Messages/SMtest.json")
    assert fetched[0][1]["auth"] == ("ACtest", "fake-test-token")
    assert not app.store.kv_get(sms.PENDING_KEY)
    assert app.store.get_message("sms:SMtest")["data"]["delivery"] == outcome
    await sms.check_deliveries(app, row["next_check"] + 500)
    assert len(fetched) == 1


async def test_sms_pending_or_failed_poll_is_bounded_and_durable(app, monkeypatch, twilio):
    app.store.kv_set(sms.PENDING_KEY, {"SMstored": {"recipient": "+15551234567", "account": "ACtest",
                                                 "attempts": 0, "next_check": 0}})

    async def get(*args, **kwargs):
        raise http.HttpError("secret-response-do-not-log")

    monkeypatch.setattr(http, "get_json", get)
    for i in range(sms.MAX_CHECKS):
        await sms.check_deliveries(app, i * sms.CHECK_DELAY)
    final = app.store.get_message("sms:SMstored")
    assert final["data"]["delivery"] == "unverified"
    assert "secret-response" not in final["content"] and not app.store.kv_get(sms.PENDING_KEY)
