# Weebo 3 second bug audit — 2026-10-17

This pass starts at `16e4271` on `ccr-4344bfa1-gl9ayf`, after the first audit's fixes. It is a focused
bug-fix pass, not another implementation of the seven-phase plan. Weebo 1.x, credentials, model settings and
the installed Codex protocol are unchanged. The user's application remains stopped.

## Fixed

### Bot lifecycle and permissions

- Paused or retired bots no longer receive automatic approvals from stale profile snapshots. Their agent
  requests are checked against the current owner, including native approval requests arriving after deletion.
- Retirement expires the bot's bundled approval requests, cancels **all** open/running jobs (not just the
  first 50), cancels paused as well as active routines, and stops only that bot's agents.
- Retiring while Codex startup is waiting now signals the existing cancellation event promptly, even before
  a thread ID is available. Deleting a conversation also signals that event.
- Retirement keeps listening after an interrupt acknowledgement until the terminal event arrives, then
  detaches and archives the thread. Late tools stay denied; queued user replies cannot revive the retired bot.

### Honest draft and brief completion

- A requested draft no longer becomes Done when it is merely queued or started. An internal delivery receipt
  links the request to its chief turn; completion requires a successful turn with a nonempty final answer.
- The Ideas UI shows the pending state and prevents another Do it request while that draft is pending. Failed
  or interrupted drafts can be retried. Dismissing an idea prevents late completion from resurrecting it.
- Stop, conversation deletion and restart recovery release pending draft/brief receipts. No action is
  automatically replayed during recovery.
- Brief reports are acknowledged only when the chief's answer finishes successfully, and only the reports
  included in that request are acknowledged. Failed briefs retain their reports and permit another attempt.
- Approval-decision digest pagination uses journal sequence IDs, preserving overflow even when timestamps
  match. The previous timestamp checkpoint migrates without replaying old decisions.
- Discuss cannot resurrect completed, dismissed or expired ideas; repeated dismissal is one negative vote,
  not several. Commitment suggestions attribute the assistant's promises to Weebo, not to the user.

### Facts-derived UI and calendar verification

- Adding a related email thread or person invalidates the affected day-sheet row without rebuilding unrelated
  rows. Weather/directions enrichment refreshes at the existing facts TTL.
- Missing event locations produce unknown travel, not a false same-place claim. Suggestions carry the actual
  reason rather than always blaming a missing directions connector.
- Calendar day queries use the user's timezone and the next local midnight, including short/long DST days.
- Calendar booking reuses the existing strict time computation: nonexistent or ambiguous naive local times
  are rejected, and durations use elapsed seconds across clock changes.
- Booking read-back now compares event ID, title, description, start and end instants. A matching title alone
  cannot confirm an event at the wrong time. The existing calendar test fixture now supplies realistic ID and
  timestamp fields; its assertions were preserved.

Calendar field and interval semantics were checked against the official
[event resource](https://developers.google.com/workspace/calendar/api/v3/reference/events) and
[event-list reference](https://developers.google.com/workspace/calendar/api/v3/reference/events/list).
Tests do not access a real calendar or use real credentials.

## Validation

40 new regression checks are in `tests/weebo/test_second_audit.py` and a real Chromium
pending/failure/retry/completion flow in `test_weebo3_ui.py`. Failing regression runs were observed before the
corresponding fixes. No test assertions were removed or weakened.

Final-code validation on Python 3.14 / Windows:

- `python -m weebo --selftest`: passed.
- All 13 JavaScript modules pass `weebo.evolution.gates.check_js`.
- `python -m pytest tests/weebo -q -p no:cacheprovider`: **765 passed**, one existing Windows symlink skip.
- Repository-wide non-integration/non-smoke lane: **1,526 passed**, two existing platform skips (symlinks and
  `notify-send`), seven marker exclusions, 13 passing subtests. ChromaDB emits an upstream Python deprecation
  warning; its dependency and the legacy code were left unchanged.
- Diff scope, whitespace and common credential-pattern checks passed. No production configuration was edited.

## Still needs a design decision

The first audit's larger limitations remain; this pass does not claim they are solved:

1. **Native connector isolation:** observing and interrupting a native tool after it starts cannot guarantee
   prevention of its external side effect. Inherited global apps/MCP servers need a real pre-execution isolation
   boundary verified against a live Codex installation.
2. **Connector sandbox and broad approval rules:** connector subprocesses are authorized, not OS-sandboxed.
   Command/rule heuristics are not a complete capability security boundary. Avoid treating unattended external
   actions as safe based only on those heuristics.
3. **Pre-publication grounding:** facts checks remain advisory/post-stream, not a hard publication gate.
   Draft/brief delivery receipts prove successful answer completion, not that every requested fact or report was
   semantically covered by that answer. That stronger guarantee needs a deliberate output-validation policy.
4. **SMS and direct-user budgets:** SMS still checks provider acceptance rather than ultimate delivery.
   Direct user-to-bot chats retain the existing tested budget exemption; automated turns obey the caps.

Validation did not launch the user's application or run live Codex model turns or external connector actions.
Publication target: `ccr-4344bfa1-gl9ayf`. Local validation is recorded above; CI results must be checked separately.
