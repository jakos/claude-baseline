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
  checks-<n>.md    automated check results for round n
  review-<n>.md    reviewer verdict for round n
  summary.md       final summary, when done or escalated
  worktree/        the task's git worktree, when worktrees are used
```

Source changes happen **in the worktree** when `status.md` names one, otherwise in the
repository root on the task branch. `.work/` files are always read and written at the
repository root, never inside the worktree.

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
  the worktree root. Project config wins over profile defaults.
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
`Not run`, not FAIL.

## review-<n>.md

```markdown
# Review: round <n>
## Acceptance criteria
| AC | Result | Evidence |
| AC1 | PASS | tests/test_retry.py::test_gives_up_after_three — passed in checks-2 |
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
FAIL in `checks-<n>.md` or any FAIL criterion means `CHANGES_REQUESTED`. Unexplained
out-of-scope changes are blocking.

## Rounds and escalation

- A round is implement → check → review. **At most 3 rounds.**
- In round n > 1 the implementer fixes only the Fix list of `review-<n-1>.md`.
- After round 3 without `APPROVED`: stop, write `summary.md` with what passed, what still
  fails and why, and hand back to the human. Never start a 4th round on your own.
- An implementer that finds the plan wrong stops and appends `BLOCKED: <problem>` to
  `status.md`. The orchestrator returns to the human, not to the planner on its own.

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
