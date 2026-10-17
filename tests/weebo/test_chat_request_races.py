"""Browser regressions for delayed history loads and failed message sends."""

import asyncio
import base64
from contextlib import asynccontextmanager

import pytest
from aiohttp.test_utils import TestServer

from weebo.server.app import create_app

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def browser_page(app):
    playwright = pytest.importorskip("playwright.async_api")
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Install the browser with python -m playwright install chromium")
            raise
        async with browser:
            # These tests deliberately intercept/delay network requests. A service
            # worker can bypass page.route; keep real-worker coverage separate.
            page = await browser.new_page(viewport={"width": 390, "height": 844}, service_workers="block")
            await page.goto(str(server.make_url("/")))
            await page.wait_for_function("() => window.weebo?.chat?.convId && "
                                         "!weebo.chat.root.classList.contains('loading')")
            yield page


async def test_history_response_cannot_add_an_old_chats_messages_to_the_main_chat(app):
    old = app.conversations.create(title="Older conversation")
    for index in range(205):
        app.store.add_message(old["id"], "user", f"Old message {index}")
    main_id = app.conversations.main_id()
    requested, release = asyncio.Event(), asyncio.Event()

    async with browser_page(app) as page:
        await page.evaluate("id => weebo.openConversation(id)", old["id"])

        async def delay_history(route):
            requested.set()
            await release.wait()
            await route.continue_()

        await page.route(f"**/api/conversations/{old['id']}?before=*", delay_history)
        await page.evaluate("() => { window.historyLoad = weebo.chat.loadOlder(); }")
        await asyncio.wait_for(requested.wait(), 5)
        try:
            await page.evaluate("id => weebo.openConversation(id)", main_id)
        finally:
            release.set()
        await page.evaluate("() => window.historyLoad")
        assert await page.evaluate("id => weebo.chat.messages.every(m => m.conversation_id === id)", main_id)
        assert await page.get_by_text("Old message 0", exact=True).count() == 0


async def test_failed_send_restores_the_attachment_and_keeps_the_new_draft(app):
    requested, release = asyncio.Event(), asyncio.Event()
    async with browser_page(app) as page:
        async def fail_send(route):
            requested.set()
            await release.wait()
            await route.fulfill(status=503, json={"error": "Temporarily unavailable"})

        await page.route("**/api/conversations/*/messages", fail_send)
        await page.locator('input[type="file"]').set_input_files({
            "name": "sample.png", "mimeType": "image/png",
            "buffer": base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="),
        })
        await page.wait_for_function("() => weebo.chat.pendingImages.length === 1")
        await page.locator(".composer-input").fill("Please inspect this image")
        await page.locator(".send-btn").click()
        await asyncio.wait_for(requested.wait(), 5)
        await page.locator(".composer-input").fill("Also check the corner")
        release.set()
        await page.get_by_text("Message not sent", exact=True).wait_for()
        text = await page.locator(".composer-input").input_value()
        assert "Please inspect this image" in text and "Also check the corner" in text
        assert await page.evaluate("() => weebo.chat.pendingImages.length") == 1
        assert await page.locator(".attachment").count() == 1


@pytest.mark.parametrize("dismiss", ["escape", "close", "backdrop"])
async def test_dismissing_a_confirmation_resolves_it_as_cancelled(app, dismiss):
    async with browser_page(app) as page:
        await page.evaluate("""async () => {
            const { confirmDialog } = await import('/static/js/ui.js');
            window.confirmResult = 'pending';
            confirmDialog('Audit confirmation', 'This must not run.').then(result => window.confirmResult = result);
        }""")
        dialog = page.get_by_role("dialog", name="Audit confirmation")
        await dialog.wait_for()
        if dismiss == "escape":
            await page.keyboard.press("Escape")
        elif dismiss == "close":
            await dialog.get_by_role("button", name="Close", exact=True).click()
        else:
            await page.locator(".modal-backdrop").click(position={"x": 3, "y": 3})
        await dialog.wait_for(state="detached")
        assert await page.evaluate("window.confirmResult") is False


