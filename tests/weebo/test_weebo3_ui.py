"""Browser tests for Weebo 3: the final tabs, bots and their chats, the "needs you" card, Ideas, and the honest
activity display (priority, fall-back, "+N more", waiting on you, idle, reduced motion)."""

import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestServer

from weebo.brain.activity_registry import BACKGROUND, Entry
from weebo.server.app import create_app

from .browser_support import launch_chromium

pytestmark = pytest.mark.asyncio
TZ = "America/Chicago"


async def test_publication_gate_and_per_action_card_in_browser(app, fake_engine, monkeypatch):
    from .test_release_safety import add_fact, confirm
    from .test_connectors_growth import new_bot
    playwright = pytest.importorskip("playwright.async_api")
    app.settings.update({"autonomy.quiet_hours_start": "00:00", "autonomy.quiet_hours_end": "00:00"})
    add_fact(app)
    gate, entered = asyncio.Event(), asyncio.Event()

    async def review(text, **kwargs):
        entered.set()
        await gate.wait()
        return [{"claim": "Room A", "fact": "Room B"}]

    monkeypatch.setattr(app.factcheck, "second_pass", review)
    errors = []
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        browser, page = await open_page(pw, server, errors)
        async with browser:
            main = app.conversations.main_id()
            await app.conversations.run_event(main, "brief", "", trigger="brief")
            turn = fake_engine.turns[-1]
            await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Design review is in Room A.")
            await asyncio.wait_for(entered.wait(), 2)
            assert "Room A" not in await page.locator("body").inner_text()
            await page.reload()
            await page.wait_for_function("() => window.weebo?.chat?.convId")
            assert "Room A" not in await page.locator("body").inner_text()
            gate.set()
            await page.get_by_text("Fact-check: Room A, but Room B.", exact=False).first.wait_for()
            bot = new_bot(app, name="Messenger")
            await app.approvals.handle(bot, "weebo/confirm", confirm())
            card = page.locator(".needs-you-card")
            await card.get_by_role("button", name="Allow once", exact=True).wait_for()
            assert await card.get_by_role("button", name="Always allow", exact=True).count() == 0
            await card.get_by_text("Action details", exact=True).click()
            assert "+15551234567" in await card.inner_text()
            assert not errors


async def until(predicate, page, tries=100):
    for _ in range(tries):
        if predicate():
            return True
        await page.wait_for_timeout(40)
    return predicate()


async def open_page(pw, server, errors, **context):
    browser = await launch_chromium(pw)
    page = await browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, **context)
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.goto(str(server.make_url("/")))
    await page.wait_for_function("() => window.weebo?.chat?.convId && !document.body.classList.contains('offline')")
    return browser, page


async def test_bots_cards_chats_and_needs_you(app, fake_engine):
    playwright = pytest.importorskip("playwright.async_api")
    proposed = app.bots.propose({"name": "Mail", "job": "Keeps an eye on email.", "tier": "fast", "why": "Cheap reading.",
                                 "autonomy": ["read"]})
    errors = []
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        browser, page = await open_page(pw, server, errors)
        async with browser:
            tabbar = page.locator("#tabbar")
            for name in ("Chat", "Ideas", "Bots", "Evolution", "More"):
                assert await tabbar.get_by_role("button", name=name).first.is_visible()

            # The proposal's card in the chat: one tap is the user's yes.
            card = page.locator(f'[data-id="bot:{proposed["id"]}"]')
            await card.get_by_text("New bot: Mail").wait_for()
            await card.get_by_role("button", name="Approve").click()
            assert await until(lambda: app.bots.get(proposed["id"])["status"] == "active", page)
            await card.get_by_text("Approved: it's on the team").wait_for()

            # The Bots tab: its strip, its profile, and its own chat you can talk in.
            await tabbar.get_by_role("button", name="Bots").click()
            await page.locator("#drawer .bot-chip").filter(has_text="Mail").click()
            await page.locator("#drawer .task-title").get_by_text("Mail").wait_for()
            assert await page.locator("#drawer form textarea").first.input_value() == "Keeps an eye on email."
            await page.locator("#drawer").get_by_role("button", name="Open its chat").click()
            bot = app.bots.get(proposed["id"])
            await page.wait_for_function("id => weebo.chat.convId === id", arg=bot["conversation_id"])
            await page.locator(".bot-chat-bar").get_by_text("Mail").wait_for()
            assert await page.locator(".composer").is_visible()
            await page.locator(".composer textarea").fill("What's new in my inbox?")
            await page.locator(".composer textarea").press("Enter")
            assert await until(lambda: any(t.get("thread_id") == app.store.get_conversation(bot["conversation_id"]).get("thread_id")
                                           for t in fake_engine.turns), page)
            bot_thread = app.store.get_conversation(bot["conversation_id"])["thread_id"]
            await fake_engine.finish_turn(bot_thread, fake_engine.turns[-1]["turn_id"], "Two new threads.")
            await page.locator(".bot-chat-bar").get_by_role("button", name="Back to Weebo").click()
            await page.wait_for_function("id => weebo.chat.convId === id", arg=app.conversations.main_id())

            # What the team needs from the user arrives on one card; "Always allow" remembers it.
            await app.interactions.ask("item/commandExecution/requestApproval",
                                       {"threadId": "x", "turnId": "t", "command": "make deploy", "cwd": "/tmp"},
                                       conversation_id=bot["conversation_id"])
            needs = page.locator('[data-id="needs-you"]')
            await needs.get_by_text("Mail wants to run `make deploy`").wait_for()
            await needs.get_by_role("button", name="Always allow").click()
            assert await until(lambda: not app.approvals.open_items(), page)
            assert app.approvals.rules(bot["id"])[0]["target"] == "make deploy"
            await page.wait_for_function("() => !document.querySelector('[data-id=\"needs-you\"] .ny-item')")
            assert errors == []


