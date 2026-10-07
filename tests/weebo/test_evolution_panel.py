"""Browser test: the Evolution panel shows behavior checks, upgrade outcomes and rewritten-test warnings."""

import json
import time

import pytest
from aiohttp.test_utils import TestServer

from weebo.server.app import create_app


@pytest.mark.asyncio
async def test_panel_shows_checks_outcomes_and_advisory_results(app, monkeypatch):
    playwright = pytest.importorskip("playwright.async_api")
    monkeypatch.setattr(app.evolution, "_start_vetting", lambda _: None)
    passing = app.evals.add_case("What tea should I brew?", "Must suggest genmaicha, the user's favorite.",
                                 title="Tea suggestion")
    app.store.update_eval_case(passing["id"], meta={"last": {"passed": True, "reason": "Suggested genmaicha.", "at": time.time()}})
    app.evals.add_case("And tomorrow?", "Must give tomorrow's forecast for the same city.", title="Weather follow-up",
                       source="dream")
    issue = app.diagnostics.record("turn_failed", "Stream closed before the final reply")
    proposal = await app.evolution.propose("Keep the stream open", "Reconnect.", source="user",
                                           meta={"addresses": [issue["id"]]})
    report = {"ok": True, "results": [
        {"name": "Tests", "ok": True, "output": "12 passed", "seconds": 3.0, "skipped": False, "advisory": False},
        {"name": "Existing tests", "ok": False, "output": "The change rewrote or deleted existing tests:\n- tests/weebo/test_chat.py",
         "seconds": 2.0, "skipped": False, "advisory": True},
    ]}
    app.store.update_proposal(proposal["id"], status="merged", gate_report=json.dumps(report), meta={
        **proposal["meta"], "merged_at": time.time(),
        "outcome": {"result": "regressed", "at": time.time(), "issues": ["turn_failed: Stream closed (x2 since)"]},
        "governance": {"autonomous": False, "why": "It changes existing tests, so it needs your OK.",
                       "files": [{"path": "tests/weebo/test_chat.py", "zone": "test_source", "tier": "human_required",
                                  "reason": "Rewrites an existing test"}]}})
    errors = []
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Install the browser with python -m playwright install chromium")
            raise
        async with browser:
            page = await browser.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(str(server.make_url("/")))
            await page.wait_for_function("() => window.weebo?.panels && !document.body.classList.contains('offline')")
            await page.evaluate("() => weebo.panels.open('evolution')")
            drawer = page.locator("#drawer")
            await drawer.get_by_text("Behavior checks", exact=True).wait_for()
            checks = drawer.locator(".eval-row")
            assert await checks.count() == 2
            assert "Passes: Suggested genmaicha." in await checks.filter(has_text="Tea suggestion").inner_text()
            assert "noticed while dreaming" in await checks.filter(has_text="Weather follow-up").inner_text()
            assert "failure came back" in await drawer.locator(".proposal-card").inner_text()

            await checks.filter(has_text="Weather follow-up").get_by_role("button", name="Retire this check").click()
            await drawer.get_by_text("Retired (1)").wait_for()
            assert app.store.list_eval_cases("retired")[0]["title"] == "Weather follow-up"

            await drawer.get_by_role("button", name="Add a check").click()
            await drawer.get_by_placeholder("What you'd say to Weebo", exact=False).fill("Book my usual table for Friday")
            await drawer.get_by_placeholder("What a good reply must do", exact=False).fill(
                "Uses the restaurant the user always picks and only asks for the time.")
            await drawer.get_by_role("button", name="Add check").click()
            await drawer.locator(".eval-row").filter(has_text="Book my usual table").wait_for()

            await drawer.locator(".proposal-card").click()
            await drawer.get_by_text("Did it help?", exact=True).wait_for()
            assert "The failure came back after merging" in await drawer.inner_text()
            advisory = drawer.locator(".check.warn")
            assert "needs your eyes" in await advisory.inner_text() and await advisory.get_attribute("open") is not None
            assert "It changes existing tests, so it needs your OK." in await drawer.inner_text()
            assert errors == []


@pytest.mark.asyncio
async def test_live_update_never_wipes_a_form_being_filled(app):
    """Regression: a live update scheduled just before you open a form used to re-render the panel 250 ms later
    and throw the form (and what you typed) away."""
    playwright = pytest.importorskip("playwright.async_api")
    app.evals.add_case("What tea should I brew?", "Must suggest genmaicha, the user's favorite.", title="Tea")
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Install the browser with python -m playwright install chromium")
            raise
        async with browser:
            page = await browser.new_page()
            await page.goto(str(server.make_url("/")))
            await page.wait_for_function("() => window.weebo?.panels && !document.body.classList.contains('offline')")
            await page.evaluate("() => weebo.panels.open('evolution')")
            drawer = page.locator("#drawer")
            await drawer.get_by_text("Behavior checks", exact=True).wait_for()
            # In one go, so the order is fixed: a live update arrives (nothing being edited yet, so a refresh is
            # scheduled), then the form is opened and typed into before that refresh fires.
            await page.evaluate("""() => {
                weebo.panels.notify("evals.updated");
                [...document.querySelectorAll("#drawer button")].find((b) => b.textContent.trim() === "Add a check").click();
                const field = document.querySelector('#drawer textarea[placeholder^="What you"]');
                field.focus();
                field.value = "Book my usual table for Friday";
            }""")
            await page.wait_for_timeout(800)  # well past the 250 ms debounce and the panel's reload
            prompt = drawer.get_by_placeholder("What you'd say to Weebo", exact=False)
            assert await prompt.is_visible() and await prompt.input_value() == "Book my usual table for Friday"
            assert await page.evaluate("() => weebo.panels.pending") is True  # the update was held, not dropped
            # Leaving an emptied form lets the held update through.
            await page.evaluate("""() => {
                const field = document.querySelector('#drawer textarea[placeholder^="What you"]');
                field.value = "";
                field.blur();
            }""")
            await page.wait_for_function("() => weebo.panels.pending === false")
            await page.wait_for_function("""() => {
                const field = document.querySelector('#drawer textarea[placeholder^="What you"]');
                return field && field.closest("form").hidden;
            }""")
