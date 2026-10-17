"""Connectors: what Weebo can reach. The user's ChatGPT apps (through Codex's apps feature) and MCP servers,
including thin wrappers around the user's own programs (see ``weebo/connectors/template``).

Every connector goes through the same onboarding: register (from a manifest), test in isolation, approve (a hard
gate: the user sees each tool with its risk label, and tools that spend money or contact people need explicit
rules), assign to bots (a gated profile change), and monitor (failures and use feed back to the chief).
"""

from __future__ import annotations

import asyncio
import re
import shlex
import time
from typing import TYPE_CHECKING, Any

from .. import log
from ..store import new_id
from .mcp_client import MCPClient, MCPError

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("connectors")

RISKS = ("read", "act", "spend", "contact_people")
NEEDS_RULE = ("spend", "contact_people")


class ConnectorError(ValueError):
    pass


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:40] or "connector"


class Connectors:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self.config_failed = False  # set when the installed Codex refused per-thread MCP config

    # ------------------------------------------------------------------ reading
    def list(self, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        rows = self.app.store.query("SELECT * FROM connectors ORDER BY created_at")
        return [r for r in rows if not statuses or r["status"] in statuses]

    def get(self, ref: str | None) -> dict[str, Any] | None:
        if not ref:
            return None
        row = self.app.store.query_one("SELECT * FROM connectors WHERE id=?", (ref,))
        if row:
            return row
        ref_l = ref.strip().lower()
        for row in self.list():
            if ref_l in (row["name"].lower(), row["data"].get("key", ""), str(row["data"].get("app_id", "")).lower()):
                return row
        return None

    def require(self, ref: str) -> dict[str, Any]:
        row = self.get(ref)
        if not row:
            names = ", ".join(c["name"] for c in self.list()) or "none registered"
            raise ConnectorError(f"No connector {ref!r}. Connectors: {names}.")
        return row

    def resolve_ids(self, refs: list[str]) -> list[str]:
        """Connector references on a bot profile, as ids. Unknown ones are refused: register them first."""
        return [self.require(ref)["id"] for ref in refs]

    # ------------------------------------------------------------------ onboarding
    def register(self, manifest: dict[str, Any], actor: str) -> dict[str, Any]:
        if not isinstance(manifest, dict):
            raise ConnectorError("A connector manifest is a JSON object.")
        name = str(manifest.get("name") or "").strip()
        if not name or len(name) > 60:
            raise ConnectorError("A connector needs a name (up to 60 characters).")
        kind = manifest.get("kind") or ("app" if manifest.get("app_id") else "mcp")
        if kind not in ("app", "mcp"):
            raise ConnectorError("kind must be app or mcp")
        if self.get(name):
            raise ConnectorError(f"A connector called {name} is already registered.")
        data: dict[str, Any] = {"key": _key(name), "description": str(manifest.get("description") or "")[:600],
                                "registered_by": actor, "failures": 0, "uses": 0, "last_used": None}
        if kind == "mcp":
            transport = manifest.get("transport") or ("http" if manifest.get("url") else "stdio")
            if transport not in ("stdio", "http"):
                raise ConnectorError("transport must be stdio or http")
            command = manifest.get("command") or []
            if isinstance(command, str):
                command = shlex.split(command)
            if transport == "stdio" and (not isinstance(command, list) or not command
                                         or not all(isinstance(c, str) for c in command)):
                raise ConnectorError("A stdio connector needs a command (a list, or one command line).")
            url = str(manifest.get("url") or "")
            if transport == "http" and not url.startswith(("http://", "https://")):
                raise ConnectorError("An HTTP connector needs an http(s) url.")
            data.update(transport=transport, command=command, url=url, cwd=manifest.get("cwd") or None,
                        env_names=[str(n) for n in manifest.get("env_names") or []])
        else:
            data["app_id"] = str(manifest.get("app_id") or "").strip()
            data["install_url"] = str(manifest.get("install_url") or "")
            if not data["app_id"]:
                raise ConnectorError("An app connector needs its app_id (from the Capabilities page's app list).")
        tools = []
        for tool in manifest.get("tools") or []:
            if not isinstance(tool, dict) or not tool.get("name"):
                raise ConnectorError("Each tool needs a name and a risk label.")
            risk = tool.get("risk") or "act"
            if risk not in RISKS:
                raise ConnectorError(f"Tool {tool['name']}: risk must be one of {', '.join(RISKS)}.")
            tools.append({"name": str(tool["name"]), "description": str(tool.get("description") or "")[:300],
                          "risk": risk, "test_call": tool.get("test_call") if risk == "read" else None})
        data["tools"] = tools
        data["rules"] = {str(k): str(v)[:400] for k, v in (manifest.get("rules") or {}).items()}
        now = time.time()
        row = {"id": new_id("cn"), "name": name, "kind": kind, "status": "registered", "created_at": now,
               "updated_at": now, "data": data}
        self.app.store._insert("connectors", row)
        self.app.store.journal("connectors", f"Registered connector {name}", data={"actor": actor, "id": row["id"]})
        return self._changed(row["id"])

    async def test(self, ref: str) -> dict[str, Any]:
        """Run the connector in isolation: start it, list its tools, check they match what it declared, and make the
        declared read-only test calls. Nothing is approved by a test."""
        row = self.require(ref)
        self._set(row["id"], status="testing")
        data = row["data"]
        result: dict[str, Any] = {"at": time.time(), "ok": False, "calls": []}
        try:
            if row["kind"] == "app":
                apps = await self.apps()
                match = next((a for a in apps.get("apps", []) if a.get("id") == data["app_id"]), None)
                if not apps.get("available"):
                    raise ConnectorError(apps.get("reason") or "Codex can't list apps here.")
                if not match:
                    raise ConnectorError("That app isn't in your ChatGPT apps list.")
                if match.get("isAccessible") is False:
                    raise ConnectorError("The app is listed but not connected; connect it in ChatGPT first.")
                result.update(ok=True, found=[match.get("name") or data["app_id"]])
            else:
                async with MCPClient(data["transport"], command=data.get("command"), url=data.get("url", ""),
                                     cwd=data.get("cwd")) as client:
                    tools = await client.list_tools()
                    found = [t.get("name") for t in tools]
                    declared = [t["name"] for t in data["tools"]]
                    result["found"] = found
                    result["missing"] = [n for n in declared if n not in found]
                    result["undeclared"] = [n for n in found if n not in declared]
                    for tool in data["tools"]:
                        if tool.get("test_call") is not None and tool["name"] in found:
                            reply = await client.call_tool(tool["name"], (tool["test_call"] or {}).get("arguments"))
                            result["calls"].append({"tool": tool["name"], "ok": not reply.get("isError"),
                                                    "text": _content_text(reply)[:300]})
                    result["server"] = client.server_info
                    result["ok"] = (not result["missing"] and not result["undeclared"]
                                    and all(c["ok"] for c in result["calls"]))
                    if result["undeclared"]:
                        result["error"] = ("The server has tools the manifest doesn't label: "
                                           + ", ".join(result["undeclared"]) + ". Label each one's risk.")
                    elif result["missing"]:
                        result["error"] = "Declared tools the server doesn't have: " + ", ".join(result["missing"])
        except (MCPError, ConnectorError, OSError, asyncio.TimeoutError) as exc:
            result["error"] = str(exc) or type(exc).__name__
        status = "tested" if result["ok"] else "failed"
        row = self._set(row["id"], status=status, data={**self.require(row["id"])["data"], "test": result})
        self.app.store.journal("connectors", f"Tested {row['name']}: {'passed' if result['ok'] else 'failed'}",
                               result.get("error", ""), data={"id": row["id"]})
        self.sync_card(row, bring_forward=status == "tested")
        return row

    def approve(self, ref: str, actor: str, rules: dict[str, str] | None = None) -> dict[str, Any]:
        """The hard gate: only after a passing test, and tools that spend or contact people need explicit rules."""
        row = self.require(ref)
        if row["status"] not in ("tested", "disabled") or not row["data"].get("test", {}).get("ok"):
            raise ConnectorError(f"{row['name']} must pass its test first (it's {row['status']}).")
        merged = {**row["data"].get("rules", {}), **{str(k): str(v)[:400] for k, v in (rules or {}).items() if v}}
        missing = [t["name"] for t in row["data"]["tools"] if t["risk"] in NEEDS_RULE and not merged.get(t["name"])]
        if missing:
            raise ConnectorError("These tools can spend money or contact people, so each needs a rule first (for "
                                 "example \"may call businesses to book appointments; never personal contacts\"): "
                                 + ", ".join(missing))
        row = self._set(row["id"], status="approved",
                        data={**row["data"], "rules": merged, "approved_by": actor, "approved_at": time.time()})
        self.app.store.journal("connectors", f"Approved connector {row['name']}", data={"actor": actor, "id": row["id"]})
        self.sync_card(row)
        return row

    def disable(self, ref: str, actor: str) -> dict[str, Any]:
        row = self._set(self.require(ref)["id"], status="disabled")
        self.app.store.journal("connectors", f"Disabled connector {row['name']}", data={"actor": actor, "id": row["id"]})
        self.sync_card(row)
        return row

    def _set(self, connector_id: str, **values: Any) -> dict[str, Any]:
        values["updated_at"] = time.time()
        self.app.store._update("connectors", "id", connector_id, values)
        return self._changed(connector_id)

    def _changed(self, connector_id: str) -> dict[str, Any]:
        row = self.app.store.query_one("SELECT * FROM connectors WHERE id=?", (connector_id,))
        self.app.bus.publish("connector.updated", {"connector": row})
        return row  # type: ignore[return-value]

    def sync_card(self, row: dict[str, Any], bring_forward: bool = False) -> None:
        """A tested connector waits for the user's approval on a card in the main chat."""
        store = self.app.store
        message_id = f"connector:{row['id']}"
        existing = store.get_message(message_id)
        if not existing and row["status"] != "tested":
            return
        main = self.app.conversations.main_id()
        message = store.upsert_message(message_id, existing["conversation_id"] if existing else main, "event",
                                       "connector", row["name"], {"connector": row}, None,
                                       "pending" if row["status"] == "tested" else row["status"])
        if bring_forward:
            message = store.bring_forward(message_id) or message
        self.app.bus.publish("conv.message", {"conversation_id": message["conversation_id"], "message": message})

    # ------------------------------------------------------------------ scope, risk, monitoring
    def _match_server(self, server: str) -> dict[str, Any] | None:
        server_l = (server or "").strip().lower()
        if not server_l:
            return None
        for row in self.list():
            names = {row["name"].lower(), row["data"].get("key", ""), str(row["data"].get("app_id", "")).lower()}
            if server_l in names:
                return row
        return None

    def risk(self, server: str, tool: str) -> str:
        row = self._match_server(server)
        if not row:
            return "act"
        for item in row["data"].get("tools", []):
            if item["name"] == tool:
                return item["risk"]
        return "act"

    def in_scope(self, bot: dict[str, Any], server: str, tool: str = "") -> bool:
        """A bot may use only approved connectors assigned to it. A server Weebo doesn't know is out of scope. ChatGPT
        apps all arrive through Codex's one apps server, so there the tool name tells which app it is."""
        assigned = [self.get(cid) for cid in bot["data"].get("connectors") or []]
        approved = [row for row in assigned if row and row["status"] == "approved"]
        if server == "codex_apps":
            tool_l = (tool or "").lower()
            return any(row["kind"] == "app" and any(n and n in tool_l for n in (
                row["data"].get("key", ""), str(row["data"].get("app_id", "")).lower(),
                row["name"].lower().replace(" ", "_"))) for row in approved)
        row = self._match_server(server)
        return bool(row and row in approved and (not tool or any(t["name"] == tool for t in row["data"].get("tools", []))))

    def thread_config(self, bot: dict[str, Any]) -> dict[str, Any]:
        """Extra thread/start params giving a bot its approved MCP connectors, when the installed Codex accepts
        per-thread config (checked against its schema). Otherwise empty: scope is enforced in Weebo instead."""
        features = getattr(self.app.engine, "features", None)
        if self.config_failed or not getattr(features, "thread_config", False):
            return {}
        config: dict[str, Any] = {}
        for connector_id in bot["data"].get("connectors") or []:
            row = self.get(connector_id)
            if not row or row["status"] != "approved" or row["kind"] != "mcp":
                continue
            key, data = row["data"]["key"], row["data"]
            if data["transport"] == "stdio":
                config[f"mcp_servers.{key}.command"] = data["command"][0]
                config[f"mcp_servers.{key}.args"] = data["command"][1:]
            else:
                config[f"mcp_servers.{key}.url"] = data["url"]
        return {"config": config} if config else {}

    def record_use(self, server: str, ok: bool, error: str = "") -> None:
        row = self._match_server(server)
        if not row:
            return
        data = dict(row["data"])
        data["uses"] = int(data.get("uses") or 0) + 1
        data["last_used"] = time.time()
        data["failures"] = 0 if ok else int(data.get("failures") or 0) + 1
        if not ok:
            data["last_error"] = error[:300]
        limit = int(self.app.settings.get("connectors.max_failures"))
        flag = not ok and data["failures"] >= limit and not data.get("flagged")
        if ok:
            data["flagged"] = False
        if flag:
            data["flagged"] = True
        self._set(row["id"], data=data)
        if flag:  # keeps failing: the chief hears about it, and it shows on the Capabilities page
            note = (f"Connector {row['name']} has failed {data['failures']} times in a row"
                    + (f" ({error[:160]})" if error else "") + ". Check it on the Capabilities page.")
            self.app.store.journal("connectors", f"{row['name']} keeps failing", note, data={"id": row["id"]})
            self.app.conversations.add_context(self.app.conversations.main_id(), note)

    # ------------------------------------------------------------------ ChatGPT apps through Codex
    async def apps(self) -> dict[str, Any]:
        engine = self.app.engine
        if not getattr(getattr(engine, "features", None), "apps", False) or not hasattr(engine, "list_apps"):
            return {"available": False, "apps": [],
                    "reason": "The installed Codex doesn't offer ChatGPT apps (app/list), or isn't signed in with "
                              "ChatGPT."}
        try:
            return {"available": True, "apps": await engine.list_apps()}
        except Exception as exc:  # degrade: the page says why
            return {"available": False, "apps": [], "reason": f"Couldn't list apps: {exc}"}


def _content_text(reply: dict[str, Any]) -> str:
    return "\n".join(c.get("text", "") for c in reply.get("content") or [] if isinstance(c, dict))
