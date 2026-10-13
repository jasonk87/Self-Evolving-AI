"""Keep Weebo's Codex threads out of the user's way in the Codex app.

Codex lists every thread Weebo saves next to the user's own Codex work. Weebo keeps one there, the main chat's,
and archives the rest once they're done: a finished agent's thread (brought back when follow-up work continues
it), a main-chat thread that was replaced, older chats' threads. Archived threads stay in Codex's archive and can
be restored there; nothing is deleted.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .. import log
from ..codex.rpc import EngineClosed, RpcError

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("threads")

NAME_PREFIXES = ("Weebo · ", "Weebo agent · ")  # the names Weebo gives the threads it saves
SWEEP_KEY = "codex.thread_sweep"
SWEEP_VERSION = 1  # raise to tidy up once more on the next start
SWEEP_PAGES = 20  # of 100 threads each: how far back the sweep looks through the Codex thread list


class ThreadKeeper:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self._archiving: dict[str, asyncio.Task] = {}
        self._backlog: dict[str, str] = {}  # thread id -> name: to put away once the engine is ready
        self._sweep: asyncio.Task | None = None

    def in_use(self, thread_id: str) -> bool:
        """The main chat's thread, or one an agent is working (or about to work) in."""
        store = self.app.store
        main = store.find_conversation_by_kind("desk")
        if main and main.get("thread_id") == thread_id:
            return True
        return store.query_one("SELECT 1 AS busy FROM tasks WHERE thread_id=? AND status IN ('queued', 'running')",
                               (thread_id,)) is not None

    def put_away(self, thread_id: str, name: str = "", retry: bool = True) -> asyncio.Task | None:
        """Name a finished thread (so it reads well in Codex's archive) and archive it, unless by then something is
        using it again. ``name`` is only for threads that took a turn: naming one saves it to disk."""
        if not thread_id:
            return None
        previous = self._archiving.get(thread_id)

        async def run() -> None:
            if previous:
                await asyncio.gather(previous, return_exceptions=True)
            if self.in_use(thread_id):  # continued meanwhile
                return
            engine = self.app.engine
            if engine.status != "ready":
                if retry:
                    self._backlog[thread_id] = name
                return
            if name:
                await engine.set_thread_name(thread_id, name)
            if await engine.archive_thread(thread_id):
                self._backlog.pop(thread_id, None)
            elif retry and engine.status != "ready":  # the engine went down mid-way: try again when it's back
                self._backlog[thread_id] = name

        task = asyncio.get_running_loop().create_task(run())
        self._archiving[thread_id] = task
        task.add_done_callback(lambda done, key=thread_id: self._forget(key, done))
        return task

    def _forget(self, thread_id: str, task: asyncio.Task) -> None:
        if self._archiving.get(thread_id) is task:
            del self._archiving[thread_id]

    def later(self, thread_id: str, name: str = "") -> None:
        """Put away once the engine is ready (an agent cut off by a restart)."""
        if thread_id:
            self._backlog[thread_id] = name

    async def settle(self, thread_id: str) -> None:
        """Wait for an archive of this thread that is already under way."""
        pending = self._archiving.get(thread_id)
        if pending:
            await asyncio.gather(pending, return_exceptions=True)

    async def take_back(self, thread_id: str) -> None:
        """Before resuming a thread that may have been put away: un-archive it (Codex won't resume an archived
        thread). A thread that isn't archived just stays as it is."""
        await self.settle(thread_id)
        self._backlog.pop(thread_id, None)
        await self.app.engine.unarchive_thread(thread_id)

    def on_engine_ready(self) -> None:
        backlog, self._backlog = self._backlog, {}
        for thread_id, name in backlog.items():
            self.put_away(thread_id, name)
        if self._sweep is None and int(self.app.store.kv_get(SWEEP_KEY, 0) or 0) < SWEEP_VERSION:
            self._sweep = asyncio.get_running_loop().create_task(self._sweep_once())

    async def _sweep_once(self) -> None:
        """Once: archive the threads Weebo left in the Codex app before it put its threads away by itself."""
        store, engine = self.app.store, self.app.engine
        found: dict[str, None] = {}
        for row in store.query("SELECT thread_id FROM tasks WHERE thread_id IS NOT NULL AND thread_id != '' "
                               "AND status NOT IN ('queued', 'running') ORDER BY created_at"):
            found[row["thread_id"]] = None
        for row in store.query("SELECT thread_id FROM conversations WHERE thread_id IS NOT NULL AND thread_id != '' "
                               "AND kind != 'desk'"):
            found[row["thread_id"]] = None  # older chats are kept to read in Weebo, which has their messages
        cursor = None
        try:  # threads Weebo named but no longer tracks, such as main-chat threads it replaced
            for _ in range(SWEEP_PAGES):
                page = await engine.list_threads(limit=100, cursor=cursor, searchTerm="Weebo")
                for thread in page.get("data") or []:
                    if str(thread.get("name") or "").startswith(NAME_PREFIXES) and thread.get("id"):
                        found[thread["id"]] = None
                cursor = page.get("nextCursor")
                if not cursor:
                    break
        except (RpcError, EngineClosed, asyncio.TimeoutError) as exc:
            logger.info("Could not list Codex threads to tidy up: %s", exc)
        for thread_id in found:
            if engine.status != "ready":
                return  # not marked done: the next start tries again
            task = self.put_away(thread_id, retry=False)
            if task:
                await task
        store.kv_set(SWEEP_KEY, SWEEP_VERSION)
        if found:
            logger.info("Tidied up %d of Weebo's older Codex threads", len(found))
