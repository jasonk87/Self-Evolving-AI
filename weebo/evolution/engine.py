"""Self-evolution: Weebo improves its own code, safely.

Lifecycle of a proposal::

    [vetting ->] proposed -> queued -> building -> checking -> ready -> merged (-> verified)
         \\-> declined                    \\-> failed      \\-> rejected / discarded / conflict / rolled_back

* Ideas Weebo came up with itself go to the Council first (council.py): is this worth
  building at all? Declined ideas are kept with the reason; the user can still build them.
  Ideas the user asked for skip the Council.
* Building happens in an isolated git worktree on its own branch, by a Codex agent
  that can only write inside that worktree.
* Weebo then verifies the result itself: syntax, a boot self-test and the test suite
  (gates.py), then a Codex code review of exactly the agent's change. Failures go back
  to the agent for up to two revision rounds. A review that can't run fails the build.
* Weebo 1.x's change policy classifies every touched file. Only UI/tests/docs/skills
  changes may merge without a human; core, execution and governance code always wait
  for the user.
* After a merge Weebo restarts into the new code. The supervisor reverts the merge
  automatically if the upgraded Weebo fails to boot.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import log, paths
from ..codex.rpc import EngineClosed, RpcError
from . import council, git
from .gates import run_gates

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("evolution")

MAX_ROUNDS = 3
OPEN_STATES = ("vetting", "proposed", "queued", "building", "checking", "ready", "merging")
PENDING_FILE = "evolution_pending.json"
SCRATCH_DIR = ".weebo-tmp"  # build agents keep temp files, logs and test dirs here; never committed
SCRATCH_EXCLUDES = tuple(f":(exclude){name}" for name in (SCRATCH_DIR, ".verification-tmp"))


class EvolutionError(RuntimeError):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32] or "change"


BUILD_PROMPT = """You are improving Weebo, a self-evolving AI assistant, by changing its own source code.
You are working in an isolated git worktree of Weebo's repository: {worktree}
Weebo 2.0 lives in weebo/ (Python, asyncio + aiohttp, Codex app-server integration) with the web UI in weebo/web/
(vanilla ES modules, no build step). Weebo 1.x lives in ai_assistant/ and is reused through weebo/legacy/.

## Improvement to implement
Title: {title}

{description}

Why: {rationale}

## Rules
- Make the smallest complete change that fully implements this. Match the surrounding code style.
- Add or update tests under tests/weebo/ that prove the change works (pytest, pytest-asyncio available).
- Run the checks yourself before finishing:
    python -m weebo --selftest
    python -m pytest tests/weebo -q -p no:cacheprovider
  Fix anything that fails.
- Do not commit; Weebo verifies and commits your work.
- Keep scratch files (temp dirs, logs, patches; point TEMP/TMP there if tests need it) in `.weebo-tmp/` at the
  worktree root. It is never committed. Don't leave other files that aren't part of the change.
- Do not touch weebo_data/, .git, or anything outside this worktree.
- Finish with a short report: what you changed (files), how you verified it, and any caveats.
"""

REVISION_PROMPT = """Your previous attempt at "{title}" did not pass Weebo's verification. Fix these problems in the
same worktree ({worktree}), re-run the checks, and report back. Do not commit.

