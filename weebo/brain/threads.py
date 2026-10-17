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
ORIGINATOR = "weebo"  # the client name Weebo gives Codex, recorded on every thread it starts
PENDING_KEY = "codex.put_away_pending"  # threads still to put away, kept across restarts
SWEEP_KEY = "codex.thread_sweep"
SWEEP_TRIES_KEY = "codex.thread_sweep_tries"
SWEEP_CURSOR_KEY = "codex.thread_sweep_cursor"
SWEEP_VERSION = 2  # retry older cleanup failures that weren't previously saved in the pending list
SWEEP_TRIES = 3  # starts on which the Codex thread list may fail before the sweep makes do with Weebo's own records
SWEEP_PAGES = 20  # of 100 threads each: how far back the sweep looks through the Codex thread list


class ThreadKeeper:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self._archiving: dict[str, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        self._closing = False
        # Thread id -> name: to put away, saved so a restart (or Codex being down) doesn't lose any.
        pending = app.store.kv_get(PENDING_KEY, {})
        self._pending: dict[str, str] = dict(pending) if isinstance(pending, dict) else {}
        self._sweep: asyncio.Task | None = None

    def _note(self, thread_id: str, name: str | None) -> None:
        """Remember (a name) or forget (None) a thread still to put away."""
        if name is None:
            if self._pending.pop(thread_id, None) is None:
                return
        else:
            self._pending[thread_id] = name
        self.app.store.kv_set(PENDING_KEY, self._pending)

    def in_use(self, thread_id: str) -> bool:
        """The main chat's thread, or one an agent is working (or about to work) in."""
        store = self.app.store
        main = store.find_conversation_by_kind("desk")
        if main and main.get("thread_id") == thread_id:
            return True
        for conv_id, session in self.app.conversations.sessions.items():
            if session.turn:
                conv = store.get_conversation(conv_id)
                if conv and conv.get("thread_id") == thread_id:
                    return True
        return store.query_one("SELECT 1 AS busy FROM tasks WHERE thread_id=? AND "
                               "(status IN ('queued', 'running') OR json_extract(meta, '$.unsettled_thread') = 1)",
                               (thread_id,)) is not None

    def put_away(self, thread_id: str, name: str = "", retry: bool = True) -> asyncio.Task | None:
        """Name a finished thread (so it reads well in Codex's archive) and archive it, unless by then something is
        using it again. ``name`` is only for threads that took a turn: naming one saves it to disk. With ``retry``
        it is remembered until it's done, across restarts and Codex being down."""
        if not thread_id or self._closing:
            return None
        previous = self._archiving.get(thread_id)
        if retry:
            self._note(thread_id, name or self._pending.get(thread_id, ""))

        async def run() -> None:
            if previous:
                await asyncio.gather(previous, return_exceptions=True)
            if self.in_use(thread_id):  # continued meanwhile: whoever finishes with it puts it away
                self._note(thread_id, None)
                return
            engine = self.app.engine
            if engine.status != "ready":
                return  # still noted: done once Codex is ready
            if name:
                await engine.set_thread_name(thread_id, name)
            if not await engine.thread_idle(thread_id):
                return  # it may be running in another client; keep the durable retry
            if self.in_use(thread_id):  # naming awaits Codex; a continuation may have claimed it meanwhile
                self._note(thread_id, None)
                return
            archived = await engine.archive_thread(thread_id)
            if archived is not None or not retry:  # done, or refused for good (gone, already archived)
                self._note(thread_id, None)

        task = asyncio.get_running_loop().create_task(run())
        self._archiving[thread_id] = task
        self._tasks.add(task)
        task.add_done_callback(lambda done, key=thread_id: self._forget(key, done))
        return task

    def _forget(self, thread_id: str, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if self._archiving.get(thread_id) is task:
            del self._archiving[thread_id]
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Could not archive thread %s: %r", thread_id, task.exception())

    def later(self, thread_id: str, name: str = "") -> None:
        """Put away once the engine is ready (an agent cut off by a restart)."""
        if thread_id and not self._closing:
            self._note(thread_id, name)

    async def settle(self, thread_id: str) -> None:
        """Wait for an archive of this thread that is already under way."""
        pending = self._archiving.get(thread_id)
        if pending:
            await asyncio.gather(pending, return_exceptions=True)

    async def take_back(self, thread_id: str) -> None:
        """Before resuming a thread that may have been put away: un-archive it (Codex won't resume an archived
        thread). A thread that isn't archived just stays as it is."""
        await self.settle(thread_id)
        self._note(thread_id, None)
        await self.app.engine.unarchive_thread(thread_id)

    def on_engine_ready(self) -> None:
        if self._closing:
            return
        for thread_id, name in list(self._pending.items()):
            if thread_id not in self._archiving:
                self.put_away(thread_id, name)
        if self._sweep is None and int(self.app.store.kv_get(SWEEP_KEY, 0) or 0) < SWEEP_VERSION:
            self._sweep = asyncio.get_running_loop().create_task(self._sweep_once())

    async def stop(self) -> None:
        """Settle all archive work before its engine and store close; durable retries stay queued."""
        self._closing = True
        tasks = set(self._tasks)
        if self._sweep:
            tasks.add(self._sweep)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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
        cursor, listed = store.kv_get(SWEEP_CURSOR_KEY), True
        try:  # threads Weebo named but no longer tracks, such as main-chat threads it replaced
            for _ in range(SWEEP_PAGES):
                page = await engine.list_threads(limit=100, cursor=cursor, searchTerm="Weebo")
                for thread in page.get("data") or []:
                    if self._weebos(thread):
                        found[thread["id"]] = None
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            if cursor:
                listed = False  # a page limit is not a completed sweep
        except (RpcError, EngineClosed, asyncio.TimeoutError) as exc:
            listed = False
            store.kv_set(SWEEP_CURSOR_KEY, None)  # a saved server cursor may have expired; restart the list next time
            logger.warning("Could not list Codex threads to tidy up (%s); trying again on the next start", exc)
        else:
            # Do not start at page one forever when a large list contains threads we must leave alone.
            # Save only after their ids are durably queued below, so an interrupted sweep cannot skip work.
            for thread_id in found:
                self.later(thread_id)
            store.kv_set(SWEEP_CURSOR_KEY, cursor)
        for thread_id in found:
            if engine.status != "ready":
                return  # not marked done: the next start tries again
            task = self.put_away(thread_id)  # failed archives must survive even after the sweep is marked done
            if task:
                await task
        tries = int(store.kv_get(SWEEP_TRIES_KEY, 0) or 0) + 1
        store.kv_set(SWEEP_TRIES_KEY, tries)
        if listed or (tries >= SWEEP_TRIES and not cursor):
            store.kv_set(SWEEP_KEY, SWEEP_VERSION)
        if found:
            logger.info("Tidied up %d of Weebo's older Codex threads", len(found))

    @staticmethod
    def _weebos(thread: dict) -> bool:
        """A thread Weebo started and named, not a copy the user made of one in Codex (a fork keeps the name)."""
        if not thread.get("id") or not str(thread.get("name") or "").startswith(NAME_PREFIXES):
            return False
        if thread.get("forkedFromId") or thread.get("parentThreadId"):
            return False
        # A display name alone is not ownership: users can name their own threads "Weebo · ...".
        # Older Codex versions are covered by the explicit ids in our own database instead.
        return thread.get("originator") == ORIGINATOR
