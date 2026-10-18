"""Browser test: one chat with a tab bar, live agent cards, upgrade cards you can act on, reply chips, and older chats
kept to read."""

import pytest
from aiohttp.test_utils import TestServer

from weebo.agents.manager import AgentRun
from weebo.server.app import create_app


@pytest.mark.asyncio
async def test_one_chat_cards_chips_tabs_and_archive(app, fake_engine, monkeypatch):
    playwright = pytest.importorskip("playwright.async_api")
    main = app.conversations.main_id()
    older = app.conversations.create(title="Trip planning")
    app.store.add_message(older["id"], "user", "Where should we stay in Tahoe?")
    task = await app.agents.start("Fix the build", "Make the tests pass.", conversation_id=main)
    app.store.update_task(task["id"], status="running")
    app.agents._sync_card(task["id"])
    stopped, merged = [], []

    async def stop(task_id):
        stopped.append(task_id)

    async def merge(proposal_id, automatic=False, approved_by=""):
        merged.append(proposal_id)
        return app.store.get_proposal(proposal_id)

    monkeypatch.setattr(app.agents, "stop", stop)
    monkeypatch.setattr(app.evolution, "merge", merge)
    monkeypatch.setattr(app, "request_restart", lambda reason: None)
    proposal = await app.evolution.propose("Faster recall", "Make memory search quicker.", source="user",
                                           conversation_id=main)
    app.store.update_proposal(proposal["id"], status="ready")
    app.evolution._publish(proposal["id"])
    app.store.add_message(main, "assistant", "Worth a quick check tonight: is 10-11 their local time?",
                          data={"suggestions": ["I'll check with the seller tonight", "That 10-11 was just my block"]})
    errors = []
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Install the browser with python -m playwright install chromium")
            raise
        async with browser:
            page = await browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(str(server.make_url("/")))
            await page.wait_for_function("id => window.weebo?.chat?.convId === id && "
                                         "!document.body.classList.contains('offline')", arg=main)
            tabbar = page.locator("#tabbar")
            assert await tabbar.is_visible()

            # The agent's card: plain words while it works, and a Stop that works.
            card = page.locator(f'[data-id="agent:{task["id"]}"]')
            await card.get_by_text("Fix the build").wait_for()
            run = app.agents.runs.setdefault(task["id"], AgentRun(task["id"]))
            app.agents._set_progress(run, "Running the tests", "reviewing")
            app.agents._sync_card(task["id"])
            await card.get_by_text("Running the tests").wait_for()
            await card.get_by_role("button", name="Stop").click()
            for _ in range(50):
                if stopped:
                    break
                await page.wait_for_timeout(50)
            assert stopped == [task["id"]]

            # A notification about it has nothing left to tell: the card is right here.
            note = app.notify("agent", "Agent “Fix the build” finished", "All green.", {"task_id": task["id"]})
            for _ in range(200):  # the browser receives it, sees the card, and marks it read on the server
                if note["id"] not in [n["id"] for n in app.store.list_notifications(unread_only=True)]:
                    break
                await page.wait_for_timeout(25)
            assert note["id"] not in [n["id"] for n in app.store.list_notifications(unread_only=True)]
            await page.wait_for_function("id => weebo.notifications.seen.has(id) && !weebo.notifications.unread.has(id)",
                                         arg=note["id"])
            assert not await page.locator(".notification-banner").is_visible()

            # The upgrade's card: merge right from the chat.
            upgrade = page.locator(f'[data-id="evolution:{proposal["id"]}"]')
            await upgrade.get_by_role("button", name="Merge").click()
            for _ in range(50):
                if merged:
                    break
                await page.wait_for_timeout(50)
            assert merged == [proposal["id"]]

            # Reply chips under the newest answer send when tapped.
            chip = page.get_by_role("button", name="I'll check with the seller tonight")
            await chip.click()
            for _ in range(100):
                if any("I'll check with the seller tonight" in str(turn["input"]) for turn in fake_engine.turns):
                    break
                await page.wait_for_timeout(50)
            assert "I'll check with the seller tonight" in str(fake_engine.turns[-1])
            await page.wait_for_function("() => !document.querySelector('.reply-chip')")

            # Running work shows in plain words up top, never the command.
            await page.evaluate("""id => weebo.chat.upsert({id: 'cmd-1', conversation_id: id, role: 'tool', kind: 'command',
                status: 'running', seq: 999999, content: 'bash -lc "python -m pytest -q tests/weebo"',
                data: {doing: 'Running the tests', activity: 'reviewing'}})""", main)
            stage = page.locator(".stage-say")
            await stage.get_by_text("Running the tests").wait_for()
            assert "pytest" not in await stage.inner_text()

            # Tabs: Agents (under More) opens its panel, Chat brings you back.
            await tabbar.get_by_role("button", name="More").click()
            await page.locator("#drawer .more-row").filter(has_text="Agents").first.click()
            await page.locator("#drawer h3").get_by_text("Agents", exact=True).wait_for()
            await tabbar.get_by_role("button", name="Chat").click()
            await page.wait_for_function("() => !document.body.classList.contains('drawer-open')")

            # Older chats: kept to read, with the way back to the one chat.
            await tabbar.get_by_role("button", name="More").click()
            await page.locator("#drawer").get_by_text("Older chats").first.click()
            await page.locator("#drawer").get_by_text("Trip planning").click()
            await page.wait_for_function("id => weebo.chat.convId === id", arg=older["id"])
            await page.get_by_text("Where should we stay in Tahoe?").wait_for()
            assert not await page.locator(".composer").is_visible()
            await page.get_by_role("button", name="Back to Weebo").click()
            await page.wait_for_function("id => weebo.chat.convId === id", arg=main)
            await page.locator(".composer").wait_for(state="visible")
            assert not await page.locator(".old-chat-bar").is_visible()
            assert errors == []