async def test_copy_works_when_the_modern_clipboard_api_is_unavailable(app):
    app.store.add_message(app.conversations.main_id(), "assistant", "```python\nprint('hello')\n```")
    async with browser_page(app) as page:
        await page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true});
            window.copiedText = null;
            document.execCommand = command => {
                if (command !== 'copy') return false;
                window.copiedText = document.activeElement.value;
                return true;
            };
        }""")
        await page.locator(".code-copy").click()
        assert await page.evaluate("window.copiedText") == "print('hello')"


async def test_copy_denial_is_reported_without_an_unhandled_rejection(app):
    app.store.add_message(app.conversations.main_id(), "assistant", "```text\nhello\n```")
    async with browser_page(app) as page:
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        await page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', {value: {
                writeText: async () => { throw new Error('Clipboard denied'); }
            }, configurable: true});
            document.execCommand = () => false;
        }""")
        await page.locator(".code-copy").click()
        await page.wait_for_timeout(100)
        assert errors == []
        assert await page.get_by_text("Couldn't copy", exact=True).count() == 1


async def test_chat_load_does_not_erase_messages_or_completion_arriving_after_its_snapshot(app, fake_engine):
    conv_id = app.conversations.main_id()
    await app.conversations.send(conv_id, "Testing a delayed snapshot")
    turn = fake_engine.turns[-1]
    requested, release = asyncio.Event(), asyncio.Event()
    async with browser_page(app) as page:
        await page.evaluate("""async () => {
            const { on } = await import('/static/js/api.js');
            on('conv.turn.completed', () => window.sawCompletion = true);
        }""")

        async def delayed_snapshot(route):
            response = await route.fetch()
            requested.set()
            await release.wait()
            await route.fulfill(response=response)

        await page.route(f"**/api/conversations/{conv_id}", delayed_snapshot)
        await page.evaluate("id => { window.chatLoad = weebo.chat.open(id); }", conv_id)
        await asyncio.wait_for(requested.wait(), 5)
        try:
            await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "The completed answer survives")
            await page.wait_for_function("() => window.sawCompletion")
        finally:
            release.set()
        await page.evaluate("() => window.chatLoad")
        assert await page.get_by_text("The completed answer survives", exact=True).count() == 1
        assert not await page.evaluate("weebo.chat.busy")


async def test_reconnect_recovers_messages_missed_while_disconnected(app):
    async with browser_page(app) as page:
        # Obtain the isolated server's hub through its event subscription, never the live app.
        hubs = [getattr(callback, "__self__", None) for _, callback in app.bus._subs.values()]
        hub = next(obj for obj in hubs if obj is not None and hasattr(obj, "close_all"))
        await hub.close_all()
        app.store.add_message(app.conversations.main_id(), "assistant", "Recovered after reconnect")
        await page.get_by_text("Recovered after reconnect", exact=True).wait_for(timeout=6000)


async def test_chat_snapshot_and_live_deltas_are_reconciled_exactly_once(app, fake_engine):
    conv_id = app.conversations.main_id()
    await app.conversations.send(conv_id, "Stream across a history request")
    turn = fake_engine.turns[-1]
    item = {"type": "agentMessage", "id": "stream-race", "text": "", "phase": "final_answer"}
    await fake_engine.emit(turn["thread_id"], "item/started", {"turnId": turn["turn_id"], "item": item})
    captured, release = asyncio.Event(), asyncio.Event()
    async with browser_page(app) as page:
        await page.evaluate("""async () => {
            const { on } = await import('/static/js/api.js');
            window.receivedDeltas = [];
            on('conv.delta', d => window.receivedDeltas.push(d.delta));
        }""")

        async def delta(text):
            await fake_engine.emit(turn["thread_id"], "item/agentMessage/delta", {
                "turnId": turn["turn_id"], "itemId": item["id"], "delta": text})

        async def delayed_snapshot(route):
            # A is both in the pending socket events and the returned snapshot.
            await delta("Alpha ")
            response = await route.fetch()
            captured.set()
            await release.wait()
            await route.fulfill(response=response)

        await page.route(f"**/api/conversations/{conv_id}", delayed_snapshot)
        await page.evaluate("id => { window.chatLoad = weebo.chat.open(id); }", conv_id)
        await asyncio.wait_for(captured.wait(), 5)
        try:
            await delta("Beta")  # B is newer than the snapshot.
            await page.wait_for_function("() => window.receivedDeltas.length === 2")
        finally:
            release.set()
        await page.evaluate("() => window.chatLoad")
        assert await page.evaluate("[...weebo.chat.streams.values()]") == ["Alpha Beta"]
        await page.get_by_text("Alpha Beta", exact=True).wait_for()


async def test_reconnecting_status_is_only_announced_while_offline(app):
    async with browser_page(app) as page:
        banner = page.get_by_role("status").filter(has_text="Reconnecting to Weebo")
        assert await banner.count() == 0
        await page.evaluate("() => weebo.setConnection('offline')")
        assert await banner.count() == 1
        await page.evaluate("() => weebo.setConnection('online')")
        assert await banner.count() == 0
