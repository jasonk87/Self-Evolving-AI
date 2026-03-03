# Mission Control UI v1 Blueprint

## Goal
Provide a real-time operational view of the AI assistant that is understandable to end-users and actionable for operators.

## Core Panels

1. **Run Timeline**
   - current goal
   - planner/reviewer/executor phases
   - elapsed time and current step

2. **Task & Queue Health**
   - active tasks
   - failed/interrupted tasks
   - pending approvals/review gates

3. **Tool Activity**
   - last tool calls
   - result summaries
   - failures and retries

4. **Suggestions & Learning**
   - pending suggestions
   - autonomous learning status
   - accepted/rejected trends

5. **System Health**
   - debug mode state
   - model provider status
   - background queue depth

## Backend Contract (v1)

Expose structured status snapshots via `ai_assistant.core.status_reporting.get_status_snapshot()`:

- `tools_total`
- `tools_by_type`
- `projects_summary`
- `suggestions_summary`
- `background_tasks`
- `debug_mode_enabled`
- `autonomous_learning_enabled`

## Rollout Plan

1. Build snapshot endpoint and consume in a simple status widget.
2. Add event stream cards for planner/executor lifecycle updates.
3. Add quick actions: retry, review, dismiss, inspect logs.
4. Add run-history playback and diff view for repaired plans.

## Success Metrics

- Time-to-diagnose failed run reduced by 50%.
- User manual retries reduced by 30%.
- Median completion time for common workflows reduced by 20%.
