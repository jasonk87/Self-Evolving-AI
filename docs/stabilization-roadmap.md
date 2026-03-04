# Stabilization Roadmap
> Consolidated planning is tracked in `docs/master-roadmap.md`.

This roadmap defines how to keep delivery speed high while reducing regressions.

## 1) Test lanes

Use three lanes with explicit purpose:

- **Unit lane (default in CI on every push)**
  - Goal: fast, deterministic signal.
  - Command:
    ```bash
    pytest -q -m "not integration and not smoke"
    ```

- **Integration lane (scheduled or pre-release)**
  - Goal: validate optional dependencies and environment-sensitive code paths.
  - Command:
    ```bash
    pytest -q -m "integration"
    ```

- **Smoke lane (critical-path health checks)**
  - Goal: quickly verify that high-level flows are still wired correctly.
  - Command:
    ```bash
    pytest -q -m "smoke"
    ```

## 2) Generated-tool governance

For tool generation workflows, enforce a staged pipeline:

1. Redundancy check against existing tool registry.
2. Generation attempt with deterministic parsing.
3. Review/approval gate (Council or fallback reviewer).
4. Persist + register + post-save tests.

Keep each stage unit-testable and avoid side effects in tests (write to temp dirs).

## 3) Config hardening for release

Before production deployment:

- Disable debug/verbose LLM logs in production.
- Require secure `SECRET_KEY` (no default fallback in prod).
- Keep API keys env-only.

## 4) Sandbox executor hardening

- Keep Docker sandboxing opt-in for local determinism.
- Continue splitting executor internals into testable helpers:
  - input validation
  - execution strategy selection
  - process invocation
  - output harvesting

## 5) Optional dependency boundaries

- Lazy-import optional generated tools.
- Return clear diagnostics when optional deps are missing.
- Prefer graceful skip/fallback over import-time crashes.


## 6) Phase completion checklist (Release-Gate Completion Pack)

Use this checklist before marking a roadmap phase complete:

- [ ] **Unit coverage** for newly introduced policy/lifecycle logic.
- [ ] **Integration coverage** for cross-component handoff paths (e.g., reflection -> specialist spawn, identity continuity APIs).
- [ ] **Smoke coverage** for core Mission Control operator actions (snapshot + lifecycle actions).
- [ ] **Failure-mode coverage** for degraded runtime states (orchestrator/task-manager unavailable, partial optional deps).
- [ ] **DoD evidence captured** in PR notes with exact test commands and outcomes.

Recommended evidence commands:

```bash
pytest -q tests/test_release_gate_pr8_integration.py -m integration
pytest -q tests/test_release_gate_pr8_smoke.py -m smoke
pytest -q tests/test_mission_control_status_api.py -k "reflection_spawn_specialist_endpoint_returns_503_when_task_manager_unavailable or health_audit_reports_partial_optional_dependency_availability"
```