{feedback}
"""


class EvolutionEngine:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._vetting: set[asyncio.Task] = set()
        self.current: str | None = None
        self.restart_after_build: str | None = None  # a merged upgrade waits for the running build before restarting

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._recover()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._work(), name="evolution")

    async def stop(self) -> None:
        """Stop Council sessions in flight; they are convened again on the next start."""
        tasks = list(self._vetting)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _recover(self) -> None:
        store = self.app.store
        for proposal in store.list_proposals(limit=200, statuses=("queued", "building", "checking", "merging")):
            store.update_proposal(proposal["id"], status="failed",
                                  gate_report=json.dumps({"ok": False, "error": "Interrupted by a restart. Rebuild to try again."}))
        for proposal in store.list_proposals(limit=200, statuses=("vetting",)):
            self._start_vetting(proposal["id"])  # the Council was interrupted; convene again
        self._empty_trash()
        self._verify_after_upgrade()

    def _verify_after_upgrade(self) -> None:
        """Read the supervisor's note about the last merge (verified boot or rollback)."""
        pending_path = paths.data_dir() / PENDING_FILE
        if not pending_path.exists():
            return
        try:
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pending_path.unlink(missing_ok=True)
            return
        proposal_id = pending.get("proposal_id")
        proposal = self.app.store.get_proposal(proposal_id) if proposal_id else None
        if pending.get("rolled_back"):
            if proposal:
                self._set(proposal_id, "rolled_back")
            self.app.store.journal("evolution", "Rolled back an upgrade that failed to boot",
                                   proposal["title"] if proposal else "", pending)
            self.app.notify("evolution", "Upgrade rolled back",
                            "The new version failed to start, so Weebo reverted it automatically.", {"proposal_id": proposal_id})
            pending_path.unlink(missing_ok=True)
            return
        # This boot runs the merged code; confirm it once the server has been healthy for a bit.
        asyncio.get_event_loop().call_later(20, self._mark_verified, proposal_id, pending_path)

    def _mark_verified(self, proposal_id: str | None, pending_path: Path) -> None:
        pending_path.unlink(missing_ok=True)
        if proposal_id and self.app.store.get_proposal(proposal_id):
            proposal = self.app.store.get_proposal(proposal_id)
            meta = dict(proposal.get("meta") or {})
            meta["verified_at"] = time.time()
            self.app.store.update_proposal(proposal_id, meta=meta)
            self.app.store.journal("evolution", "Upgrade is live", proposal["title"])
            self.app.bus.publish("evolution.updated", {"proposal": self.app.store.get_proposal(proposal_id)})

    async def _work(self) -> None:
        while True:
            proposal_id = await self._queue.get()
            proposal = self.app.store.get_proposal(proposal_id)
            if proposal is None or proposal["status"] != "queued":
                continue  # rejected or discarded while waiting in line
            self.current = proposal_id
            try:
                await self._build(proposal_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Evolution build crashed")
                self._fail(proposal_id, f"Build crashed: {exc}")
            finally:
                self.current = None
                if self.restart_after_build and self._queue.empty():
                    reason, self.restart_after_build = self.restart_after_build, None
                    self.app.request_restart(reason)

    # ------------------------------------------------------------------ public API
    async def propose(self, title: str, description: str, rationale: str = "", source: str = "user",
                      conversation_id: str | None = None, meta: dict | None = None) -> dict[str, Any]:
        title = (title or "").strip()[:140]
        description = (description or "").strip()
        if not title or not description:
            raise EvolutionError("A proposal needs a title and a description.")
        duplicate = self._find_duplicate(title)
        if duplicate:
            return duplicate
        proposal = self.app.store.add_proposal(title, description, rationale.strip(), source,
                                               meta={"conversation_id": conversation_id, **(meta or {})})
        self.app.store.journal("evolution", f"New self-improvement idea: {title}", rationale[:400], {"proposal_id": proposal["id"]})
        self.app.bus.publish("evolution.updated", {"proposal": proposal})
        if self.app.settings.get("evolution.mode") == "off":
            return proposal
        if council.needs_council(source):
            self._set(proposal["id"], "vetting")
            self._start_vetting(proposal["id"])
        else:
            await self._go_ahead(proposal["id"])
        return self.app.store.get_proposal(proposal["id"]) or proposal

    async def _go_ahead(self, proposal_id: str, note: str = "") -> None:
        """An idea is cleared to proceed (the user asked for it, or the Council approved it)."""
        if self.app.settings.get("evolution.mode") in ("build", "auto_merge"):
            await self.approve(proposal_id)
        else:
            title = self._get(proposal_id)["title"]
            self.app.notify("evolution", "Weebo has an idea for improving itself", title + note, {"proposal_id": proposal_id})

    def _start_vetting(self, proposal_id: str) -> None:
        task = asyncio.create_task(self._vet(proposal_id), name=f"council-{proposal_id}")
        self._vetting.add(task)
        task.add_done_callback(self._vetting.discard)

    async def _vet(self, proposal_id: str) -> None:
        """Convene the Council on one of Weebo's own ideas. Nothing is built unless it explicitly approves."""
        try:
            ok, why = self.app.heartbeat.budget()
            if ok:
                verdict = await council.convene(self.app, self._get(proposal_id))
            else:
                verdict = {"approved": None, "reason": f"Not vetted: {why} It waits for your decision instead."}
            proposal = self._get(proposal_id)
            if proposal["status"] != "vetting":
                return  # the user built, rejected or discarded it meanwhile
            meta = {**(proposal.get("meta") or {}), "council": verdict}
            if verdict["approved"]:
                self.app.store.update_proposal(proposal_id, status="proposed", meta=meta)
                self.app.store.journal("evolution", f"The Council approved: {proposal['title']}", verdict["reason"],
                                       {"proposal_id": proposal_id})
                self._publish(proposal_id)
                await self._go_ahead(proposal_id, " (the Council approved it)")
            elif verdict["approved"] is False:
                self.app.store.update_proposal(proposal_id, status="declined", meta=meta)
                self.app.store.journal("evolution", f"The Council declined: {proposal['title']}", verdict["reason"],
                                       {"proposal_id": proposal_id})
                self._publish(proposal_id)
            else:
                self.app.store.update_proposal(proposal_id, status="proposed", meta=meta)
                self._publish(proposal_id)
                self.app.notify("evolution", "An idea needs your decision", f"{proposal['title']}. {verdict['reason']}",
                                {"proposal_id": proposal_id})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Council vetting crashed")
            if (self.app.store.get_proposal(proposal_id) or {}).get("status") == "vetting":
                self._set(proposal_id, "proposed")

    async def approve(self, proposal_id: str) -> dict[str, Any]:
        proposal = self._get(proposal_id)
        if proposal["status"] not in ("vetting", "proposed", "declined", "ready", "failed", "conflict", "rolled_back",
                                      "rejected"):  # "ready" too: the panel offers Rebuild there
            raise EvolutionError(f"Proposal is {proposal['status']}; it can't be built now.")
        if proposal["status"] != "proposed":
            await self._cleanup(proposal)
        self._set(proposal_id, "queued")
        await self._queue.put(proposal_id)
        return self._get(proposal_id)

    async def reject(self, proposal_id: str) -> dict[str, Any]:
        proposal = self._get(proposal_id)
        if proposal["status"] in ("building", "checking", "merging"):
            raise EvolutionError("Wait for the current build step to finish first.")
        await self._cleanup(proposal)
        return self._set(proposal_id, "rejected")

    async def discard(self, proposal_id: str) -> dict[str, Any]:
        return await self.reject(proposal_id)

    def queue_position(self, proposal_id: str) -> int:
        """How many builds run before this one (0 = next or now)."""
        waiting = list(getattr(self._queue, "_queue", []))
        ahead = waiting.index(proposal_id) if proposal_id in waiting else 0
        return ahead + (1 if self.current and self.current != proposal_id else 0)

    def diff(self, proposal_id: str) -> str:
        proposal = self._get(proposal_id)
        return str((proposal.get("meta") or {}).get("diff", ""))

    # ------------------------------------------------------------------ build
    async def _build(self, proposal_id: str) -> None:
        proposal = self._get(proposal_id)
        root = paths.PROJECT_ROOT
        if not await git.is_repo(root):
            raise EvolutionError(f"{root} is not a git repository, so Weebo can't evolve safely.")
        base_branch = await git.current_branch(root)
        # Build on the code exactly as it is on disk right now (uncommitted work included) so the upgrade
        # matches what is actually running. The snapshot never touches the user's branch or index.
        base_commit = await git.snapshot_commit(root)
        branch = f"weebo/evolve-{proposal_id[2:10]}-{_slug(proposal['title'])}"
        worktree = paths.worktrees_dir() / proposal_id
        await self._remove_worktree(worktree)
        branch_free = (await git.git(root, "branch", "-D", branch)).ok or not await self._branch_exists(branch)
        if worktree.exists() or not branch_free:
            # The old folder (or its branch) is still held by something; build next to it instead of crashing.
            suffix = str(int(time.time()))
            worktree, branch = paths.worktrees_dir() / f"{proposal_id}-{suffix}", f"{branch}-{suffix}"
        await git.git(root, "worktree", "add", "-b", branch, str(worktree), base_commit, check=True, timeout=300)
        # The code review compares against this ref, so it sees only the agent's change, not work that was
        # already on disk (uncommitted) when the build started.
        base_ref = f"weebo/base-{proposal_id[2:10]}"
        await git.git(root, "branch", "-f", base_ref, base_commit, check=True)
        meta = dict(proposal.get("meta") or {})
        meta.pop("blocked", None)
        meta.update({"base_branch": base_branch, "base_ref": base_ref, "rounds": [], "base_head": await git.head(root),
                     "round": 1, "stage": "building"})
        self.app.store.update_proposal(proposal_id, status="building", branch=branch, worktree=str(worktree),
                                       base_commit=base_commit, meta=meta)
        self._publish(proposal_id)
        self.app.bus.publish("weebo.mood", {"mood": "evolving", "background": True})

        feedback = ""
        gate_report = None
        review: dict[str, Any] = {}
        changed: list[str] = []
        for round_number in range(1, MAX_ROUNDS + 1):
            self._stage(proposal_id, "building", "building", round_number)
            prompt = (BUILD_PROMPT if round_number == 1 else REVISION_PROMPT).format(
                worktree=worktree, title=proposal["title"], description=proposal["description"],
                rationale=proposal["rationale"] or "(not given)", feedback=feedback)
            task = await self.app.agents.start(
                f"Evolve: {proposal['title']}" + (f" (revision {round_number - 1})" if round_number > 1 else ""),
                prompt, cwd=str(worktree), kind="evolution",
                meta={"sandbox_mode": "workspace-write-auto", "proposal_id": proposal_id, "tools_scope": "none"},
                effort=self.app.settings.get("codex.agent_effort"))
            self.app.store.update_proposal(proposal_id, task_id=task["id"])
            finished = await self.app.agents.wait(task["id"])
            if finished["status"] != "completed":
                self._fail(proposal_id, f"The build agent {finished['status']}: {finished.get('error') or 'no details'}")
                return
            await self._commit_all(worktree, f"Weebo evolution: {proposal['title']}"
                                   + (f" (revision {round_number - 1})" if round_number > 1 else ""))
            changed = await git.changed_files(worktree, base_commit)
            if not changed:
                self._fail(proposal_id, "The build agent finished without changing any files.")
                return
            self._stage(proposal_id, "checking", "testing", round_number)
            gate_report = await run_gates(worktree, changed, self.app.settings.get("evolution.test_command"))
            self._stage(proposal_id, "checking", "reviewing", round_number)
            review = await self._codex_review(worktree, base_ref)
            if review.get("error"):
                review = await self._codex_review(worktree, base_ref)  # one retry, then fail closed
            round_info = {"round": round_number, "gates_ok": gate_report.ok, "review_blocking": review.get("blocking"),
                          "task_id": task["id"]}
            meta = dict(self._get(proposal_id).get("meta") or {})
            meta.setdefault("rounds", []).append(round_info)
            self.app.store.update_proposal(proposal_id, meta=meta)
            if review.get("error"):
                self._fail(proposal_id, f"The code review couldn't run ({review['error']}), so the change isn't "
                                        "trusted. Rebuild to try again.")
                return
            if gate_report.ok and not review.get("blocking"):
                break
            feedback = self._feedback(gate_report, review)

        assert gate_report is not None
        diff_text = (await git.git(worktree, "diff", f"{base_commit}...HEAD", timeout=60)).out
        diff_stat = (await git.git(worktree, "diff", "--stat", f"{base_commit}...HEAD", timeout=60)).out.strip()
        head_commit = await git.head(worktree)
        governance = self._governance(changed)
        meta = dict(self._get(proposal_id).get("meta") or {})
        meta.update({"diff": diff_text[:400_000], "changed": changed, "governance": governance,
                     "review_blocking": review.get("blocking", False), "stage": None})
        passed = gate_report.ok and not review.get("blocking")
        self.app.store.update_proposal(
            proposal_id, status="ready" if passed else "failed", head_commit=head_commit, diff_stat=diff_stat,
            gate_report=json.dumps(gate_report.to_dict()), review_report=review.get("text", ""), meta=meta)
        self._publish(proposal_id)
        proposal = self._get(proposal_id)
        if not passed:
            self.app.store.journal("evolution", f"Couldn't finish: {proposal['title']}",
                                   "Verification still failing after revisions.\n" + gate_report.summary())
            self.app.notify("evolution", "Self-improvement needs a look", f"{proposal['title']}: verification failed.",
                            {"proposal_id": proposal_id})
            return
        self.app.store.journal("evolution", f"Built and verified: {proposal['title']}",
                               f"{len(changed)} file(s). {gate_report.summary()}", {"proposal_id": proposal_id})
        auto = (self.app.settings.get("evolution.mode") == "auto_merge" and governance["autonomous"])
        if auto:
            try:
                await self.merge(proposal_id, automatic=True)
                return
            except EvolutionError as exc:
                logger.info("Auto-merge skipped: %s", exc)
        why = "" if governance["autonomous"] else " It touches core code, so it needs your OK."
        self.app.notify("evolution", "Upgrade ready to review", f"{proposal['title']}.{why}", {"proposal_id": proposal_id})
        self.app.bus.publish("weebo.mood", {"mood": "proud", "background": True})

    async def _checkpoint(self, root: Path, title: str) -> str:
        """Save the user's uncommitted work as a normal commit (their identity, their hooks) before merging."""
        await git.git(root, "add", "-A", check=True, timeout=300)
        message = f"Checkpoint: save work before Weebo upgrade \"{title}\""
        result = await git.git(root, "commit", "-q", "-m", message, timeout=300)
        if not result.ok and "user.email" in result.text() + result.err:
            result = await git.git(root, "-c", "user.name=Weebo", "-c", "user.email=weebo@localhost",
                                   "commit", "-q", "-m", message, timeout=300)
        if not result.ok:
            await git.git(root, "reset", "-q")
            raise EvolutionError("Couldn't save your uncommitted work in a checkpoint commit: " + result.text()[-600:])
        self.app.store.journal("evolution", "Saved your work in a checkpoint commit", title)
        return await git.head(root)

    async def _commit_all(self, worktree: Path, message: str) -> None:
        await git.git(worktree, "add", "-A", "--", ".", *SCRATCH_EXCLUDES, check=True)
        staged = await git.git(worktree, "diff", "--cached", "--quiet")
        if staged.code == 0:
            return  # nothing but scratch files changed
        await git.git(worktree, "-c", "user.name=Weebo", "-c", "user.email=weebo@localhost", "commit", "-q",
                      "-m", message, check=True)

    def _feedback(self, report: Any, review: dict[str, Any]) -> str:
        parts = []
        if not report.ok:
            parts.append("## Failing checks\n" + report.failure_text())
        if review.get("blocking"):
            parts.append("## Code review found blocking issues\n" + review.get("text", "")[-5000:])
        return "\n\n".join(parts)

    def _governance(self, changed: list[str]) -> dict[str, Any]:
        try:
            from ai_assistant.core.change_policy import decide_governance
        except Exception as exc:  # the policy module is required for any automatic merge
            return {"autonomous": False, "files": [], "error": str(exc)}
        files = []
        for path in changed:
            decision = decide_governance(path, project_root=paths.PROJECT_ROOT)
            files.append({"path": path, "zone": decision.zone.value, "tier": decision.tier.value,
                          "reason": decision.reason})
        autonomous = bool(files) and all(f["tier"] == "autonomous" for f in files)
        return {"autonomous": autonomous, "files": files}

    async def _codex_review(self, worktree: Path, base_ref: str) -> dict[str, Any]:
        """Codex's built-in code review of the agent's change (everything since ``base_ref``).
        Findings tagged P0/P1 block the merge. A review that doesn't finish returns ``error``."""
        engine = self.app.engine
        try:
            result = await engine.start_thread(cwd=str(worktree), approvalPolicy="never", sandbox="read-only",
                                               ephemeral=True, serviceName="weebo-review",
                                               model=engine.default_model(self.app.settings.get("codex.model")))
        except (RpcError, EngineClosed, asyncio.TimeoutError) as exc:
            return {"text": f"Review unavailable: {exc}", "blocking": True, "error": str(exc) or type(exc).__name__}
        thread_id = result["thread"]["id"]
        done = asyncio.Event()
        state: dict[str, Any] = {"review": "", "messages": [], "status": None}

        def listener(method: str, params: dict[str, Any]) -> None:
            if method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "exitedReviewMode":
                    state["review"] = item.get("review", "")
                elif item.get("type") == "agentMessage":
                    state["messages"].append(item.get("text", ""))
            elif method in ("turn/completed", "engine/closed"):
                state["status"] = (params.get("turn") or {}).get("status", "failed")
                done.set()

        async def deny(method: str, params: dict[str, Any]) -> Any:
            return {"decision": "decline"} if "Approval" in method or "approval" in method.lower() else {"answers": {}}

        engine.route(thread_id, listener=listener, request_handler=deny)
        try:
            await engine.start_review(thread_id, {"type": "baseBranch", "branch": base_ref})
            await asyncio.wait_for(done.wait(), 1200)
        except (RpcError, EngineClosed, asyncio.TimeoutError) as exc:
            return {"text": f"Review did not complete: {exc}", "blocking": True, "error": str(exc) or "timed out"}
        finally:
            engine.unroute(thread_id)
            engine.loaded_threads.discard(thread_id)
            self.app.count_background_turn("review")
        text = (state["review"] or "\n\n".join(state["messages"])).strip()
        if state["status"] != "completed" or not text:
            reason = f"review turn {state['status']}" if state["status"] != "completed" else "review was empty"
            return {"text": text or f"Review did not complete ({reason}).", "blocking": True, "error": reason}
        blocking = bool(re.search(r"\[P[01]\]|\bpriority[:\s]*[01]\b", text, flags=re.IGNORECASE))
        return {"text": text, "blocking": blocking}

    # ------------------------------------------------------------------ merge & rollback
    async def merge(self, proposal_id: str, automatic: bool = False) -> dict[str, Any]:
        proposal = self._get(proposal_id)
        if proposal["status"] != "ready":
            raise EvolutionError(f"Only ready proposals can be merged (this one is {proposal['status']}).")
        root = paths.PROJECT_ROOT
        meta = dict(proposal.get("meta") or {})
        current = await git.current_branch(root)
        previous_head = await git.head(root)
        checkpoint = None
        pending = await git.working_changes(root)
        if pending:
            if not self.app.settings.get("evolution.checkpoint_commits"):
                raise EvolutionError("Your working tree has uncommitted changes (" + ", ".join(pending[:5])
                                     + "). Commit or stash them, or turn on checkpoint commits in Settings.")
            checkpoint = await self._checkpoint(root, proposal["title"])
        self._set(proposal_id, "merging")
        # The build started from a snapshot of the code on disk, which no branch contains. Merging the branch
        # directly would diff against the last real commit, so every file the snapshot added would collide
        # (add/add). Replay only the agent's own commits onto the current code, then merge those.
        result = await self._replay(proposal, await git.head(root))
        if result.ok:
            result = await git.git(root, "-c", "user.name=Weebo", "-c", "user.email=weebo@localhost", "merge", "--no-ff",
                                   "-m", f"Weebo evolution: {proposal['title']} ({proposal_id})", proposal["branch"],
                                   timeout=300)
            if not result.ok:
                await git.git(root, "merge", "--abort")
        if not result.ok:
            if checkpoint:
                # Put the user's work back exactly as it was: uncommitted, nothing lost.
                await git.git(root, "reset", "-q", previous_head)
            self.app.store.update_proposal(proposal_id, status="conflict", review_report=(
                proposal.get("review_report", "") + "\n\nMerge conflict:\n" + result.text()[-3000:]))
            self._publish(proposal_id)
            raise EvolutionError("The upgrade conflicts with newer changes. Rebuild it on the latest code.")
        merged = await git.head(root)
        meta.update({"previous_head": previous_head, "merged_into": current, "merged_at": time.time(),
                     "automatic": automatic, "checkpoint": checkpoint})
        self.app.store.update_proposal(proposal_id, status="merged", merged_commit=merged, meta=meta)
        await self._cleanup(self._get(proposal_id), delete_branch=True)
        self.app.store.journal("evolution", f"Upgraded myself: {proposal['title']}",
                               ("Merged automatically" if automatic else "Merged with your approval") + f" ({merged[:8]}).",
                               {"proposal_id": proposal_id})
        self._publish(proposal_id)
        self.app.bus.publish("weebo.mood", {"mood": "celebrate"})
        self.app.notify("evolution", "Weebo upgraded itself", proposal["title"], {"proposal_id": proposal_id})
        needs_restart = any(f.endswith(".py") for f in meta.get("changed", []))
        if needs_restart:
            (paths.data_dir() / PENDING_FILE).write_text(json.dumps({
                "proposal_id": proposal_id, "merged_commit": merged, "previous_head": previous_head, "at": time.time(),
            }), encoding="utf-8")
            if self.current or not self._queue.empty():
                # Restarting now would kill the build in progress; the merged code goes live right after it.
                self.restart_after_build = "Applying a self-upgrade"
                self.app.notify("evolution", "Upgrade merged", f"{proposal['title']}: Weebo restarts to apply it as soon "
                                "as the current build finishes.", {"proposal_id": proposal_id})
            else:
                self.app.request_restart("Applying a self-upgrade")
        else:
            self.app.bus.publish("ui.reload", {"reason": proposal["title"]})
        return self._get(proposal_id)

    async def _replay(self, proposal: dict[str, Any], onto: str) -> "git.GitResult":
        """Rebase the build branch's commits (everything after its snapshot base) onto ``onto``.
        Runs in the build's worktree (recreated if it's gone), so the user's checkout is never touched."""
        worktree = Path(proposal.get("worktree") or paths.worktrees_dir() / proposal["id"])
        if not (worktree / ".git").exists():
            if worktree.exists():
                worktree = paths.worktrees_dir() / f"{proposal['id']}-merge-{int(time.time())}"
            await git.git(paths.PROJECT_ROOT, "worktree", "prune")
            added = await git.git(paths.PROJECT_ROOT, "worktree", "add", str(worktree), proposal["branch"], timeout=300)
            if not added.ok:
                return added
            self.app.store.update_proposal(proposal["id"], worktree=str(worktree))
        result = await git.git(worktree, "-c", "user.name=Weebo", "-c", "user.email=weebo@localhost", "rebase",
                               "--onto", onto, proposal["base_commit"], timeout=300)
        if not result.ok:
            await git.git(worktree, "rebase", "--abort")
        return result

    async def rollback(self, proposal_id: str) -> dict[str, Any]:
        proposal = self._get(proposal_id)
        if proposal["status"] != "merged" or not proposal.get("merged_commit"):
            raise EvolutionError("Only merged upgrades can be rolled back.")
        root = paths.PROJECT_ROOT
        before = await git.head(root)
        checkpoint = None
        if await git.working_changes(root):
            if not self.app.settings.get("evolution.checkpoint_commits"):
                raise EvolutionError("Commit or stash your changes before rolling back, or turn on checkpoint commits.")
            checkpoint = await self._checkpoint(root, f"roll back {proposal['title']}")
        result = await git.git(root, "-c", "user.name=Weebo", "-c", "user.email=weebo@localhost", "revert",
                               "--no-edit", "-m", "1", proposal["merged_commit"], timeout=300)
        if not result.ok:
            await git.git(root, "revert", "--abort")
            if checkpoint:
                await git.git(root, "reset", "-q", before)
            raise EvolutionError("Couldn't revert cleanly: " + result.text()[-500:])
        self._set(proposal_id, "rolled_back")
        self.app.store.journal("evolution", f"Rolled back: {proposal['title']}", "Reverted at the user's request.")
        if any(f.endswith(".py") for f in (proposal.get("meta") or {}).get("changed", [])):
            self.app.request_restart("Rolling back an upgrade")
        else:
            self.app.bus.publish("ui.reload", {"reason": "rollback"})
        return self._get(proposal_id)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    async def _branch_exists(branch: str) -> bool:
        return (await git.git(paths.PROJECT_ROOT, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")).ok

    def _stage(self, proposal_id: str, status: str, stage: str, round_number: int) -> None:
        """Record the visible build step (building / testing / reviewing) and the revision round."""
        meta = {**(self._get(proposal_id).get("meta") or {}), "stage": stage, "round": round_number}
        self.app.store.update_proposal(proposal_id, status=status, meta=meta)
        self._publish(proposal_id)

    async def _cleanup(self, proposal: dict[str, Any], delete_branch: bool = True) -> None:
        if proposal.get("worktree"):
            await self._remove_worktree(Path(proposal["worktree"]))
        base_ref = (proposal.get("meta") or {}).get("base_ref")
        if base_ref:
            await git.git(paths.PROJECT_ROOT, "branch", "-D", base_ref)
        if delete_branch and proposal.get("branch"):
            await git.git(paths.PROJECT_ROOT, "branch", "-D", proposal["branch"])

    async def _remove_worktree(self, worktree: Path) -> None:
        if worktree.exists():
            await git.git(paths.PROJECT_ROOT, "worktree", "remove", "--force", str(worktree), timeout=120)
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
            if worktree.exists():
                # Folders the Codex sandbox account created inside (e.g. pytest temp dirs) can be locked to that
                # account. Move the worktree aside so its path is free for the next build; trash is retried later.
                trash = paths.worktrees_dir() / ".trash"
                trash.mkdir(exist_ok=True)
                try:
                    worktree.rename(trash / f"{worktree.name}-{int(time.time() * 1000)}")
                except OSError as exc:
                    logger.warning("Couldn't clear old worktree %s: %s", worktree, exc)
        await git.git(paths.PROJECT_ROOT, "worktree", "prune")

    @staticmethod
    def _empty_trash() -> None:
        trash = paths.worktrees_dir() / ".trash"
        if trash.is_dir():
            for item in trash.iterdir():
                shutil.rmtree(item, ignore_errors=True)

    def _find_duplicate(self, title: str) -> dict[str, Any] | None:
        words = set(re.findall(r"[a-z0-9]+", title.lower()))
        if not words:
            return None
        for proposal in self.app.store.list_proposals(limit=60, statuses=OPEN_STATES):
            other = set(re.findall(r"[a-z0-9]+", proposal["title"].lower()))
            if other and len(words & other) / len(words | other) >= 0.75:
                return proposal
        return None

    def _get(self, proposal_id: str) -> dict[str, Any]:
        proposal = self.app.store.get_proposal(proposal_id)
        if proposal is None:
            raise EvolutionError(f"No proposal {proposal_id}")
        return proposal

    def _set(self, proposal_id: str, status: str) -> dict[str, Any]:
        self.app.store.update_proposal(proposal_id, status=status)
        self._publish(proposal_id)
        return self._get(proposal_id)

    def _fail(self, proposal_id: str, reason: str) -> None:
        if self.app.store.get_proposal(proposal_id) is None:
            return
        self.app.store.update_proposal(proposal_id, status="failed",
                                       gate_report=json.dumps({"ok": False, "error": reason}))
        self._publish(proposal_id)
        self.app.store.journal("evolution", "Self-improvement attempt failed", reason[:600], {"proposal_id": proposal_id})
        self.app.diagnostics.record("evolution_failed", reason)

    def _publish(self, proposal_id: str) -> None:
        proposal = self.app.store.get_proposal(proposal_id)
        if proposal:
            slim = {k: v for k, v in proposal.items() if k != "meta"}
            meta = dict(proposal.get("meta") or {})
            meta.pop("diff", None)
            slim["meta"] = meta
            self.app.bus.publish("evolution.updated", {"proposal": slim})
