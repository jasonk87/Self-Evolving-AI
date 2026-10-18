"""Mobile Feed and chat-first bot acceptance, with the real browser and fake Codex."""
import asyncio

import pytest
from aiohttp.test_utils import TestServer

from weebo.server.app import create_app
from .test_connectors_growth import new_bot
from .test_weebo3_ui import open_page, until

pytestmark = pytest.mark.asyncio


async def test_project_to_idea_goal_and_existing_bot_job(app, fake_engine):
    pw = pytest.importorskip("playwright.async_api")
    app.store.add_memory("Build a birdhouse with Rowan", "project", source="chat:test")
    bot = new_bot(app, name="Builder")
    errors = []
    async with TestServer(create_app(app)) as server, pw.async_playwright() as playwright:
        browser, page = await open_page(playwright, server, errors)
        async with browser:
            await page.locator('#tabbar [data-panel="feed"]').click()
            card = page.locator(".idea-card").first
            await card.locator("summary").click()
            await card.locator("button.chip").first.click()
            source = page.get_by_role("dialog", name="Source", exact=True)
            assert "local:memories" in await source.inner_text()
            assert "not independent verification" in await source.inner_text()
            await source.get_by_role("button", name="Close", exact=True).click()
            await card.get_by_role("button", name="Start a goal").click()
            editor = page.get_by_role("dialog", name="Edit goal")
            await editor.get_by_label("Next step", exact=True).fill("Draft a short parts list")
            await editor.get_by_role("button", name="Save goal").click()
            await editor.wait_for(state="hidden")
            goal = app.feed.goals()[0]
            assert goal["data"]["next_step"] == "Draft a short parts list"
            assert not fake_engine.turns
            gc = page.locator(f'[data-goal="{goal["id"]}"]')
            await gc.locator("summary").click()
            await gc.get_by_role("button", name="Delegate", exact=True).click()
            dialog = page.get_by_role("dialog", name="Delegate:", exact=False)
            await dialog.get_by_label("Delegate to bot").select_option(bot["id"])
            await dialog.get_by_role("button", name="Assign job").click()
            assert await until(lambda: bool(app.bots.jobs(bot["id"])), page)
            await dialog.wait_for(state="hidden")
            assert len(app.bots.jobs(bot["id"])) == 1
            assert app.feed.require(goal["id"])["status"] == "active"
            turn = fake_engine.turns[-1]
            await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Parts list ready to review.")
            await gc.get_by_text("Job finished · review the outcome").wait_for()
            assert app.feed.require(goal["id"])["status"] == "active"
            assert not errors


async def test_bot_list_is_vertical_chat_first_and_drafts_stay_with_each_chat(app):
    pw = pytest.importorskip("playwright.async_api")
    bots = [new_bot(app, name=n) for n in ("Garden", "Research")]
    app.store.add_message(bots[1]["conversation_id"], "assistant", "Three books worth comparing.")
    errors = []
    async with TestServer(create_app(app)) as server, pw.async_playwright() as playwright:
        browser, page = await open_page(playwright, server, errors, reduced_motion="reduce")
        async with browser:
            await page.locator(".composer textarea").fill("Chief's unsent message")
            await page.locator('#tabbar [data-panel="bots"]').click()
            rows = page.locator(".bot-conversation")
            await rows.first.wait_for()
            boxes = [await rows.nth(i).bounding_box() for i in range(2)]
            assert boxes[1]["y"] >= boxes[0]["y"] + boxes[0]["height"] - 1
            assert "Three books worth comparing." in await rows.first.inner_text()
            await page.locator(f'.bot-conversation[data-bot="{bots[0]["id"]}"]').click()
            await page.wait_for_function("id => weebo.chat.convId === id", arg=bots[0]["conversation_id"])
            assert await page.locator(".composer textarea").input_value() == ""
            await page.locator(".composer textarea").fill("Garden's unsent message")
            await page.locator(".bot-chat-bar").get_by_role("button", name="Profile").click()
            await page.locator("#drawer-title").get_by_text("Manage bots").wait_for()
            await page.locator("#drawer-close").click()
            await page.locator(".bot-chat-bar").get_by_role("button", name="Back to Weebo").click()
            assert await page.locator(".composer textarea").input_value() == "Chief's unsent message"
            await page.locator('#tabbar [data-panel="bots"]').click()
            await page.locator(f'.bot-conversation[data-bot="{bots[0]["id"]}"]').click()
            assert await page.locator(".composer textarea").input_value() == "Garden's unsent message"
            await page.locator('#tabbar [data-panel="bots"]').click()
            await page.set_viewport_size({"width": 320, "height": 740})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors


