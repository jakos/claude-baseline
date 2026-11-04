---
name: workflow
description: Shared contract for task-flow agents — the .work/<task-id>/ layout, file formats, round limit and escalation rules. Read by task-flow's planner, implementer, checker and reviewer; not for general use.
disable-model-invocation: true
user-invocable: false
---

# task-flow: the shared contract

Every task-flow agent reads this first. **Files are the interface.** No agent relies on
chat history; everything another role needs is written to `.work/<task-id>/`.

## Layout

```
.work/<task-id>/
  task.md          original request, normalized: Goal, Context, Constraints
  plan.md          planner output, format below
  status.md        phase, round, base, branch, worktree, approval, history
  checks-0.md      baseline check results on the untouched base, before round 1
  checks-<n>.md    automated check results for round n (1..3)
  review-<n>.md    reviewer verdict for round n
  impl-<n>.json    implementer metrics for round n — written by scripts/implement.py
                   for an external profile, and by the orchestrator for a Claude round
                   when records are enabled (see Task records below); an external round
                   may carry a `cli` section (driver "cli"), and `cost_usd`/`tokens` are
                   null when the cost or usage is unknown (e.g. a CLI subscription) —
                   unknown, never zero
  impl-<n>.progress.jsonl  per-turn trail of an external round (per parsed CLI event or
                   heartbeat record for driver "cli"); scratch, deleted at /task-finish
  role-<role>-<invocation-id>.json  metrics of one external producer-role invocation
                   (the planner today), written by scripts/role_run.py next to the
                   artifact; kept at /task-finish like impl-*.json, with `cost_usd`/
                   `tokens` null when unknown — unknown, never zero
  summary.md       final summary, when done or escalated
  worktree/        the task's git worktree, when worktrees are used
```

Autopilot runs live in `.work/_autopilot/<run-id>/` (`requirements.md`, `run.md`,
`report.md`, plus `backlog.md` only for decomposed runs); their tasks are ordinary
`.work/<task-id>/` folders. Benchmark runs live in `.work/_bench/<run-id>/`; their task
folders are scratch copies under `runs/<n>/.work/`, not real tasks.

Epics live in `.work/_epics/<epic-id>/` (`epic.md`, plus `report.md` and
`attempts/<task-id>/attempt-<k>/` once written); an epic is **not** a task: it never has
a `status.md`, is never listed or picked by `task-list`, and never enters the
`.work/<task-id>/` namespace. `.work/_epics/` is read and written at the repository root
like the other `.work/` artifacts.

When an external profile implements (`[roles] implementer` in models.toml), the script
`scripts/implement.py`, not an agent, writes the `round <n> implementer:` and `BLOCKED:`
Log lines in `status.md`. When an external profile plans (`[roles] planner`, or a
`--planner <profile>` override of one invocation), the script `scripts/role_run.py`
runs the same agent body through a read-only transport adapter, validates the returned
artifact against the plan.md contract below (`scripts/role_contracts.py`), and publishes
only the validated `plan.md` — the orchestrator alone updates `status.md` afterwards.

After `/task-finish` only the `.md` files, the `impl-*.json` files, the `role-*.json`
files and `worktree/` (if
kept) remain; any other file or directory in the folder is scratch and is deleted.

Source changes happen **in the worktree** when `status.md` names one, otherwise in the
repository root on the task branch. `.work/` files are always read and written at the
repository root, never inside the worktree.

## epics

