"""Browser regression for a final reply completing before its last stream paint."""

import pytest
from aiohttp.test_utils import TestServer

from weebo.server.app import create_app


@pytest.mark.asyncio
async def test_mobile_final_reply_survives_queued_stream_paint_and_reload(app, fake_engine, tmp_path):
    playwright = pytest.importorskip("playwright.async_api")
    conv = app.conversations.create(title="Comparison")
    conv_id = conv["id"]
    final_text = (
        "Here is the **full comparison**.\n\n"
        "| Capability | Companion | Weebo |\n"
        "| --- | --- | --- |\n"
        "| Cloud computer | Available | Local computer |\n"
        "| Follow-up | Ongoing | Task completion |\n\n"
        + "The assistant can report when a task finishes. " * 20
        + "\n\nEnd of the final reply."
    )
    errors = []
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Install the browser with python -m playwright install chromium")
            raise
        async with browser:
            page = await browser.new_page(
                viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.add_init_script(
                "localStorage.setItem('weebo.activeConversation', " + repr(conv_id) + ");"
            )
            await page.goto(str(server.make_url("/")))
            await page.wait_for_function(
                "id => window.weebo?.chat?.convId === id && !weebo.chat.root.classList.contains('loading')",
                arg=conv_id,
            )
            await page.wait_for_function("() => document.body.classList.contains('offline') === false")
            # Hold the browser's paint queue to reproduce mobile/background frame delays.
            await page.evaluate("""() => {
                window.pendingFrames = [];
                window.requestAnimationFrame = fn => pendingFrames.push(fn);
                window.flushFrames = () => pendingFrames.splice(0).forEach(fn => fn(performance.now()));
            }""")
            await page.locator(".composer-input").fill("Compare the companions")
            await page.locator(".send-btn").click()
            await page.wait_for_function("() => weebo.chat.busy", polling=20)
            turn = fake_engine.turns[0]
            tid, turn_id = turn["thread_id"], turn["turn_id"]

            async def emit(method, **params):
                await fake_engine.emit(tid, method, {"turnId": turn_id, **params})

            async def start_text(item_id, phase, text):
                item = {"type": "agentMessage", "id": item_id, "phase": phase, "text": ""}
                await emit("item/started", item=item)
                await emit("item/agentMessage/delta", itemId=item_id, delta=text)
                return item

            commentary = await start_text("commentary", "commentary", "Checking the details.")
            # Streaming must still paint normally before completion.
            await page.wait_for_function("() => weebo.chat._dirtyStreams.size > 0", polling=20)
            await page.evaluate("() => flushFrames()")
            assert await page.locator(".commentary .md").inner_text() == "Checking the details."
            await emit("item/completed", item={**commentary, "text": "Checking the details."})
            command = {"id": "tool", "type": "commandExecution", "command": "check details",
                       "status": "inProgress"}
            await emit("item/started", item=command)
            await page.locator(".composer-input").fill("Also compare follow-up")
            await page.locator(".send-btn").click()
            await page.locator(".msg-tag").filter(has_text="added while Weebo was working").wait_for()
            assert len(fake_engine.turns) == 1 and fake_engine.steers[0]["turn_id"] == turn_id
            await emit("item/completed", item={**command, "status": "completed", "exitCode": 0})

            final = await start_text("final", "final_answer", final_text)
            message_id = f"{conv_id}:final"
            await page.wait_for_function("id => weebo.chat._dirtyStreams.has(id)", arg=message_id, polling=20)
            await emit("item/completed", item={**final, "text": final_text})
            await emit("turn/completed", turn={"id": turn_id, "status": "completed"})
            await page.wait_for_function("id => weebo.chat.byId.get(id)?.status === 'done' && !weebo.chat.busy", arg=message_id, polling=20)
            reply = page.locator(f'[data-id="{message_id}"] .md')
            assert await reply.locator("table tbody tr").count() == 2
            # This queued callback used to replace the final message with empty HTML.
            await page.evaluate("() => flushFrames()")
            assert "End of the final reply." in await reply.inner_text()
            assert await reply.locator("table tbody tr").count() == 2
            await page.evaluate("() => { weebo.chat.render(); flushFrames(); }")
            assert "full comparison" in await reply.inner_text()
            table = reply.locator("table")
            await table.scroll_into_view_if_needed()
            assert await table.is_visible()
            assert await table.evaluate("""node => {
                const rect = node.getBoundingClientRect();
                const scroller = node.closest('.chat-scroll').getBoundingClientRect();
                return rect.bottom > scroller.top && rect.top < scroller.bottom && rect.width > 0;
            }""")
            await page.screenshot(path=str(tmp_path / "mobile-final-live.png"))
            assert not await page.locator(f'[data-id="{message_id}"]').evaluate(
                "node => node.closest('.work-group') !== null"
            )

            stored = app.store.get_message(message_id)
            assert stored["content"] == final_text
            assert stored["data"]["phase"] == "final_answer" and stored["status"] == "done"
            await page.reload()
            await page.locator(f'[data-id="{message_id}"] table').wait_for()
            assert "End of the final reply." in await reply.inner_text()
            assert await reply.locator("table tbody tr").count() == 2
            assert await page.evaluate("id => weebo.chat.byId.get(id).content", message_id) == final_text
            await reply.locator("table").scroll_into_view_if_needed()
            await page.screenshot(path=str(tmp_path / "mobile-final-reloaded.png"))
            assert errors == []
