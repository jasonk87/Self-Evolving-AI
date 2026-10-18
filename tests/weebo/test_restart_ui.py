"""Real-browser regressions for startup, reconnection and repeated self-upgrade restarts."""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from aiohttp.test_utils import TestServer

from weebo.server.app import HUB_KEY, create_app

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def browser_page(app):
    playwright = pytest.importorskip("playwright.async_api")
    app.settings.update({"autonomy.quiet_hours_start": "00:00", "autonomy.quiet_hours_end": "00:00"})
    web_app = create_app(app)
    errors = []
    async with TestServer(web_app) as server, playwright.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        async with browser:
            page = await browser.new_page(viewport={"width": 390, "height": 844},
                                          is_mobile=True, service_workers="block")
            page.on("pageerror", lambda error: errors.append(str(error)))
            yield page, str(server.make_url("/")), web_app[HUB_KEY]
    assert not errors


async def loaded(page, url):
    await page.goto(url)
    await page.wait_for_function("() => weebo.mainId && !weebo.chat.loadingEvents && "
                                 "!document.body.classList.contains('offline')")


async def test_expired_caption_does_not_pin_the_old_startup_status(app):
    async with browser_page(app) as (page, url, _):
        await loaded(page, url)
        await page.clock.install()
        await page.evaluate("""() => {
          weebo.chat.stage.setResting('Connecting to my Codex brain…');
          weebo.chat.stage.say('Merging it in…', 4000);
        }""")
        await page.clock.run_for(4100)
        assert await page.locator('.stage-say').inner_text() == "Connecting to my Codex brain…"
        await page.evaluate("() => weebo.chat.stage.setResting('Ready now')")
        await page.clock.run_for(100)
        assert await page.locator('.stage-say').inner_text() == "Ready now"
        # Replacing a timed caption with an untimed one must also retire its old timer.
        await page.evaluate("""() => {
          weebo.chat.stage.say('Done', 2500);
          weebo.chat.stage.say('An immediate update', 0);
          weebo.chat.stage.setResting('The current status');
        }""")
        await page.clock.run_for(2600)
        assert await page.locator('.stage-say').inner_text() == "The current status"


async def test_live_ready_event_wins_over_a_delayed_startup_snapshot(app, fake_engine):
    async with browser_page(app) as (page, url, _):
        captured = asyncio.Event()
        release = asyncio.Event()

        async def delay_bootstrap(route):
            response = await route.fetch()
            data = await response.json()
            data["snapshot"]["engine"]["status"] = "starting"
            data["activity"] = {"state": "sleeping", "now": None, "entries": []}
            captured.set()
            await release.wait()
            await route.fulfill(response=response, body=json.dumps(data))

        await page.route("**/api/bootstrap", delay_bootstrap)
        await page.goto(url)
        await asyncio.wait_for(captured.wait(), 10)
        await page.wait_for_function("() => weebo.connectedOnce")
        app.bus.publish("engine.status", fake_engine.snapshot())
        app.bus.publish("activity.updated", {"activity": {"state": "idle", "now": None, "entries": []}})
        await page.wait_for_function("() => weebo.state.engine.status === 'ready' && weebo.state.activity.state === 'idle'")
        release.set()
        await page.wait_for_function("() => weebo.mainId && !weebo.chat.loadingEvents")
        assert await page.evaluate("() => weebo.state.engine.status") == "ready"
        assert not await page.locator('#engine-pill').is_visible()
        assert "Connecting" not in await page.locator('.stage-say').inner_text()
        assert "Resting" in await page.locator('.now-text').inner_text()


async def test_reconnect_recovers_status_and_activity_missed_offline(app, fake_engine):
    from weebo.brain.activity_registry import Entry, BACKGROUND

    fake_engine.status = "starting"
    async with browser_page(app) as (page, url, hub):
        await loaded(page, url)
        await page.locator('#engine-pill').get_by_text("Starting Codex…", exact=True).wait_for()
        # Change the server state without sending events: the next hello must reconcile it.
        fake_engine.status = "ready"
        app.activity.entries["sync"] = Entry("sync", "system", "Weebo", BACKGROUND, "syncing", "Syncing facts")
        await page.evaluate("() => { weebo.reconnects = 0; }")
        await page.evaluate("""async () => {
          const {on} = await import('/static/js/api.js');
          on('hello', () => weebo.reconnects++);
        }""")
        await hub.close_all()
        await page.wait_for_function("() => weebo.reconnects > 0")
        assert await page.evaluate("() => weebo.state.engine.status") == "ready"
        assert not await page.locator('#engine-pill').is_visible()
        assert "Syncing facts" in await page.locator('.now-text').inner_text()
        assert "Connecting" not in await page.locator('.stage-say').inner_text()


