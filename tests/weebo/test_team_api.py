"""The team, policy, connectors and capabilities over the authenticated REST API (the user's own clicks)."""

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from weebo.server.app import TOKEN_KEY, create_app

pytestmark = pytest.mark.asyncio
SERVER = str(Path(__file__).with_name("fake_mcp_server.py"))


@pytest_asyncio.fixture
async def client(app):
    web_app = create_app(app)
    client = TestClient(TestServer(web_app))
    await client.start_server()
    client.h = {"Host": "127.0.0.1", "X-Weebo-Token": web_app[TOKEN_KEY]}
    yield client
    await client.close()


async def post(client, path, body=None):
    resp = await client.post(path, json=body or {}, headers=client.h)
    return resp.status, await resp.json()


async def test_bot_card_buttons_and_profile_edits(app, client):
    bot = app.bots.propose({"name": "Mail", "job": "Email.", "tier": "fast", "why": "x"})
    assert (await client.get("/api/bots", headers=client.h)).status == 200
    status, data = await post(client, f"/api/bots/{bot['id']}/approve")
    assert status == 200 and data["bot"]["status"] == "active" and data["bot"]["conversation_id"]
    resp = await client.patch(f"/api/bots/{bot['id']}", json={"tier": "capable", "tools": ["recall"]}, headers=client.h)
    assert resp.status == 200 and (await resp.json())["bot"]["data"]["tier"] == "capable"  # the user's own edit
    status, data = await post(client, f"/api/bots/{bot['id']}/pause")
    assert data["bot"]["status"] == "paused"
    detail = await (await client.get(f"/api/bots/{bot['id']}", headers=client.h)).json()
    assert detail["bot"]["name"] == "Mail" and "jobs" in detail and "usage" in detail
    status, data = await post(client, f"/api/bots/{bot['id']}/archive")
    assert data["bot"]["status"] == "archived"
    status, data = await post(client, "/api/bots/nobody/approve")
    assert status == 400


async def test_needs_you_and_rules_from_the_ui(app, client, fake_engine):
    bot = app.bots.approve(app.bots.propose({"name": "Coder", "job": "Code.", "tier": "fast", "why": "x"})["id"])
    params = {"threadId": "x", "turnId": "t", "command": "make deploy", "cwd": "/tmp"}
    await app.interactions.ask("item/commandExecution/requestApproval", params, conversation_id=bot["conversation_id"])
    policy = await (await client.get("/api/policy", headers=client.h)).json()
    item = policy["needs_you"][0]
    status, data = await post(client, f"/api/needs-you/{item['id']}/always")
    assert status == 200 and data["item"]["status"] == "approved"
    status, data = await post(client, "/api/policy/report", {"scope": "Coder", "match": "nightly", "bucket": "log"})
    assert status == 200 and data["rule"]["bucket"] == "log"
    rules = (await (await client.get("/api/policy", headers=client.h)).json())
    assert rules["report_rules"] and rules["standing_rules"]
    resp = await client.delete(f"/api/policy/report/{data['rule']['id']}", headers=client.h)
    assert (await resp.json())["ok"]


async def test_connector_onboarding_from_the_ui(app, client):
    status, data = await post(client, "/api/connectors", {
        "name": "Lookup", "transport": "stdio", "command": [sys.executable, SERVER],
        "tools": [{"name": "lookup", "risk": "read"}, {"name": "place_call", "risk": "contact_people"}]})
    connector = data["connector"]
    status, data = await post(client, f"/api/connectors/{connector['id']}/test")
    assert data["connector"]["status"] == "tested"
    status, data = await post(client, f"/api/connectors/{connector['id']}/approve")
    assert status == 400 and "place_call" in data["error"]
    status, data = await post(client, f"/api/connectors/{connector['id']}/approve",
                              {"rules": {"place_call": "Businesses only."}})
    assert data["connector"]["status"] == "approved"
    listing = await (await client.get("/api/connectors", headers=client.h)).json()
    assert listing["connectors"][0]["name"] == "Lookup" and listing["apps"]["available"] is False
    caps = await (await client.get("/api/capabilities", headers=client.h)).json()
    assert caps["connectors"][0]["risk"] == "contact_people"


async def test_bootstrap_carries_the_team(app, client):
    app.bots.propose({"name": "Research", "job": "Look things up.", "tier": "standard", "why": "x"})
    boot = await (await client.get("/api/bootstrap", headers=client.h)).json()
    assert boot["bots"][0]["name"] == "Research"