async def test_feed_refresh_keeps_expansion_scroll_and_failed_refresh_keeps_content(app):
    pw = pytest.importorskip("playwright.async_api")
    for i in range(12):
        app.feed.create(f"Personal project {i}", next_step="A concrete next step to discuss")
    errors = []
    async with TestServer(create_app(app)) as server, pw.async_playwright() as playwright:
        browser, page = await open_page(playwright, server, errors)
        async with browser:
            await page.locator('#tabbar [data-panel="feed"]').click()
            card = page.locator(".goal-card").nth(3)
            await card.locator("summary").click()
            key = await card.get_attribute("data-feed-key")
            await page.locator("#drawer-body").evaluate("e => e.scrollTop = 250")
            before = await page.locator("#drawer-body").evaluate("e => e.scrollTop")
            await page.evaluate("weebo.panels.refresh({live:true})")
            assert await page.locator(f'[data-feed-key="{key}"]').evaluate("e => e.open")
            assert abs(await page.locator("#drawer-body").evaluate("e => e.scrollTop") - before) < 3
            await page.route("**/api/feed", lambda route: route.fulfill(status=503, body='{"error":"Offline"}'))
            await page.evaluate("weebo.panels.refresh({live:true})")
            assert await page.locator(".goal-card").count() == 12
            assert await page.locator(f'[data-feed-key="{key}"]').evaluate("e => e.open")
            assert not errors


async def test_failed_send_restores_only_its_original_bot_draft(app):
    pw = pytest.importorskip("playwright.async_api")
    bot = new_bot(app, name="Writer")
    errors = []
    started, release = asyncio.Event(), asyncio.Event()
    async def fail(route):
        started.set()
        await release.wait()
        await route.fulfill(status=503, body='{"error":"Try later"}')
    async with TestServer(create_app(app)) as server, pw.async_playwright() as playwright:
        browser, page = await open_page(playwright, server, errors)
        async with browser:
            await page.locator('#tabbar [data-panel="bots"]').click()
            await page.locator(".bot-conversation").click()
            await page.route(f"**/api/conversations/{bot['conversation_id']}/messages", fail)
            await page.locator(".composer textarea").fill("A draft for Writer only")
            await page.locator(".composer textarea").press("Enter")
            await asyncio.wait_for(started.wait(), 3)
            await page.locator(".bot-chat-bar").get_by_role("button", name="Back to Weebo").click()
            await page.locator(".composer textarea").fill("A separate chief message")
            release.set()
            await page.get_by_text("Message not sent", exact=True).wait_for()
            assert await page.locator(".composer textarea").input_value() == "A separate chief message"
            await page.locator('#tabbar [data-panel="bots"]').click()
            await page.locator(".bot-conversation").click()
            assert await page.locator(".composer textarea").input_value() == "A draft for Writer only"
            assert not errors


async def test_bot_completion_is_unread_not_an_unsolicited_toast(app):
    pw = pytest.importorskip("playwright.async_api")
    bot = new_bot(app, name="Scout")
    errors = []
    async with TestServer(create_app(app)) as server, pw.async_playwright() as playwright:
        browser, page = await open_page(playwright, server, errors)
        async with browser:
            message = app.store.add_message(bot["conversation_id"], "assistant", "The quiet report is ready.")
            app.bus.publish("conv.message", {"conversation_id": bot["conversation_id"], "message": message})
            app.bus.publish("conv.turn.completed", {"conversation_id": bot["conversation_id"], "status": "completed", "final_text": message["content"]})
            await page.locator('#tabbar [data-panel="bots"] .badge').wait_for(state="visible")
            assert "The quiet report is ready" not in await page.locator("#toasts").inner_text()
            await page.locator('#tabbar [data-panel="bots"]').click()
            await page.locator(".bot-conversation").click()
            await page.locator(".chat-list").get_by_text("The quiet report is ready.").wait_for()
            assert await page.locator('#tabbar [data-panel="bots"] .badge').is_hidden()
            assert not errors