An epic gives a group of tasks one persistent id that exists before execution, runs or
resumes through the ordinary autopilot loop (`source: epic` in the run's `run.md`, linked
by the epic's `active-run:` field), and is inspected with `scripts/epics.py status`. The
strict `epic.md` contract (UTF-8, LF, atomic replacement; every refusal leaves the existing
bytes unchanged):

```markdown
# Epic: <epic-id>
- title: <one line>
- goal: <one line>
- base: <branch the root tasks start from>
- state: pending | active | paused | completed
- delivery: per-task | epic
- active-run: none | <autopilot run-id>
## Tasks
| # | Task | Depends on |
| --- | --- | --- |
| 1 | first-task | - |
| 2 | second-task | first-task |
## History
- <ISO timestamp> <event> — <one line>
```

- Exactly the six header fields above, in that order; duplicate or unknown fields are
  refused. Exactly the `## Tasks` and `## History` sections, in that order.
- Task rows are numbered from 1; `Depends on` is `-` or the id of one **earlier** row —
  at most one parent (joining branches needs a separate integration design). Forward,
  dangling and cyclic dependencies and multiple parents are refused.
- A row must name an existing ordinary task; epic ids and task ids never collide in
  either creation direction, and a task belongs to at most one non-completed epic
  (a completed epic's history survives later permitted membership).
- `state` is derived: `completed` when every member is `done`, `active` while the linked
  run is running, `paused` when the linked run is paused or stopped or a member failed,
  else `pending`; adding a task to a completed epic reopens it as `pending`.
- `delivery` is intent only until `agent-pr-path` merges: epics never push, open PRs,
  merge or make an aggregate integration commit; a branched forest reports all delivery
  branches.
- `new`/`add`/`remove` mutate membership only while the epic is idle (active execution
  freezes membership); a started task is never removed and no dangling edge is left.
  While a run is linked, an idle `add`/`remove` — or `epics.py sync-run` after a hand edit
  of `epic.md` — regenerates the linked run's `## Tasks` table from the epic graph:
  existing rows keep their `Tokens used` / `External USD` cells and the header fields,
  new rows start at `0`, and the run state returns to `planning` so the overview and the
  start gate run again.
- A retry offered through the epic archives the failed task's round evidence and a spend
  snapshot under `attempts/<task-id>/attempt-<k>/` before round filenames are reused (task
  pruning only prunes `.work/<task-id>/`, so the archive survives), records the attempt and
  the previous round count in History, and — only with the user's explicit authorization —
  resets the task's `status.md` (phase `approved`, round 0) so the next
  `/task-flow:task-run` starts a new attempt at round 1; `--auto` never grants it.

## plan.md

The planner writes exactly these headings, in this order:

```markdown
# Plan: <title>
## Goal
## Context
## Profiles
## Steps
## Acceptance criteria
## Verification commands
## Project rules
## Out of scope
## Risks / open questions
```

- **Profiles**: one line per profile — `python: src/payments/*.py, pyproject.toml`.
  Values: `python`, `docs`, `config`, `generic`.
- **Steps**: numbered. Each: what, files, profile.
- **Acceptance criteria**: numbered `AC1`, `AC2`, … Each is testable and ends with
  `Verify:` naming a test, a command, or `manual: <what to look at>`.
- **Verification commands**: exact commands, one per line in a code block, to run from
  the worktree root. Project config wins over profile defaults. Each non-blank line that
  does not start with `#` is one command: one program plus its arguments, no shell.
  Quotes group (`"a|b"` stays one argument), a backslash is literal (Windows paths work
  unchanged), and unquoted `| & ; < > ( ) $ * ?` or backticks, or a `NAME=value` first
  word, are refused as ERROR by `scripts/gates.py`. Every command to check must be in
  the list: gates.py runs no profile defaults.
- **Project rules**: the parts of `.claude/task-flow.md` that apply — architecture rules,
  forbidden paths. Copied here so later agents never need to re-read the config.
- **Risks / open questions**: if any question blocks planning, the first line is
  `BLOCKED: yes` and the plan stops there.

## checks-<n>.md

```markdown
# Checks: round <n>
| # | Command | Result | Exit | Duration |
| 1 | `uv run pytest -q` | FAIL | 1 | 4s |
## Failures
### 1. `uv run pytest -q`
<at most ~30 lines: the failing test names and the assertion or error lines. Never full logs.>
## Not run
<profile defaults skipped, and why: tool not installed, not configured in the repo>
```

`Result` is PASS, FAIL, or ERROR (could not run). A command missing from the repo is
`Not run`, not FAIL. The file is written by the checker agent or by `scripts/gates.py`
(when `[roles] checker = "script"`); the format is the same.

Round 0 uses the same format. It is run on the untouched base, before any implementation.

## review-<n>.md

```markdown
# Review: round <n>
## Acceptance criteria
| AC | Result | Evidence |
| AC1 | PASS | tests/test_retry.py::test_gives_up_after_three — passed in checks-2 |
## Pre-existing failures
<commands failing in checks-<n>.md that already fail in checks-0.md, with the matching
checks-0.md evidence; or `none`>
## Findings
1. [blocking] src/client.py:42 — <problem, and the input or state that breaks it>
2. [suggestion] …
## Scope
<changes outside plan.md's Steps, or "none">
## Verdict: APPROVED | CHANGES_REQUESTED
## Fix list
1. <one concrete instruction per blocking finding or failing AC>
```

Rules: evidence is a file:line, a test name, or command output — never "looks fine". Any
**introduced** FAIL or ERROR in `checks-<n>.md`, any FAIL criterion, or any blocking
finding means `CHANGES_REQUESTED`. Unexplained out-of-scope changes are blocking.

**Pre-existing failure**: a command with Result FAIL or ERROR in `checks-<n>.md` is
pre-existing when the same command is FAIL or ERROR in `checks-0.md` **and** every failing
test name or error line reported for it in `checks-<n>.md` also appears in `checks-0.md`.
Any new failing test or error line makes it an introduced failure. A pre-existing failure
does not by itself force `CHANGES_REQUESTED`; it blocks only when a Step or acceptance
criterion of the plan is about fixing it.

## summary.md

The finish summary has exactly these headings, in this order. The task-record helper
reads `## Goal` and `## Out of scope` from them when records are enabled, so a missing
section is a malformed summary, never silently skipped:

```markdown
# Summary: <id>
## Goal
## Changes
## Verification
## Rounds
## Out of scope
```

## Task records (opt-in, finish-time)

A repository opts in with `.claude/task-flow.records.toml` in the selected worktree (or
the repository root when `worktree: none`) — see `templates/records.toml`:

```toml
[records]
enabled = true
```

A missing file or `enabled = false` keeps the old finish behaviour: no record, no
trailers, no new required artifacts, no extra approval gate. When enabled,
`/task-flow:task-finish` runs `scripts/task_record.py render` (writes
`docs/tasks/<id>.md` in the worktree) and `trailers` (prints the commit trailer block)
after `summary.md` and before the commit; any helper failure stops finish before the
commit.

The sources are deterministic and are all read at the repository root's
`.work/<id>/` — never a model rewrite:

- task id, round count and approval source: `status.md` (`# Status:`, `- round:`,
  `- plan-approved:`);
- goal and omitted scope: the `## Goal` and `## Out of scope` sections of `summary.md`;
- criteria text: the `## Acceptance criteria` section of `plan.md` (`AC<n>: … Verify: …`);
- AC results and per-round verdicts: the `## Acceptance criteria` table and
  `## Verdict:` of every `review-<n>.md` for rounds 1..round;
- implementer profile/model per round: `impl-<n>.json` (`profile`, `model`; external
  rounds are written by `scripts/implement.py`, Claude rounds by the orchestrator as
  `{"kind": "claude", "profile": "claude", "model": "<model>"}` at implement time when
  records are enabled). A missing or empty value is recorded as `unknown`, never
  guessed from configuration.

The record is one bounded, schema-versioned (`task-record/1`), UTF-8/LF
`docs/tasks/<id>.md` with no timestamps and no absolute paths; all exported prose is
scrubbed of file paths, credential URLs, tokens, private keys, secret assignments and
attribution/session markers, with visible truncation at fixed limits. The trailers are
`Task-Id`, `Task-Rounds`, repeated `Task-Implementer: <profile>/<model>` for the
distinct executed pairs, and `Task-Plan-Approved: user|auto`; `Task-Risk` is not
emitted until a merged risk schema exists. Regeneration is byte-identical; a
conflicting existing record, an unsafe destination or a malformed artifact is refused
without partial output. The helper never commits, pushes, opens PRs, contacts models
or prunes.

## Rounds and escalation

- Round 0 is the baseline check only: the checker runs the plan's verification commands on
  the untouched base and writes `checks-0.md`. No implementer, no reviewer. Failures there
  do not stop the run.
- A round is implement → check → review. **At most 3 rounds.**
- In round n > 1 the implementer fixes only the Fix list of `review-<n-1>.md`.
- **No progress**: the failing set of round n is the **introduced** commands (Result FAIL
  or ERROR in `checks-<n>.md` and not pre-existing, see above; pre-existing ones are
  excluded) plus the AC ids with Result FAIL in `review-<n>.md`. After review n, if
  n >= 2, the verdict is `CHANGES_REQUESTED`, and the failing set is **non-empty** and
  **equal** to that of round n-1, stop and escalate like the round cap, instead of using
  the remaining rounds. This is a mechanical comparison of the two rounds' tables, not a
  judgment. An empty set (blocking findings only) never triggers it.
- After round 3 without `APPROVED`, or on no progress: stop, write `summary.md` with the
  stop reason (for no progress, also the repeated failing set), what passed, what still fails and why, and hand back to the human. Never
  start a 4th round on your own.
- An implementer that finds the plan wrong stops and appends `BLOCKED: <problem>` to
  `status.md`. The orchestrator returns to the human, not to the planner on its own.

### Stops

A run stops at exactly these five points. `--auto` skips the approval gate (1); the
other four apply identically with and without it:

1. the approval gate;
2. `BLOCKED` from an agent (planner or implementer);
3. `APPROVED`;
4. no progress;
5. the round cap (3).

These are the stops of one task's run. `task-autopilot` adds run-level stops that are
evaluated only between tasks (orchestration.md → Autopilot).

`/task-flow:task-release` is not a task run: its single approval gate is defined in
orchestration.md → Release, and the five stops above do not apply to it.

A failure or refusal of an external implementer ends the command like a failed
precondition: `status.md` is unchanged and the same command resumes the round. It is not a
stop and not a judgment that the run cannot pass.

**No agent, and not the orchestrator, may stop a run, skip a round or declare it hopeless
on its own judgment.** `--auto` changes only the approval gate.

## Profiles

Profile files live next to this one. Read each one named in `plan.md → Profiles`:

- `${CLAUDE_PLUGIN_ROOT}/skills/profile-python/SKILL.md`
- `${CLAUDE_PLUGIN_ROOT}/skills/profile-docs/SKILL.md`
- `${CLAUDE_PLUGIN_ROOT}/skills/profile-config/SKILL.md`
- `${CLAUDE_PLUGIN_ROOT}/skills/profile-generic/SKILL.md`

Each has the same sections: Detect, Conventions, Verify, Review checklist. Use the section
that matches your role. **Project rules in `plan.md` beat profile defaults.**

## Boundaries every agent keeps

A hook enforces these; do not try to work around a denial — record the problem instead.

- No push, no PR, no deploy, no `git commit`. Changes stay staged until `/task-finish`.
- Planner, checker and reviewer write only under `.work/`.
