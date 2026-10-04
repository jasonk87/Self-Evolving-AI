"""Unread notification recovery and explicit, identity-scoped acknowledgment."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from weebo.server.app import TOKEN_KEY, create_app


@pytest.mark.asyncio
async def test_unread_api_keeps_old_records_and_requires_explicit_ids(app):
    ready = app.store.add_notification("evolution", "Upgrade ready to review", data={"proposal_id": "p_ready"})
    failed = app.store.add_notification("agent", "Agent failed", data={"task_id": "t_failed"})
    for i in range(65):
        note = app.store.add_notification("weebo", f"History {i}")
        app.store.mark_notifications_read([note["id"]])
    events = []
    app.bus.subscribe("notifications.read", lambda topic, data: events.append(data))
    web_app = create_app(app)
    async with TestClient(TestServer(web_app)) as client:
        headers = {"Host": "127.0.0.1", "X-Weebo-Token": web_app[TOKEN_KEY]}
        for path, key in (("/api/bootstrap", "notifications"), ("/api/notifications?unread_only=1", "notifications")):
            data = await (await client.get(path, headers=headers)).json()
            assert {n["id"] for n in data[key]} == {ready["id"], failed["id"]}
        for body, expected in (({}, 400), ({"ids": "all"}, 400), ({"ids": []}, 200)):
            assert (await client.post("/api/notifications/read", json=body, headers=headers)).status == expected
            assert len(app.store.list_notifications(unread_only=True)) == 2
        app.store.mark_notifications_read([])
        assert len(app.store.list_notifications(unread_only=True)) == 2
        assert (await client.post("/api/notifications/read", json={"ids": [ready["id"]]}, headers=headers)).status == 200
        assert events[-1]["ids"] == [ready["id"]]
        assert [n["id"] for n in app.store.list_notifications(unread_only=True)] == [failed["id"]]


@pytest.mark.asyncio
async def test_completion_recovery_actions_races_and_browser_capability(app):
    playwright = pytest.importorskip("playwright.async_api")
    proposal = app.store.add_proposal("Recover this upgrade", "A bounded change")
    app.store.update_proposal(proposal["id"], status="ready")
    task = app.store.create_task("Failed build", "Build a change")
    app.store.update_task(task["id"], status="failed", error="Build failed")
    errors = []
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Install the browser with python -m playwright install chromium")
            raise
        async with browser:
            page = await browser.new_page(viewport={"width": 390, "height": 844})
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.add_init_script("""{
                const NativeSocket = window.WebSocket;
                window.WebSocket = class extends NativeSocket {
                    constructor(...args) { super(...args); window.eventSocket = this; }
                };
                class FakeNotification {
                    static permission = 'granted';
                    constructor(title, opts) {
                        window.desktopAttempts = (window.desktopAttempts || 0) + 1;
                        if (window.failDesktop) throw new Error('Constructor unavailable');
                        window.lastDesktop = this;
                        this.tag = opts.tag;
                    }
                }
                window.Notification = FakeNotification;
            }""")
            await page.goto(str(server.make_url("/")))
            await page.wait_for_function("() => window.weebo?.currentConvId && !document.body.classList.contains('offline')")
            await page.evaluate("eventSocket.close()")
            await page.wait_for_function("() => document.body.classList.contains('offline')")
            # Persisted while no socket is listening: no live event can deliver these.
            ready = app.store.add_notification("evolution", "Upgrade ready to review", "Review the upgrade", {"proposal_id": proposal["id"]})
            failed = app.store.add_notification("agent", "Agent failed", "Build failed", {"task_id": task["id"]})
            await page.wait_for_function("id => weebo.notifications.unread.has(id)", arg=ready["id"])
            banner = page.locator(".notification-banner")
            assert await banner.is_visible()
            await banner.get_by_role("button", name="Notification inbox").click()
            ready_row = page.locator(f'[data-notification-id="{ready["id"]}"]')
            failed_row = page.locator(f'[data-notification-id="{failed["id"]}"]')
            await failed_row.wait_for()
            assert len(app.store.list_notifications(unread_only=True)) == 2
            await ready_row.get_by_role("button", name="Review", exact=True).click()
            await page.locator("#drawer h3").get_by_text(proposal["title"], exact=True).wait_for()
            assert await page.evaluate("location.hash") == f"#evolution/{proposal['id']}"
            assert len(app.store.list_notifications(unread_only=True)) == 2
            await page.reload()
            await banner.wait_for()
            await page.locator("#drawer-close").click()
            await banner.get_by_role("button", name="Notification inbox").click()
            await failed_row.wait_for()
            await failed_row.get_by_role("button", name="Open", exact=True).click()
            await page.locator("#drawer h3").get_by_text(task["title"], exact=True).wait_for()
            # Foreground catches a completion even without a websocket event.
            foreground = app.store.add_notification("agent", "Foreground job failed", data={"task_id": "t_foreground"})
            await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            await page.wait_for_function("id => weebo.notifications.unread.has(id)", arg=foreground["id"])
            # Repeated real socket reconnects never render recovery popups.
            for _ in range(2):
                await page.evaluate("eventSocket.close()")
                await page.wait_for_function("() => document.body.classList.contains('offline')")
                await page.wait_for_function("() => !document.body.classList.contains('offline') && !weebo.notifications.syncing")
            assert await page.locator("#toasts .toast").count() == 0
            assert await page.evaluate("weebo.notifications.unread.size") == 3
            # Hold a stale recovery snapshot, then deliver a new live completion.
            captured, release = asyncio.Event(), asyncio.Event()

            async def hold_snapshot(route):
                response = await route.fetch()
                snapshot = await response.json()
                captured.set()
                await release.wait()
                await route.fulfill(response=response, body=json.dumps(snapshot))

            await page.route("**/api/notifications?unread_only=1", hold_snapshot)
            await page.evaluate("void weebo.notifications.reconcile()")
            await asyncio.wait_for(captured.wait(), 5)
            live = app.notify("agent", "Concurrent job failed", data={"task_id": "t_concurrent"})
            await page.wait_for_function("id => weebo.notifications.unread.has(id)", arg=live["id"])
            release.set()
            await page.wait_for_function("() => !weebo.notifications.syncing")
            await page.unroute("**/api/notifications?unread_only=1", hold_snapshot)
            assert await page.evaluate("id => weebo.notifications.unread.has(id)", live["id"])
            for _ in range(3):
                app.bus.publish("notify", {"notification": live})
            await page.evaluate("() => weebo.notifications.reconcile()")
            assert await page.locator("#toasts .toast").filter(has_text="Concurrent job failed").count() == 1
            # Recovery wins first, then a live duplicate: inbox stays accessible.
            app.bus.publish("notify", {"notification": ready})
            await page.locator("#drawer-close").click()
            await banner.get_by_role("button", name="Notification inbox").click()
            await ready_row.wait_for()
            captured.clear()
            release.clear()
            await page.route("**/api/notifications?unread_only=1", hold_snapshot)
            await page.evaluate("void weebo.notifications.reconcile()")
            await asyncio.wait_for(captured.wait(), 5)
            await ready_row.get_by_role("button", name="Acknowledge").click()
            await page.wait_for_function("id => !weebo.notifications.unread.has(id)", arg=ready["id"])
            release.set()
            await page.wait_for_function("() => !weebo.notifications.syncing")
            await page.unroute("**/api/notifications?unread_only=1", hold_snapshot)
            assert not await page.evaluate("id => weebo.notifications.unread.has(id)", ready["id"])
            assert {n["id"] for n in app.store.list_notifications(unread_only=True)} == {failed["id"], foreground["id"], live["id"]}
            # A delayed unread snapshot/event must not revive an acknowledged id.
            await page.evaluate("note => weebo.onNotification(note)", ready)
            await page.reload()
            await banner.get_by_role("button", name="Notification inbox").click()
            await failed_row.wait_for()
            assert await ready_row.count() == 1  # acknowledged history, with no acknowledge button
            assert await ready_row.get_by_role("button", name="Acknowledge").count() == 0
            assert await page.evaluate("weebo.notifications.unread.size") == 3
            # Browser delivery failure is visible and never consumes the inbox record.
            desktop = app.store.add_notification("agent", "Desktop failure", data={"task_id": "t_desktop"})
            await page.evaluate("""note => {
                Object.defineProperty(document, 'hidden', {configurable: true, value: true});
                window.failDesktop = true;
                weebo.onNotification(note);
                Object.defineProperty(document, 'hidden', {configurable: true, value: false});
                weebo.panels.open('settings');
            }""", desktop)
            await page.get_by_text("Browser alert failed: Constructor unavailable. Your alert remains in the inbox.", exact=True).wait_for()
            assert "True background delivery" in await page.locator("#drawer-body").inner_text()
            assert await page.evaluate("id => weebo.notifications.unread.has(id)", desktop["id"])
            # Supported constructor and async delivery error are covered separately.
            await page.evaluate("""note => {
                Object.defineProperty(document, 'hidden', {configurable: true, value: true});
                window.failDesktop = false;
                weebo.notifications.desktop(note);
                window.lastDesktop.onerror();
                Object.defineProperty(document, 'hidden', {configurable: true, value: false});
            }""", desktop)
            assert await page.evaluate("lastDesktop.tag") == desktop["id"]
            await page.get_by_text("Browser alert failed: The browser could not show the notification. Your alert remains in the inbox.", exact=True).wait_for()
            await page.evaluate("Notification.permission = 'denied'; weebo.panels.refresh()")
            await page.get_by_text("Browser alerts blocked in browser permissions.", exact=True).wait_for()
            await page.evaluate("delete window.Notification; weebo.panels.refresh()")
            await page.get_by_text("Browser alerts unsupported on this device.", exact=True).wait_for()
            assert errors == []
