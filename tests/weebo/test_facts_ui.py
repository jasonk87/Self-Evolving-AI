import pytest
from aiohttp.test_utils import TestClient, TestServer
from playwright.async_api import async_playwright

from weebo.server.app import TOKEN_KEY, create_app
from .browser_support import launch_chromium

pytestmark = pytest.mark.asyncio


async def test_browser_prerequisite_uses_installed_chrome_instead_of_skipping(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from . import browser_support
    calls = []

    async def launch(**kwargs):
        calls.append(kwargs)
        return "browser"

    pw = SimpleNamespace(chromium=SimpleNamespace(executable_path=str(tmp_path / "missing"), launch=launch))
    monkeypatch.setattr(browser_support.shutil, "which", lambda name: "/example/chrome" if name == "google-chrome" else None)
    assert await launch_chromium(pw) == "browser"
    assert calls == [{"headless": True, "executable_path": "/example/chrome"}]


async def test_facts_api_is_private_and_validates_filters(app):
    web_app = create_app(app)
    async with TestClient(TestServer(web_app)) as client:
        headers = {"Host": "127.0.0.1", "X-Weebo-Token": web_app[TOKEN_KEY]}
        assert (await client.get("/api/facts/events", headers={"Host": "127.0.0.1"})).status == 401
        result = await client.get("/api/facts/events", headers=headers)
        assert result.status == 200 and not (await result.json())["complete"]
        assert (await client.get("/api/facts/no_such_table", headers=headers)).status == 400
        assert (await client.get("/api/facts/events?limit=bad", headers=headers)).status == 400


async def test_facts_panel_distinguishes_unavailable_and_shows_commitment_provenance(app):
    app.facts.repository.upsert("commitments", {"text": "Send the sample deck", "owner": "assistant",
                                                "source_ref": "conversation:example", "due": 1900000000},
                                source="local:commitments")
    async with TestServer(create_app(app)) as server, async_playwright() as pw:
        browser = await launch_chromium(pw)
        async with browser:
            page = await browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            await page.goto(str(server.make_url("/")))
            await page.wait_for_function("() => window.weebo?.chat?.convId")
            await page.locator("#tabbar").get_by_role("button", name="More", exact=True).click()
            await page.locator("#drawer .more-row").filter(has_text="Memory").first.click()
            await page.get_by_role("button", name="Source-backed facts", exact=True).click()
            await page.get_by_text("Current facts could not be verified.", exact=True).wait_for()
            await page.get_by_text("events: No source is connected", exact=True).wait_for()
            await page.get_by_role("button", name="commitments", exact=True).click()
            await page.get_by_text("Send the sample deck", exact=True).wait_for()
            await page.get_by_text("Source: local:commitments", exact=False).wait_for()
            assert await page.locator(".facts-panel").evaluate("e => e.scrollWidth <= e.clientWidth + 1")
            assert not errors


async def test_real_service_worker_does_not_cache_private_facts(app):
    async with TestServer(create_app(app)) as server, async_playwright() as pw:
        browser = await launch_chromium(pw)
        async with browser:
            page = await browser.new_page(service_workers="allow")
            await page.goto(str(server.make_url("/")))
            await page.evaluate("() => navigator.serviceWorker.ready")
            await page.wait_for_function("() => navigator.serviceWorker.controller !== null")
            result = await page.evaluate("""async () => {
                const { api } = await import('/static/js/api.js');
                const facts = await api.get('/api/facts/events');
                const keys = (await Promise.all((await caches.keys()).map(async name => {
                    const cache = await caches.open(name);
                    return (await cache.keys()).map(r => new URL(r.url).pathname);
                }))).flat();
                return {facts, keys};
            }""")
            assert not result["facts"]["complete"]
            assert result["facts"]["sources"][0]["error"] == "No source is connected"
            assert not any(path.startswith("/api/") for path in result["keys"])