def tomorrow_at(hour, minute=0):
    day = datetime.now(ZoneInfo(TZ)).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(TZ)).timestamp()


async def test_ideas_tab_discuss_opens_the_chat(app, fake_engine):
    playwright = pytest.importorskip("playwright.async_api")
    app.settings.update({"user.timezone": TZ})
    app.daysheet.weather = None
    for ext, title, start, end, where in (("a", "Design review", 10, 11, "Office"), ("b", "Dentist", 11, 12, "Clinic")):
        app.facts.repository.upsert("events", {"external_id": ext, "title": title, "start": tomorrow_at(start),
                                               "end": tomorrow_at(end), "tz": TZ, "location": where},
                                    source="test:calendar", ttl_seconds=3600)
    await app.ideas.generate()
    errors = []
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        browser, page = await open_page(pw, server, errors)
        async with browser:
            await page.locator("#tabbar").get_by_role("button", name="Ideas").click()
            idea = page.locator("#drawer .idea-card").filter(has_text="Getting to Dentist")
            await idea.wait_for()
            assert await idea.locator(".chip").filter(has_text="Dentist").count()  # evidence chips
            await page.locator("#drawer").get_by_text("travel: unknown, 0 min gap").first.wait_for()  # never guessed
            await idea.get_by_role("button", name="Discuss").click()
            main = app.conversations.main_id()
            await page.wait_for_function("id => weebo.chat.convId === id && !document.body.classList.contains('drawer-open')", arg=main)
            await page.get_by_text("Let's talk about this: Getting to Dentist").wait_for()
            assert "offer_replies" in fake_engine.turns[-1]["context"]
            assert errors == []


async def test_activity_slot_is_honest(app, fake_engine):
    playwright = pytest.importorskip("playwright.async_api")
    app.settings.update({"autonomy.quiet_hours_start": "00:00", "autonomy.quiet_hours_end": "00:00"})  # no quiet hours: idle reads as idle at any time of day
    bot = app.bots.approve(app.bots.propose({"name": "Coder", "job": "Code.", "tier": "fast", "why": "x"})["id"])
    errors = []
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        browser, page = await open_page(pw, server, errors)
        async with browser:
            slot = page.locator(".now-showing")
            await slot.get_by_text("Resting · nothing running").wait_for()  # idle is clearly idle

            # Background work shows; the reply being written for you takes the front; then it falls back.
            await app.conversations.run_event(bot["conversation_id"], "routine", "", trigger="routine")
            await slot.get_by_text("Coder: running a routine").wait_for()
            main = app.conversations.main_id()
            await app.conversations.send(main, "hello")
            await slot.get_by_text("Writing you a reply").wait_for()
            more = page.locator(".now-more")
            await more.get_by_text("+1 more").wait_for()
            await more.click()
            await page.locator(".activity-list").get_by_text("Coder: running a routine").wait_for()
            thread = app.store.get_conversation(main)["thread_id"]
            await fake_engine.finish_turn(thread, fake_engine.turns[-1]["turn_id"], "Hi!")
            await slot.get_by_text("Coder: running a routine").wait_for()

            # Waiting on you is prominent, and the avatar holds up its note.
            app.bus.publish("interaction.pending", {"interaction": {"id": "ask9", "kind": "command",
                                                                     "command": "make deploy", "conversation_id": main}})
            await page.wait_for_function("() => document.querySelector('.now-showing').dataset.state === 'waiting'")
            await page.wait_for_function("() => document.querySelector('.stage .weebo').classList.contains('mood-waiting')")
            app.bus.publish("interaction.resolved", {"id": "ask9"})

            # Quiet work says so instead of pretending.
            app.activity.put(Entry("system:audit", "system", "Weebo", BACKGROUND - 1, "reviewing", "Running the tests"))
            entry = app.activity.entries["system:audit"]
            entry.last_event_at = entry.started_at = time.time() - 600
            app.activity._publish(force=True)
            await slot.get_by_text("Still running the tests").wait_for()
            assert errors == []


