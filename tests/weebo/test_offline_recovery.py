"""Exercise the actual installed service worker, not a mocked offline screen."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright

from weebo.server.app import LOCKED_PAGE, create_app
from .browser_support import launch_chromium

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def offline_browser(app):
    state = {"status": 0, "requests": 0}

    @web.middleware
    async def availability(request, handler):
        if request.path == "/":
            state["requests"] += 1
            if state["status"]:
                body = LOCKED_PAGE if state["status"] == 401 else "Upstream unavailable"
                return web.Response(status=state["status"], text=body, content_type="text/html")
        return await handler(request)

    web_app = create_app(app)
    web_app.middlewares.insert(0, availability)
    async with TestServer(web_app) as server, async_playwright() as pw:
        browser = await launch_chromium(pw)
        async with browser:
            context = await browser.new_context(viewport={"width": 390, "height": 844},
                                                is_mobile=True, service_workers="allow")
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            url = str(server.make_url("/"))
            await page.goto(url)
            await page.wait_for_function("() => Boolean(window.weebo?.chat?.convId)", timeout=10000)
            await asyncio.wait_for(page.evaluate("() => navigator.serviceWorker.ready"), 10)
            await page.wait_for_function("() => navigator.serviceWorker.controller !== null")
            yield page, context, url, state
            assert not errors


async def test_offline_page_recovers_when_phone_network_returns(app):
    async with offline_browser(app) as (page, context, url, _):
        await context.set_offline(True)
        await page.goto(url)
        await page.get_by_role("heading", name="Can't reach Weebo").wait_for()
        await context.set_offline(False)
        await page.wait_for_function("() => Boolean(window.weebo?.chat?.convId)", timeout=10000)
        assert not await page.get_by_role("heading", name="Can't reach Weebo").count()
        assert await page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")


async def test_offline_page_polls_after_restart_without_a_network_event(app):
    async with offline_browser(app) as (page, _, url, state):
        state["status"] = 503
        await page.goto(url)
        await page.get_by_role("heading", name="Can't reach Weebo").wait_for()
        state["status"] = 0
        await page.wait_for_function("() => Boolean(window.weebo?.chat?.convId)", timeout=10000)


async def test_retry_keeps_screen_for_bad_responses_then_returns_to_access_gate(app):
    async with offline_browser(app) as (page, _, url, state):
        state["status"] = 502
        await page.goto(url)
        heading = page.get_by_role("heading", name="Can't reach Weebo")
        await heading.wait_for()
        await page.get_by_text("Retrying automatically", exact=False).wait_for()
        # A captive portal / wrong upstream is not proof that Weebo is back.
        state["status"] = 200
        await page.get_by_role("button", name="Try again", exact=True).click()
        await page.get_by_text("Still can't reach Weebo", exact=False).wait_for()
        assert await heading.is_visible()
        # Returning an access-key page is recovery, not a reason to loop offline.
        state["status"] = 401
        await page.get_by_role("button", name="Try again", exact=True).click()
        await page.get_by_role("heading", name="Weebo is private").wait_for()


async def test_reopening_offline_app_checks_immediately_and_caches_no_private_page(app):
    async with offline_browser(app) as (page, _, url, state):
        state["status"] = 504
        await page.goto(url)
        await page.get_by_role("heading", name="Can't reach Weebo").wait_for()
        state["status"] = 0
        await page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
        await page.wait_for_function("() => Boolean(window.weebo?.chat?.convId)", timeout=10000)
        cached = await page.evaluate("""async () => (await Promise.all((await caches.keys()).map(async name =>
            (await (await caches.open(name)).keys()).map(r => new URL(r.url).pathname)))).flat()""")
        assert cached and all(path.startswith("/static/") for path in cached)
