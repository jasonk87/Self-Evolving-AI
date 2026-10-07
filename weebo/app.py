"""WeeboApp: wires every subsystem together and owns the process lifecycle."""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime
from typing import Any

from . import __version__, log, paths
from .agents.manager import AgentManager
from .brain.conversation import ConversationManager
from .brain.interactions import Interactions
from .brain.mind import Mind
from .codex.engine import CodexEngine
from .config import Settings
from .diagnostics import Diagnostics
from .events import EventBus
from .evolution import outcomes
from .evolution.engine import EvolutionEngine
from .evolution.evals import Evals
from .integrations import Integrations, gcalendar
from .memory.memory import Memory
from .proactive.heartbeat import Heartbeat
from .proactive.scheduler import Scheduler
from .remote import TailnetEndpoint
from .skills import Skills
from .store import Store

logger = log.get("app")

RESTART_EXIT_CODE = 75


class WeeboApp:
    def __init__(self) -> None:
        self.settings = Settings()
        self.store = Store()
        self.bus = EventBus()
        self.engine = CodexEngine(self.bus, self.settings.get("codex.binary"))
        self.diagnostics = Diagnostics(self.store)
        self.memory = Memory(self.store, self.bus)
        self.interactions = Interactions(self)
        self.conversations = ConversationManager(self)
        self.agents = AgentManager(self)
        self.scheduler = Scheduler(self)
        self.heartbeat = Heartbeat(self)
        self.mind = Mind(self)
        self.evolution = EvolutionEngine(self)
        self.evals = Evals(self)
        self.skills = Skills(self)
        self.integrations = Integrations(self)
        self.remote = TailnetEndpoint(self)
        # Where the server actually listens (CLI flags can override the settings); set by server.main.
        self.bind_host: str = str(self.settings.get("server.host"))
        self.bind_port: int = int(self.settings.get("server.port"))
        self.started_at = time.time()
        self.clients = 0
        self.exit_code = 0
        self.shutdown_event = asyncio.Event()
        self._restart_reason = ""

    # ------------------------------------------------------------------ lifecycle
    async def start(self, with_engine: bool = True, with_background: bool = True) -> None:
        from .brain import builtin_tools  # noqa: F401  (registers Weebo's tools)

        _load_dotenv()
        self.bus.bind_loop(asyncio.get_running_loop())
        self.conversations.recover_after_restart()
        self.scheduler.recover_after_restart()
        self.agents.recover_after_restart()
        if not os.environ.get("WEEBO_SKIP_LEGACY_IMPORT"):
            try:
                self.memory.import_legacy()
            except Exception as exc:
                logger.warning("Legacy memory import failed: %s", exc)
            try:  # copied now, so deleting the 1.x folder can't take the Google OAuth client file with it
                gcalendar.credentials_path(adopt=True)
            except OSError as exc:
                logger.warning("Couldn't copy the Weebo 1.x Google credentials: %s", exc)
        self._infer_user_name()
        self.conversations.desk()
        self.skills.seed_defaults()
        self.settings.on_change(self._on_settings_changed)
        self.bus.subscribe("engine.status", self._on_engine_status)
        if with_engine:
            await self.engine.start()
        if with_background:
            self.scheduler.start()
            self.heartbeat.start()
            self.evolution.start()
            self.memory.start_indexing(self.settings.get("memory.semantic"))
            asyncio.create_task(self.evals.detect_code_version(), name="code-version")
            asyncio.create_task(self.remote.start(), name="tailnet")
        self.store.journal("system", f"Weebo {__version__} started")
        self.agents._pump()  # Also handles an engine that was already ready at startup.

    async def stop(self) -> None:
        for component in (self.scheduler, self.heartbeat, self.evolution):
            await component.stop()
        await self.engine.stop()
        await asyncio.to_thread(self.memory.stop)  # joins the indexer thread; it reads the store we close next
        self.store.close()

    def _infer_user_name(self) -> None:
        """If the user hasn't set a name but Weebo already remembers it, use their first name."""
        if self.settings.get("user.name"):
            return
        for memory in self.store.search_memories("user name named called", limit=10):
            match = re.search(r"\b(?:user'?s (?:first )?name is|user is named|user is called)\s+([A-Z][a-zA-Z'-]{1,30})",
                              memory["text"], flags=re.IGNORECASE)
            if match and match.group(1)[0].isupper():
                self.settings.update({"user.name": match.group(1)})
                logger.info("Learned the user's name from memory: %s", match.group(1))
                return

    def request_restart(self, reason: str) -> None:
        """Exit with the supervisor's restart code (used after self-upgrades)."""
        self._restart_reason = reason
        self.bus.publish("system.restarting", {"reason": reason})
        self.store.journal("system", "Restarting", reason)
        self.exit_code = RESTART_EXIT_CODE
        asyncio.get_running_loop().call_later(1.5, self.shutdown_event.set)

    def _on_settings_changed(self, changed: dict[str, Any]) -> None:
        self.bus.publish_threadsafe("settings.updated", {"settings": self.settings.all(), "changed": changed})
        if "codex.binary" in changed:
            self.engine.binary_override = changed["codex.binary"]
            asyncio.get_running_loop().create_task(self.engine.restart())
        if "agents.max_parallel" in changed:
            self.agents._pump()
        if "memory.semantic" in changed:
            self.memory.start_indexing(changed["memory.semantic"])

    async def _on_engine_status(self, _topic: str, data: dict[str, Any]) -> None:
        if data.get("status") == "ready":
            self.agents._pump()
            await self.skills.sync_with_codex()

    # ------------------------------------------------------------------ helpers used across subsystems
    def notify(self, kind: str, title: str, body: str = "", data: dict | None = None) -> dict[str, Any]:
        note = self.store.add_notification(kind, title, body, data)
        self.bus.publish("notify", {"notification": note})
        return note

    def count_background_turn(self, label: str) -> None:
        key = "bg_turns"
        today = datetime.now().strftime("%Y-%m-%d")
        counts = self.store.kv_get(key, {}) or {}
        counts = {today: int(counts.get(today, 0)) + 1}
        self.store.kv_set(key, counts)
        logger.debug("background turn (%s): %s today", label, counts[today])

    def background_turns_today(self) -> int:
        counts = self.store.kv_get("bg_turns", {}) or {}
        return int(counts.get(datetime.now().strftime("%Y-%m-%d"), 0))

    def on_turn_finished(self, conv_id: str, turn: Any, status: str) -> None:
        mood = {"completed": "happy", "failed": "sad", "interrupted": "idle"}.get(status, "idle")
        self.bus.publish("weebo.mood", {"mood": mood, "conversation_id": conv_id})
        if turn.trigger in ("brief", "routine", "agent_report", "followup") and status == "completed":
            conv = self.store.get_conversation(conv_id)
            if conv and self.clients == 0 and turn.final_text:
                self.notify("weebo", conv.get("title") or "Weebo", turn.final_text[:300], {"conversation_id": conv_id})

    # ------------------------------------------------------------------ status
    def snapshot(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "uptime": time.time() - self.started_at,
            "engine": self.engine.snapshot(),
            "heartbeat": self.heartbeat.status(),
            "integrations": self.integrations.snapshot(),
            "agents": {"running": len(self.agents.runs), "queued": len(self.agents.queue)},
            "active_turns": self.conversations.active_turns(),
            "memory": self.memory.stats(),
            "evolution": {
                "mode": self.settings.get("evolution.mode"),
                "current": self.evolution.current,
                "ready": len(self.store.list_proposals(limit=50, statuses=("ready",))),
                "track_record": outcomes.stats(self),
            },
            "evals": self.evals.stats(),
            "pending_interactions": self.interactions.list(),
            "workspace": str(paths.workspace_dir()),
            "project_root": str(paths.PROJECT_ROOT),
        }

    def status_text(self) -> str:
        snap = self.snapshot()
        engine = snap["engine"]
        limits = engine.get("rateLimits") or {}
        primary = (limits.get("primary") or {}).get("usedPercent")
        account = engine.get("account") or {}
        record, evals = snap["evolution"]["track_record"], snap["evals"]
        lines = [
            f"Weebo {snap['version']}, up {int(snap['uptime'] // 60)} min.",
            f"Codex engine {engine.get('version')} is {engine['status']}"
            + (f", signed in as {account.get('email')} ({account.get('planType')})" if account.get("email") else ""),
            f"Plan usage this window: {primary}%" if primary is not None else "Plan usage: unknown",
            f"Autonomy: {self.settings.get('autonomy.level')}, proactive {'on' if self.settings.get('autonomy.proactive') else 'off'}; "
            f"background turns today {snap['heartbeat']['background_turns_today']}"
            f"/{self.settings.get('autonomy.max_background_turns_per_day')}.",
            f"Agents: {snap['agents']['running']} running, {snap['agents']['queued']} queued.",
            f"Memory: {snap['memory']['total']} memories (recall: {snap['memory']['recall']}). "
            f"Skills: {len(self.skills.list())}.",
            f"Self-evolution: mode {snap['evolution']['mode']}, {snap['evolution']['ready']} upgrade(s) waiting for review. "
            f"Track record: {record['merged']} merged, {record['held']} fixes held, {record['regressed']} came back, "
            f"{record['rolled_back']} rolled back.",
            f"Behavior checks: {evals['passing']}/{evals['checked']} passing ({evals['cases']} cases).",
            f"Integrations: {self.integrations.ready_count()} of {len(snap['integrations']['tools'])} set up on this machine.",
        ]
        if snap["heartbeat"]["busy"]:
            lines.append(f"Currently busy with: {snap['heartbeat']['busy']}.")
        return "\n".join(lines)


def _load_dotenv() -> None:
    """Expose the project's .env (Google search keys etc.) to the integrations without overriding real env."""
    env_file = paths.PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_file, override=False)
