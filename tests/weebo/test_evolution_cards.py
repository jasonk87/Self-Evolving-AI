"""Originating-chat evolution cards survive decisions, missed events and restarts."""

import json

import pytest
from aiohttp.test_utils import TestServer

from weebo.server.app import create_app
from weebo.store import Store


@pytest.mark.asyncio
async def test_proposal_tool_returns_supported_link_and_native_acknowledgement(app):
    from weebo.brain.builtin_tools import propose_improvement
    from weebo.brain.tools import ToolContext

    conv = app.conversations.create()
    app.settings.update({"evolution.mode": "propose"})
    result = await propose_improvement(ToolContext(app, "thread", conversation_id=conv["id"]),
                                       "Requested upgrade", "Please build this", requested_by_user=True)
    p = app.store.list_proposals()[0]
    assert f"[Open in Evolution](/#evolution/{p['id']})" in result.text
    assert "Describe exactly this state" not in result.text
    assert app.store.list_messages(conv["id"])[0]["data"]["proposal_id"] == p["id"]


@pytest.mark.asyncio
async def test_submission_and_council_verdict_update_one_persisted_card(app, monkeypatch):
    conv = app.conversations.create(title="Origin")
    app.settings.update({"evolution.mode": "propose"})
    monkeypatch.setattr(app.evolution, "_start_vetting", lambda _: None)
    monkeypatch.setattr(app.heartbeat, "budget", lambda: (True, ""))
    messages = []
    app.bus.subscribe("conv.message", lambda _, d: messages.append(d["message"]))
    verdict = {"approved": True, "reason": "Fixes a recurring error."}

    async def convene(*_):
        return verdict

    monkeypatch.setattr("weebo.evolution.council.convene", convene)
    for approved in (True, False, None):
        verdict = {"approved": approved, "reason": f"Recorded verdict: {approved}"}
        p = await app.evolution.propose(f"Proposal {approved}", "A change", source="dream", conversation_id=conv["id"])
        message_id = f"evolution:{p['id']}"
        initial = app.store.get_message(message_id)
        assert initial["content"] == "Sent to Council"
        assert initial["data"]["title"] == p["title"] and messages[-1]["id"] == message_id
        await app.evolution._vet(p["id"])
        card = app.store.get_message(message_id)
        assert card["seq"] == initial["seq"] and card["created_at"] == initial["created_at"]
        assert card["data"]["reasons"] == [verdict["reason"]]
        if approved:
            assert "approved building" in card["data"]["decision"]
            assert "not Jason's approval to merge" in card["data"]["decision"]
        elif approved is False:
            assert card["content"] == "Council declined to build"
        else:
            assert "Awaiting your decision" in card["data"]["decision"]
        app.evolution._publish(p["id"])
        app.evolution._publish(p["id"])
        assert app.store.get_message(message_id) == card
        assert len([m for m in app.store.list_messages(conv["id"]) if m["id"] == message_id]) == 1
    assert not app.engine.turns


