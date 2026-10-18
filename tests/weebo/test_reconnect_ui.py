"""Real-browser regressions for reconnecting without losing the reader's place."""
from contextlib import asynccontextmanager
import re

import pytest
from aiohttp.test_utils import TestServer

from weebo.server.app import create_app

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def chat_page(app):
    playwright = pytest.importorskip("playwright.async_api")
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Install Chromium with python -m playwright install chromium")
            raise
        async with browser:
            page = await browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
            await page.goto(str(server.make_url("/")))
            await page.wait_for_function("() => window.weebo?.chat?.messages.length && !weebo.chat.loadingEvents")
            await page.add_style_tag(content="*, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }")
            await page.evaluate("() => new Promise(requestAnimationFrame)")
            yield page


async def refresh(page):
    await page.evaluate("() => weebo.chat.open(weebo.chat.convId, {refresh: true, focus: false})")
    await page.evaluate("() => new Promise(requestAnimationFrame)")


async def test_failed_refresh_keeps_chat_and_expanded_items(app):
    main = app.conversations.main_id()
    app.store.add_message(main, "assistant", "An answer worth keeping.")
    app.store.add_message(main, "tool", "python -m pytest", kind="command", data={"output": "Original output"})
    async with chat_page(app) as page:
        await page.locator('.group-head').click()
        await page.locator('.work-row').click()
        before = await page.locator('.chat-list').inner_html()
        await page.route("**/api/conversations/" + main, lambda route: route.fulfill(status=503, body="Unavailable"))
        await refresh(page)
        assert await page.locator('.chat-list').inner_html() == before
        assert await page.locator('.work-item.open').count() == 1


async def test_live_reasoning_heading_never_shows_a_shell_command(app):
    async with chat_page(app) as page:
        values = await page.evaluate("""async () => {
          const {reasoningHeading} = await import('/static/js/chat.js');
          return ['git status', 'Running npm test', 'Get-Date', 'echo hello; exit', 'Checking the result']
            .map(text => reasoningHeading(`**${text}**`));
        }""")
        assert values == ["", "", "", "", "Checking the result"]


async def test_refresh_revalidates_old_cards_and_deduplicates_moved_cards(app):
    main = app.conversations.main_id()
    card = app.store.add_message(main, "event", "Waiting", kind="agent_card", message_id="old-card",
                                 data={"task_id": "t_old", "title": "Old task", "status": "running"})
    moved = app.store.add_message(main, "event", "Waiting", kind="agent_card", message_id="moved-card",
                                  data={"task_id": "t_moved", "title": "Moved task", "status": "running"})
    for index in range(240):
        app.store.add_message(main, "assistant", f"History message {index}")
    async with chat_page(app) as page:
        await page.evaluate("() => weebo.chat.loadOlder()")
        app.store.update_message(card["id"], data={**card["data"], "status": "completed"})
        app.store.update_message(moved["id"], data={**moved["data"], "status": "completed"})
        app.store.bring_forward(moved["id"])
        await refresh(page)
        for message_id in (card["id"], moved["id"]):
            node = page.locator(f'[data-id="{message_id}"]')
            assert await node.count() == 1
            assert await node.get_by_role("button", name="Stop", exact=True).count() == 0
        assert await page.evaluate("() => weebo.chat.messages.at(-1).id") == moved["id"]


async def test_refresh_keeps_visible_message_anchored_after_earlier_content_grows(app):
    main = app.conversations.main_id()
    messages = [app.store.add_message(main, "assistant", f"Message {i} to read.") for i in range(60)]
    async with chat_page(app) as page:
        anchor_id = messages[20]["id"]
        before = await page.evaluate("""id => {
          const c = weebo.chat, n = c.list.querySelector(`[data-id="${id}"]`);
          c.scroller.scrollTop += n.getBoundingClientRect().top - c.scroller.getBoundingClientRect().top - 20;
          c.stick = false;
          return n.getBoundingClientRect().top;
        }""", anchor_id)
        app.store.update_message(messages[0]["id"], content="A much longer earlier answer. " * 100)
        await refresh(page)
        after = await page.locator(f'[data-id="{anchor_id}"]').evaluate("node => node.getBoundingClientRect().top")
        assert abs(after - before) < 3
        assert not await page.evaluate("() => weebo.chat.stick")


async def test_older_chat_has_no_mutating_buttons_and_cannot_submit(app):
    older = app.conversations.create("Read only history")
    app.store.add_message(older["id"], "event", "Old task", kind="agent_card",
                          data={"task_id": "old", "title": "Old task", "status": "running"})
    app.store.add_message(older["id"], "event", "Old proposal", kind="evolution_status",
                          data={"proposal_id": "old", "title": "Old proposal", "status": "ready"})
    app.store.add_message(older["id"], "event", "Old approval", kind="approval", status="pending",
                          data={"id": "old", "kind": "confirm"})
    async with chat_page(app) as page:
        await page.evaluate("id => weebo.openConversation(id)", older["id"])
        for name in ("Merge", "Stop", "Allow", "Deny", "Not now"):
            assert await page.locator('.chat-list').get_by_role("button", name=name, exact=True).count() == 0
        await page.evaluate("() => {weebo.chat.input.value = 'Should not send'; return weebo.chat.submit();}")
        assert not any(m["content"] == "Should not send" for m in app.store.list_messages(older["id"]))


async def test_live_agent_progress_survives_reload_and_offline_completion(app):
    from weebo.agents.manager import AgentRun

    task = app.store.create_task("Live task", "Work")
    app.store.update_task(task["id"], status="running")
    app.agents.runs[task["id"]] = AgentRun(task["id"], progress="Running the tests")
    async with chat_page(app) as page:
        await page.locator('#tabbar').get_by_role("button", name=re.compile("^More")).click()
        await page.locator("#drawer .more-row").filter(has_text="Agents").first.click()
        await page.locator('#drawer').get_by_text("Running the tests", exact=True).wait_for()
        app.store.update_task(task["id"], status="completed")
        app.agents.runs.pop(task["id"])
        await page.evaluate("() => weebo.refreshTaskCount()")
        assert await page.evaluate("() => weebo.runningTasks") == 0
        assert not await page.evaluate("id => weebo.taskProgress.has(id)", task["id"])