async def test_ready_restores_avatar_without_waiting_for_the_mood_tick(app, fake_engine):
    fake_engine.status = "starting"
    async with browser_page(app) as (page, url, _):
        await loaded(page, url)
        fake_engine.status = "ready"
        app.bus.publish("engine.status", fake_engine.snapshot())
        await page.wait_for_function("() => weebo.state.engine.status === 'ready'")
        assert await page.locator('.stage-weebo .weebo').evaluate("n => n.classList.contains('mood-idle')")


@pytest.mark.parametrize("status, words", [("error", "unavailable"), ("stopped", "stopped")])
async def test_terminal_engine_states_do_not_claim_to_be_connecting(app, fake_engine, status, words):
    fake_engine.status = status
    async with browser_page(app) as (page, url, _):
        await loaded(page, url)
        await page.wait_for_function("text => document.querySelector('.stage-say').textContent.includes(text)", arg=words)
        assert "Connecting" not in await page.locator('.stage-say').inner_text()


async def test_restart_waits_for_new_session_not_old_server_or_network_blip(app):
    async with browser_page(app) as (page, url, _):
        await page.add_init_script("sessionStorage.restartLoads = String(Number(sessionStorage.restartLoads || 0) + 1)")
        await loaded(page, url)
        await page.clock.install()
        mode = "old"
        probes = []

        async def probe(route):
            if route.request.is_navigation_request():
                return await route.continue_()
            probes.append(mode)
            if mode == "down":
                return await route.fulfill(status=503, body="Unavailable")
            response = await route.fetch()
            body = await response.text()
            if mode == "new":
                body = body.replace('name="weebo-token" content="', 'name="weebo-token" content="new-')
            await route.fulfill(response=response, body=body)

        await page.route(url, probe)
        await page.evaluate("() => weebo.showRestarting('Applying upgrades')")
        await page.clock.fast_forward(32000)
        await page.wait_for_function("() => document.querySelector('.restart-overlay').textContent.includes('32s')")
        assert await page.evaluate("() => sessionStorage.restartLoads") == "1"
        mode = "down"
        await page.clock.run_for(1000)
        await page.wait_for_function("() => document.querySelector('.restart-overlay').textContent.includes('33s')")
        assert "down" in probes
        mode = "old"
        await page.clock.run_for(1000)
        await page.wait_for_function("() => document.querySelector('.restart-overlay').textContent.includes('34s')")
        assert await page.evaluate("() => sessionStorage.restartLoads") == "1"
        mode = "new"
        await page.clock.run_for(1000)
        await page.wait_for_function("() => sessionStorage.restartLoads === '2'")
        await page.wait_for_function("() => weebo.mainId && !weebo.chat.loadingEvents")
        assert await page.locator('.restart-overlay').count() == 0


async def test_repeated_restart_events_keep_one_screen_and_its_own_animation(app):
    async with browser_page(app) as (page, url, _):
        await loaded(page, url)
        app.bus.publish("system.restarting", {"reason": "Applying an upgrade"})
        app.bus.publish("system.restarting", {"reason": "Applying the next upgrade"})
        await page.locator('.restart-overlay').first.wait_for()
        # These normally occur during shutdown; neither should turn the restart avatar idle/offline/happy.
        await page.evaluate("""async () => {
          const {director} = await import('/static/js/avatar.js');
          weebo.setConnection('offline');
          director.flash('happy', 2500);
        }""")
        assert await page.locator('.restart-overlay').count() == 1
        assert await page.locator('.restart-weebo .weebo').evaluate("n => n.classList.contains('mood-syncing')")


async def test_connecting_mid_restart_still_shows_the_restart_screen(app):
    app._restart_reason = "Upgrade already merged"
    async with browser_page(app) as (page, url, _):
        await loaded(page, url)
        await page.locator('.restart-overlay').wait_for()
        assert "Upgrade already merged" in await page.locator('.restart-overlay').inner_text()
