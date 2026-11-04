# Task record: external-role-delegation

- schema: task-record/1
- task: external-role-delegation
- approval: user
- rounds: 3
- final-verdict: APPROVED
- risk: unavailable (no merged risk schema)

## Goal

Let `[roles]` in `models.toml` send the planner role to a non-Claude profile (API or `cli` driver) as one configuration line, with a validated artifact, unchanged workflow gates and explicit accounting. Backlog and reviewer follow in the separate task `external-review-backlog` (scope cut recorded in the plan).

## Acceptance criteria

- AC1: Existing no-config and claude/claude:<model> role resolution remains unchanged; planner accepts enabled profiles, backlog and reviewer are still refused with a clear message, checker alone accepts script, and unknown roles/profiles/keys fail with file/key diagnostics. — PASS
- AC2: The legacy scalar implementer and alternative [roles.implementer] default table resolve identically; user/project scalar-table replacement is deterministic and unknown future routing keys are refused. — PASS
- AC3: A fake CLI planner and each fake API driver produce a validated plan end to end through the configured role path; the CLI receives a verified read-only command, final output is captured by the host, and only the runner publishes the artifact. — PASS
- AC4: Planner API tools deny writes, mutating commands, arbitrary interpreters, shell syntax and path/symlink escapes, and existing implementer API/CLI tools still pass their regressions. — PASS
- AC5: Planner validation rejects list-prefixed, repeated or non-sequential ACs, missing Verify references and omitted inherited checks, accepts bare AC lines and a satisfied earlier-stack prerequisite, and distinguishes a genuine blocked artifact from an invalid response. — PASS
- AC6: Invalid output receives exactly one validation repair attempt; exhausted repairs, provider errors and disallowed fallback preserve prior artifacts and status, report a redacted failure and never fall back to Claude. — PASS
- AC7: --planner works on task-plan, task-run, task-autopilot and task-epic run, selects only configured enabled profiles, records successful selection in task History, remains invocation-scoped and preserves existing planned/approved artifacts on resume. — PASS
- AC8: Doctor renders all five stages with method, model and billing, and spends no model capacity. — PASS
- AC9: Per-role metrics count repair/fallback/failed attempts once, preserve nullable CLI usage/subscription cost, redact sentinel secrets and survive finish; run/task USD totals are idempotent across resume and external stage tokens appear separately in estimates. — PASS
- AC10: Documentation and diagrams describe the delivered capabilities, planner examples, overrides and measured/unknown accounting; the manual comparison protocol specifies frozen cases, a fixed implementer and hidden probes without claiming paid benchmark results. — PASS
- AC11: Both gate registries include every inherited test, including test_cli_driver.py, and both new role suites in matching order; only baseline failures may remain and no defaults, version numbers, changelogs or workflow gates are changed. — PASS

## Rounds

- round 1: CHANGES_REQUESTED — implementer mistral-large/mistral-large-4-0
- round 2: CHANGES_REQUESTED — implementer mistral-large/mistral-large-4-0
- round 3: APPROVED — implementer mistral-large/mistral-large-4-0

## Out of scope

Backlog and reviewer delegation (`external-review-backlog`), a designer role, size-based routing (`implementer-model-routing`). Reviewer suggestions left open: `--stack-manifest` is optional so the stack-prerequisite check cannot fire in a real run; role metrics are written as `ok` before `publish()`; the external planner is not yet mentioned in README.md, plugins/task-flow/README.md and docs/task-flow/profiles.md.
