# Delegation v1 (Weebo-as-Coordinator)
> Consolidated planning is tracked in `docs/master-roadmap.md`.

## Goals

- Keep Weebo conversationally responsive while long-running coding work happens asynchronously.
- Keep delegated work tied to the same chat session by default.
- Avoid automatic progress spam; users request status explicitly.
- Add this with minimal architectural risk and no broad rewrites.

## Product Rules (v1)

1. **Weebo remains the single user-facing voice.** Worker agents do not speak directly.
2. **No automatic progress updates.** User asks `/work-status` to fetch current state.
3. **Delegation defaults to same session.** Completion is appended to the originating session.
4. **Completion awareness across sessions** is modeled via task/session metadata pointers, so future UI/Telegram bridges can notify users regardless of active thread.
5. **No "do not delegate" toggle** in v1.

## Commands (v1)

- `/delegate-code <request>`
  - Creates a delegated coding task and returns a task id.
  - Immediately acknowledges and returns control to user.

- `/work-status`
  - Lists recent delegated task ids for current session.

- `/work-status <task_id>`
  - Returns task status/reason/step for that delegated task.

- `/session-info`
  - Returns current session id, derived identity key, and mapped identity session pointer (for continuity debugging).

## Lifecycle

1. User sends `/delegate-code ...`.
2. Weebo creates `EPHEMERAL_AGENT_TASK` with delegated metadata.
3. Weebo stores task pointer in session metadata (`metadata.delegated_tasks`).
4. Background coroutine runs delegated prompt via orchestrator.
5. Task status transitions through `GENERATING_CODE` to terminal status.
6. Completion/failure message is appended to originating session.

## Worker Capability Defaults (Coder)

- Allowed by default:
  - Read/write files in project workspace.
  - Execute local test/lint/build commands.
  - Use surgical code replacement where available (`surgical_replace_node`) for safer edits.
- Deferred to later versions:
  - Dynamic agent creation from scratch.
  - Open-ended privilege escalation prompts.
  - Direct worker-to-user chat output.

## Risk Controls

- Feature is command-gated (`/delegate-code`) instead of globally auto-delegating all prompts.
- Uses existing `TaskManager` lifecycle and status APIs.
- Stores lightweight metadata only (`task_id`, prompt preview, timestamp).
- Fallback behavior: if orchestrator loop unavailable, delegated task fails with explicit reason and user-visible assistant message.

## Follow-on Milestones

1. Add per-user completion inbox notifications (cross-session visibility).
2. Add Telegram adapter with persistent session mapping and `/start` session reset.
3. Expand from fixed worker roles to templated specialist profiles.
4. Introduce richer policy profiles for worker tool permissions.


## Telegram continuity (initial wiring)

- `/chat` now accepts identity hints (`platform`, `user_id`, `chat_id`) and derives a stable identity key.
- For identity-backed requests without explicit `session_id`, the server reuses the mapped session for deterministic continuity.
- `/start` rotates the identity mapping to a fresh session id, establishing explicit reset semantics.


## Continuity APIs (ops/debug)

- `GET /api/sessions/identity` with `identity_key` (or `platform`, `user_id`, `chat_id`) returns the mapped session id.
- `POST /api/sessions/identity/reset` with the same identity payload rotates to a fresh mapped session (same semantics as `/start`).
- `GET /api/sessions/identity/list` returns current identity pointer mappings and whether each mapped session still exists.
- `POST /api/sessions/identity/prune` removes stale identity pointers that reference missing sessions.