@pytest.mark.asyncio
async def test_user_bypass_lifecycle_and_restart_reconciliation(app, monkeypatch):
    conv = app.conversations.create(title="Origin")
    other = app.conversations.create(title="Other")
    app.settings.update({"evolution.mode": "build"})
    monkeypatch.setattr(app.evolution, "_start_vetting", lambda _: pytest.fail("User request went to Council"))
    p = await app.evolution.propose("User change", "Build this", conversation_id=conv["id"])
    message_id = f"evolution:{p['id']}"
    initial = app.store.get_message(message_id)
    assert initial["data"]["status"] == "queued"
    assert "User-requested change; skips Council" in initial["data"]["decision"]
    assert app.store.list_proposal_messages(other["id"]) == []
    for status, stage, text in (("building", "building", "Building"), ("checking", "testing", "verification tests"),
                                ("checking", "reviewing", "Reviewing the code"), ("ready", None, "Jason's approval to merge")):
        app.store.update_proposal(p["id"], status=status, meta={**p["meta"], "stage": stage})
        app.evolution._publish(p["id"])
        assert text in app.store.get_message(message_id)["content"]
    app.evolution._fail(p["id"], "The build agent stopped.")
    assert app.store.get_message(message_id)["data"]["reasons"] == ["The build agent stopped."]
    app.store.update_proposal(p["id"], status="failed", gate_report=json.dumps({"results": [
        {"name": "Tests", "ok": False, "output": "test_example failed"}]}),
        review_report="Blocking review finding", meta={**p["meta"], "review_blocking": True})
    assert app.store.get_message(message_id)["data"]["reasons"] == ["Tests: test_example failed", "Blocking review finding"]
    for status, meta, text in (("rejected", {"reason": "Too risky"}, "Rejected"),
                               ("merged", {"automatic": False}, "Jason approved the merge"),
                               ("merged", {"automatic": True, "verified_at": 1}, "startup verified")):
        app.store.update_proposal(p["id"], status=status, meta={**p["meta"], **meta})
        assert text in app.store.get_message(message_id)["content"]
        if status == "rejected":
            assert "Too risky" in app.store.get_message(message_id)["data"]["reasons"]
    # Simulate an older/missed writer, then reopen the SQLite database as on restart.
    app.store.execute("UPDATE proposals SET status='ready', meta=? WHERE id=?", (json.dumps(p["meta"]), p["id"]))
    reopened = Store(app.store.path)
    try:
        assert reopened.get_proposal(p["id"])["meta"]["conversation_id"] == conv["id"]
        card = reopened.list_proposal_messages(conv["id"])[0]
        assert card["seq"] == initial["seq"] and "awaiting Jason" in card["content"]
        assert reopened.list_messages(conv["id"])[0] == card
    finally:
        reopened.close()
    app.store.delete_conversation(conv["id"])
    app.evolution._publish(p["id"])
    assert app.store.get_message(message_id) is None


@pytest.mark.asyncio
async def test_restart_interruption_updates_existing_card(app, monkeypatch):
    conv = app.conversations.create()
    p = app.store.add_proposal("Interrupted", "Build", meta={"conversation_id": conv["id"]})
    app.store.update_proposal(p["id"], status="building")
    monkeypatch.setattr(app.evolution, "_empty_trash", lambda: None)
    monkeypatch.setattr(app.evolution, "_verify_after_upgrade", lambda: None)
    app.evolution._recover()
    card = app.store.list_proposal_messages(conv["id"])[0]
    assert card["data"]["status"] == "failed"
    assert "Interrupted by a restart" in card["data"]["reasons"][0]