async def test_reduced_motion_is_calm(app):
    playwright = pytest.importorskip("playwright.async_api")
    app.activity.put(Entry("system:x", "system", "Weebo", BACKGROUND, "coding", "Building an upgrade to myself"))
    errors = []
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        browser, page = await open_page(pw, server, errors, reduced_motion="reduce")
        async with browser:
            await page.wait_for_function("() => document.querySelector('.stage .weebo').classList.contains('mood-coding')")
            duration = await page.evaluate("() => getComputedStyle(document.querySelector('.stage .weebo .wb-keys rect')).animationDuration")
            assert duration in ("1e-05s", "0.00001s")  # the prop stays, the motion doesn't
            assert await page.locator(".stage .weebo .wb-laptop").evaluate("e => getComputedStyle(e).opacity") == "1"
            app.settings.update({"ui.reduce_motion": True})
            await page.wait_for_function("() => document.body.classList.contains('reduce-motion')")
            assert errors == []


async def test_idle_activity_updates_when_a_bot_needs_approval(app):
    from playwright import async_api as playwright
    app.settings.update({"autonomy.quiet_hours_start": "00:00", "autonomy.quiet_hours_end": "00:00"})  # no quiet hours: idle reads as idle at any time of day
    bot = app.bots.approve(app.bots.propose({"name": "Scout", "job": "Review examples.", "tier": "fast"})["id"])
    errors = []
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        browser, page = await open_page(pw, server, errors)
        async with browser:
            slot = page.locator(".now-showing")
            await slot.get_by_text("Resting · nothing running").wait_for()
            await app.interactions.ask("item/commandExecution/requestApproval", {"command": "make deploy"},
                                       conversation_id=bot["conversation_id"])
            await slot.get_by_text("1 thing from the team needs you").wait_for()
            assert await slot.get_attribute("data-state") == "waiting"
            await page.locator('[data-id="needs-you"]').get_by_role("button", name="Deny").click()
            await slot.get_by_text("Resting · nothing running").wait_for()
            assert not app.approvals.open_items() and errors == []


async def test_idea_draft_waits_for_completion_and_offers_retry_after_failure(app, fake_engine):
    from playwright import async_api as playwright
    from .test_weebo3_bugfixes import idea
    row = idea(app, "draft_email")
    errors = []
    async with TestServer(create_app(app)) as server, playwright.async_playwright() as pw:
        browser, page = await open_page(pw, server, errors)
        async with browser:
            await page.locator("#tabbar").get_by_role("button", name="Ideas").click()
            card = page.locator(f'[data-idea="{row["id"]}"]')
            await card.get_by_role("button", name="Do it:").click()
            await card.get_by_text("Draft requested; waiting for completion.").wait_for()
            assert await card.get_by_role("button", name="Do it:").count() == 0
            turn = fake_engine.turns[-1]
            await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], status="failed")
            await card.get_by_text("Draft did not complete. You can retry it.").wait_for()
            await card.get_by_role("button", name="Do it:").click()
            await card.get_by_text("Draft requested; waiting for completion.").wait_for()
            turn = fake_engine.turns[-1]
            await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Here is the example draft.")
            await card.wait_for(state="detached")
            await page.locator("#tabbar").get_by_role("button", name="Chat", exact=True).click()
            await page.get_by_text("Here is the example draft.", exact=True).wait_for()
            assert app.ideas.get(row["id"])["status"] == "done" and not errors
