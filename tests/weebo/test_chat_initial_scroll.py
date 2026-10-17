"""Opening a chat must settle its scroll before delayed animation frames run."""

import asyncio

import pytest
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright

from weebo.server.app import create_app
from .browser_support import launch_chromium


@pytest.mark.asyncio
async def test_open_long_chat_settles_scroll_before_animation_frame(app):
    old = app.conversations.create(title="Older example")
    for i in range(205):
        app.store.add_message(old["id"], "user", f"History message {i}")
    async with TestServer(create_app(app)) as server, async_playwright() as pw:
        browser = await launch_chromium(pw)
        async with browser:
            page = await browser.new_page(viewport={"width": 390, "height": 844}, service_workers="block")
            await page.goto(str(server.make_url("/")))
            await page.wait_for_function("() => window.weebo?.connectedOnce && weebo.chat.convId && !weebo.chat.loadingEvents")
            position = await page.evaluate("""async id => {
                const original = window.requestAnimationFrame;
                window.requestAnimationFrame = () => 0;
                try {
                    await weebo.openConversation(id);
                    const s = weebo.chat.scroller;
                    return {top: s.scrollTop, bottom: s.scrollHeight - s.clientHeight, count: weebo.chat.messages.length};
                } finally { window.requestAnimationFrame = original; }
            }""", old["id"])
            assert position["count"] == 200
            assert position["bottom"] > 1000
            assert abs(position["top"] - position["bottom"]) <= 1


@pytest.mark.asyncio
async def test_explicit_history_read_waits_for_snapshot_refresh(app):
    old = app.conversations.create(title="Snapshot example")
    for i in range(205):
        app.store.add_message(old["id"], "user", f"Snapshot history {i}")
    async with TestServer(create_app(app)) as server, async_playwright() as pw:
        browser = await launch_chromium(pw)
        async with browser:
            page = await browser.new_page(viewport={"width": 390, "height": 844}, service_workers="block")
            await page.goto(str(server.make_url("/")))
            await page.wait_for_function("() => window.weebo?.connectedOnce && weebo.chat.convId && !weebo.chat.loadingEvents")
            await page.evaluate("id => weebo.openConversation(id)", old["id"])
            snapshot_requested, release = asyncio.Event(), asyncio.Event()

            async def delayed(route):
                response = await route.fetch()
                snapshot_requested.set()
                await release.wait()
                await route.fulfill(response=response)

            await page.route(f"**/api/conversations/{old['id']}", delayed)
            await page.evaluate("id => { window.refreshing = weebo.chat.open(id, {refresh: true}); }", old["id"])
            await asyncio.wait_for(snapshot_requested.wait(), 5)
            await page.evaluate("() => { window.older = weebo.chat.loadOlder(); }")
            release.set()
            await page.evaluate("() => Promise.all([window.refreshing, window.older])")
            assert await page.evaluate("weebo.chat.messages.length") == 205
            assert await page.get_by_text("Snapshot history 0", exact=True).count() == 1