@pytest.mark.asyncio
async def test_live_card_reload_reconnect_and_deep_link_navigation(app, monkeypatch):
    playwright = pytest.importorskip("playwright.async_api")
    origin = app.conversations.create(title="Origin")
    other = app.conversations.create(title="Other")
    app.settings.update({"evolution.mode": "propose"})
    monkeypatch.setattr(app.evolution, "_start_vetting", lambda _: None)
    monkeypatch.setattr(app.heartbeat, "budget", lambda: (True, ""))
    verdict = {"approved": True, "reason": "Useful and bounded."}

    async def convene(*_):
        return verdict

    monkeypatch.setattr("weebo.evolution.council.convene", convene)
    errors = []
    web_app = create_app(app)
    async with TestServer(web_app) as server, playwright.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Install the browser with python -m playwright install chromium")
            raise
        async with browser:
            page = await browser.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            # Keep a reference so the test can disconnect the actual event socket.
            await page.add_init_script("""{
                const NativeSocket = window.WebSocket;
                window.WebSocket = class extends NativeSocket {
                    constructor(...args) { super(...args); window.eventSocket = this; }
                };
            }""")
            await page.add_init_script("localStorage.setItem('weebo.activeConversation', " + repr(origin["id"]) + ");")
            await page.goto(str(server.make_url("/")))
            await page.wait_for_function("id => window.weebo?.chat?.convId === id && !weebo.chat.root.classList.contains('loading') && !document.body.classList.contains('offline')", arg=origin["id"])
            p = await app.evolution.propose("Chat upgrade", "A bounded change", source="dream", conversation_id=origin["id"])
            card = page.locator(f'[data-id="evolution:{p["id"]}"]')
            await card.get_by_text("Sent to Council", exact=True).wait_for()
            submitted = app.store.get_message(f"evolution:{p['id']}")
            await app.evolution._vet(p["id"])
            await card.get_by_text(verdict["reason"], exact=True).wait_for()
            assert "not Jason's approval to merge" in await card.inner_text()
            for _ in range(3):
                app.evolution._publish(p["id"])
            assert await card.count() == 1
            link = card.get_by_role("link", name="Open in Evolution")
            href = await link.get_attribute("href")
            assert href == f"/#evolution/{p['id']}"
            await link.click()
            await page.locator("#drawer h3").get_by_text(p["title"], exact=True).wait_for()
            assert await page.evaluate("location.hash") == f"#evolution/{p['id']}"
            await page.reload()
            await page.locator("#drawer h3").get_by_text(p["title"], exact=True).wait_for()
            await card.get_by_text(verdict["reason"], exact=True).wait_for()
            await page.locator("#drawer-close").click()
            assert await page.evaluate("location.hash") == ""
            # Update a conversation while a different chat is open.
            await page.evaluate("id => weebo.openConversation(id)", other["id"])
            verdict = {"approved": False, "reason": "No evidence this is needed."}
            declined = await app.evolution.propose("Declined upgrade", "Speculative", source="dream", conversation_id=origin["id"])
            await app.evolution._vet(declined["id"])
            assert await page.locator(".evolution-status-card").count() == 0
            await page.evaluate("id => weebo.openConversation(id)", origin["id"])
            denied_card = page.locator(f'[data-id="evolution:{declined["id"]}"]')
            assert "Council declined to build" in await denied_card.inner_text()
            assert verdict["reason"] in await denied_card.inner_text()
            user = await app.evolution.propose("Requested upgrade", "User asked", conversation_id=origin["id"])
            await page.get_by_text("User-requested change; skips Council. Merge approval is separate.", exact=True).wait_for()
            # A state change with no event must be reconciled by the real reconnect.
            await page.evaluate("eventSocket.close()")
            await page.wait_for_function("() => document.body.classList.contains('offline')")
            app.store.update_proposal(p["id"], status="ready")
            await card.get_by_text("Ready for review; awaiting Jason's approval to merge", exact=True).wait_for()
            assert await card.count() == 1
            app.bus.publish("conv.message", {"conversation_id": origin["id"], "message": submitted})
            # Wait for the duplicate to reach the browser before asserting no regression.
            app.bus.publish("conv.status", {"conversation_id": origin["id"], "text": "Duplicate delivered"})
            await page.get_by_text("Duplicate delivered", exact=True).wait_for()
            assert "Ready for review" in await card.inner_text()
            # A normal chat markdown link follows the same safe route.
            app.store.add_message(origin["id"], "assistant", f"[Review this upgrade](/#evolution/{declined['id']})")
            await page.reload()
            await page.get_by_role("link", name="Review this upgrade").click()
            await page.locator("#drawer h3").get_by_text(declined["title"], exact=True).wait_for()
            await page.evaluate("id => { location.hash = '#evolution/' + id; }", user["id"])
            await page.locator("#drawer h3").get_by_text(user["title"], exact=True).wait_for()
            await page.go_back()
            await page.locator("#drawer h3").get_by_text(declined["title"], exact=True).wait_for()
            # Invalid and missing routes never invoke actions or navigate elsewhere.
            await page.evaluate("location.hash = '#evolution/%2e%2e%2fsettings'")
            await page.wait_for_function("() => !weebo.panels.current")
            await page.evaluate("location.hash = '#evolution/p_missing'")
            await page.get_by_text("Couldn't load: Request failed (404)", exact=True).wait_for()
            assert not app.engine.turns
            assert errors == []
