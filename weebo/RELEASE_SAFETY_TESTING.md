# Release safety fixes and acceptance record

Base: `33be7ca` (including its time-independent quiet-hours tests). Scope: Weebo only; no keys, existing model
settings, Weebo 1.x, user database or running application changed. Direct user-to-bot budget exemption is unchanged.

## Implemented

- Outward bot requests handled by Weebo require a one-use user approval bound to the complete request, not just
  the tool name. Old always-allow rules are ignored for sends, spending, contacting people and non-read connector
  requests. An approval for one recipient/body/command cannot authorize another. Read rules still work.
  Approval hashes include full arguments even when the display preview is capped. Network/permission escalations
  also need a fresh approval; bots do not inherit the chief's unrestricted Full-mode network sandbox.
- Native bot/owned-agent startup reads effective config, disables unassigned MCP servers and apps, and allows only
  declared read tools on assigned connectors. It reapplies controls on resume, replaces old-policy bot threads,
  and fails closed if controls are missing/rejected. Native outward tools are deliberately unavailable for now.
- Connector stdio children receive basic OS launch variables (PATH, Windows system/command paths, temporary paths,
  locale) plus exact manifest `env_names`, in onboarding and normal Codex runtime. No credential values are written
  to manifests or logs. The runtime launcher is the same filter as the test client, not a shell command string.
- Briefs and Ideas/drafts are buffered before publication, including deltas, commentary, history and completion
  notifications. Checks finish before release; mismatch/unverified warnings precede the answer. Stop discards it.
  Normal chat remains streaming, with advisory checks afterwards. The existing second-pass setting and budget apply.
- Twilio acceptance is not delivery: the scheduler polls the returned message ID after 60 seconds, up to five times.
  Pending checks are durable. Delivered/failure/unverified outcomes go to the chief's chat and journal, without a
  bot notification or automatic resend. Credentials/account mismatch and lookup errors end as unverified, not sent.

## Automated checks

`tests/weebo/test_release_safety.py` covers per-action consent and argument changes, old rules, actor restrictions,
environment filtering (including a real subprocess), manifest validation, native allowlists/resume, unsupported
Codex refusal, schema requirements, buffered publication, Stop, failed checks, normal streaming and bounded SMS reads.
`test_weebo3_ui.py::test_publication_gate_and_per_action_card_in_browser` reloads the page while review is pending,
checks the final warning and verifies that outward approval cards show details and have no Always allow button.
Quiet hours are pinned in browser tests, not inferred from the machine's wall clock.

Existing tests were adjusted only for intentionally changed contracts: outward user rules are now one-use/exact,
native subprocess execution goes through the environment-filtering launcher, and rejected connector config must
not trigger an unprotected retry. Their original safety assertions are retained or strengthened.

Required before push:

```powershell
python -m weebo --selftest
python -m pytest tests/weebo -q -p no:cacheprovider
$env:PYTHONPATH='.'
python -m pytest -q -m 'not integration and not smoke' -p no:cacheprovider
```

Also parse every `weebo/web/js/*.js` with `weebo.evolution.gates.check_js`.

Final local verification on Windows / Python 3.14, 2026-10-17:

- Selftest: **OK**.
- Weebo suite: **792 passed, 1 existing platform skip**.
- Repository unit lane: **1,553 passed, 2 existing platform skips, 7 integration/smoke tests deselected**;
  all 13 subtests passed. The skips are unavailable symlink support and `notify-send`.
- JavaScript: **all 13 modules parse**.
- Installed-Codex synthetic isolation probe: **PASS** (details below).

The only warning is ChromaDB's upstream use of deprecated `asyncio.iscoroutinefunction` on Python 3.14.
These results cover the final production code and regression tests; only this verification record was updated
afterwards. CI and the unchecked live acceptance items below are separate, unverified results.

## Local Codex diagnostic

```powershell
python -m weebo.connectors.check_isolation --probe
```

The script selects the installed Codex, regenerates its own protocol schema, then starts a disposable app-server
with only two harmless synthetic MCP connectors. It uses the production per-thread configuration builder. No
credentials/config are copied from the user's Codex home, no model turn runs, and no real connector action occurs.
It verifies allowed-tool visibility and a successful harmless read, blocked-tool invisibility and rejection of a blocked direct call. Exit 0
means the synthetic checks passed; exit 2 is unsupported/inconclusive/failure, never a pass.

**Observed locally, 2026-10-17:** Codex **0.163.0**, required schema fields present; synthetic isolation **PASS**;
allowed tools visible, blocked tools hidden, blocked call rejected. This is NOT a real-app approval certification.

## Live acceptance still required before permitting native outward tools

- [ ] With the user's real connector configuration, verify two bots see only their assigned read tools, including
  after restart, connector revocation and continued owned-agent work. Include apps with existing per-tool overrides.
- [ ] Using a dedicated test connector/recipient, prove that an outward call pauses **before** its side effect;
  Deny produces zero effects, Allow once produces exactly one, and the next call asks again. Include direct bot
  chat, delegated agents and resumed threads. Do not test by emailing/calling a real person without explicit consent.
- [ ] Verify command/sandbox/permission escalation cannot bypass this rule. Command-name heuristics and tool risk
  labels are not a security boundary. Trusted connector code still runs with the user's filesystem permissions.
- [ ] If these fail, keep native outward tools disabled. Consider separate connector-only Codex environments as
  a separately reviewed change; this patch does not silently enable them or install an OS sandbox.
- [ ] Optional authorized Twilio smoke: send exactly one message to the user's chosen test number and confirm the
  later delivery record matches Twilio. Do not resend when status remains unknown. Automated tests use fake HTTP.
- [ ] Real-source brief: move/cancel a test event during generation, reload while checking, and verify a corrected
  or explicitly flagged output. A model checker is not proof of comprehensive factual coverage.

References used for protocol checks: [Codex app-server](https://developers.openai.com/codex/app-server),
[config reference](https://developers.openai.com/codex/config-reference), and
[Twilio Message resource](https://www.twilio.com/docs/messaging/api/message-resource). The installed binary's schema,
not a guessed API field, determines support.
