# Master Roadmap (Unified)

This document consolidates the major roadmap tracks into a single execution plan:

- Platform stabilization and release safety.
- Mission Control observability and operations UX.
- Weebo delegation and agent orchestration.
- Agent memory scopes (session-scoped vs persistent).
- Reflection-driven specialist agent expansion.

Related docs:
- `docs/stabilization-roadmap.md`
- `docs/mission-control-ui-v1.md`
- `docs/delegation-v1.md`

---

## 0) Current State Snapshot

### Implemented foundation (updated)
- Structured Mission Control status surfaces are in place (snapshot, health/cadence, topology, policy matrix).
- Delegation lifecycle is operational with task-backed status, work-inbox visibility, and cross-session identity continuity endpoints.
- Agent scope contracts are now centralized and enforced at task-creation time with contract metadata/audit visibility.
- Reflection-to-specialist lifecycle now includes suggestions, templated spawns, and review-gated dynamic specialist proposals.
- Release-gate coverage pack exists with unit/integration/smoke/failure-mode evidence lanes.

### Remaining gaps (program-level)
- Production-quality SLO dashboarding and explicit operational SLO targets.
- Final documentation pass to keep roadmap “current state” synchronized after each phase completion.
- Broader persistent user-scoped agent lifecycle/productization beyond current proposal-and-contract foundations.

---

## 1) Guiding Product Principles

1. **Weebo is always the single conversational voice.**
2. **Delegation should reduce latency, not increase cognitive load.**
3. **Status is pull-first (on demand), not spammy push.**
4. **Safety is policy-based defaults, not constant manual approvals.**
5. **Agent memory scope must match task type.**
6. **Roadmap milestones must ship with measurable success criteria.**

---

## 2) Agent Scope Model (New Core Track)

### A. Session-scoped agents (ephemeral)
Use for coding/review tasks bound to a specific request context.

- Lifecycle: created from a user request; retired on completion/failure.
- Memory: only current session/task context.
- Persistence: metadata pointer + artifacts + summary.
- Default examples: `coder_worker`, `task_reviewer_worker`.

### B. User-scoped persistent agents
Use for long-lived environment/domain awareness.

- Lifecycle: durable profile with controlled updates.
- Memory: user environment, paths, preferences, known collaborators.
- Persistence: profile store with versioning and provenance.
- Default examples: `desktop_assistant_worker`, `ops_assistant_worker`.

### C. Policy requirement
Every spawned agent must explicitly declare:
- `scope_type`: `session` or `user`
- `capability_profile`: tool families and limits
- `retention_policy`: when/how memory is kept or dropped

---

## 3) Delegation & Orchestration Roadmap

## Phase D1 — Delegation v1 hardening (current)
- Keep command-triggered delegation and on-demand status.
- Improve task lookup ergonomics and status summaries.
- Ensure completion/failure messages append to origin session.

**Exit criteria**
- Delegated tasks are discoverable by short IDs.
- Status queries return stable, actionable summaries.
- Failure modes are user-visible and non-silent.

## Phase D2 — Cross-session completion inbox
- Add per-user completion inbox independent of active chat tab.
- Show unread completion notices in any active session.
- Maintain canonical transcript in originating session.

**Exit criteria**
- A task completed in session A is visible when user is in session B.
- Users can clear/read notices without losing source transcript.

## Phase D3 — Telegram continuity integration
- Map Telegram user/chat identity to session identity.
- Continue same conversation by default.
- Reset only on explicit `/start`.

**Exit criteria**
- Conversation continuity is deterministic across desktop/mobile.
- `/start` reliably rotates active session pointer.

---

## 4) Reflection-Driven Specialist Expansion

## Phase R1 — Reflection suggestions only
- Reflection layer can suggest specialist types and rationale.
- Human/operator-visible suggestion list in Mission Control.

## Phase R2 — Templated specialist spawn
- Spawn from approved templates only (no free-form generation).
- Attach explicit scope + capability profile on spawn.

## Phase R3 — Controlled dynamic specialists
- Allow dynamic specialist creation with review gate.
- Require provenance, retirement policy, and rollback path.

**Guardrails for all phases**
- No direct worker-to-user speaking channel.
- Coordinator (Weebo) owns final user communication.
- Specialist creation is auditable and reversible.

---

## 5) Coder Agent Safety/Quality Policy

Default coder profile should:
- Read/write project workspace.
- Run local tests/linters/build checks.
- Prefer surgical code edits over whole-file replacement when modifying existing logic.

Practical implementation detail:
- Use surgical replacement strategies (e.g., match-and-replace target node patterns) where tooling supports it.

Review flow options:
- Start with existing review/validation process as source-of-truth.
- Add reviewer/auditor agents as orchestration roles, not parallel truth systems.

---

## 6) Mission Control Evolution Plan

## Phase M1 — Delegation visibility
- Add delegated-task cards and summary counters.
- Show worker type, scope type, and current state.

## Phase M2 — Agent topology view
- Surface active coordinator/worker relationships.
- Show handoff edges and completion/failure reasons.

## Phase M3 — Reflection & specialist management
- Show reflection-suggested specialist proposals.
- Approve/reject lifecycle actions from UI.

Success metrics to track:
- Mean time-to-diagnose failures.
- Manual retry rate.
- Delegated-task completion latency.
- Failed delegation recovery rate.

---

## 7) Stabilization & Release Gates (All Tracks)

Every phase ships with:
1. Unit tests for new command and lifecycle logic.
2. Integration tests for cross-component handoffs.
3. Smoke checks for core user-facing flow.
4. Failure-mode tests (orchestrator unavailable, missing deps, partial outages).

Recommended lane policy:
- PR gate: unit lane.
- Nightly: integration lane.
- Pre-release: smoke + integration.

PR8 release-gate completion evidence should include all of the following in CI or PR notes:
- Reflection -> specialist lifecycle handoff integration coverage.
- Session continuity integration coverage across simulated channels.
- Mission Control operator smoke coverage (snapshot + specialist lifecycle actions).
- Failure-mode coverage for unavailable orchestrator/task manager and partial optional dependency states.

---

## 8) Quarter-Style Milestone Plan

### Milestone A (now → near term) — ✅ complete
- D1 hardening complete.
- Master roadmap adopted.
- Scope model constants/contracts drafted and enforced.

### Milestone B — ✅ complete
- D2 cross-session inbox.
- M1 delegation visibility in Mission Control.
- R1 reflection suggestions surfaced.

### Milestone C — ✅ complete
- D3 Telegram continuity + `/start` reset semantics.
- R2 templated specialist spawning.
- M2 agent topology panel.

### Milestone D — 🟡 in progress
- R3 controlled dynamic specialists (review-gated proposal flow complete; continue operationalization).
- M3 specialist lifecycle management (core flows present; continue polish/ops instrumentation).
- Production-quality SLO dashboarding (baseline API endpoint + archived-task latency/recovery instrumentation added; continue with retry/MTTD instrumentation and UI visualization).

---

## 9) Definition of Done (Program-Level)

A roadmap phase is complete only when:
- Product behavior is documented.
- Automated tests for happy + failure paths exist and pass.
- Mission Control shows relevant operational signals.
- Rollback path is explicit.
- User-facing command/help text is updated.

