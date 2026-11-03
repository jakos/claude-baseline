---
name: task-epic
description: Create a persistent epic (one id for a group of tasks with declared dependencies), mutate its membership while idle, run or resume it through the ordinary autopilot loop, and inspect its status and report; retry of a failed member needs your explicit authorization.
argument-hint: "<new <epic-id> --title <title> --goal <goal> --base <branch> [--tasks <id>[,<id>...]] | add <epic-id> --tasks <id>[,<id>...] [--depends-on <row-id>|-] | remove <epic-id> --tasks <id>[,<id>...] | run <epic-id> | status <epic-id> | retry <epic-id> <task-id> --authorized | sync-run <epic-id>> [--full | --semi] [--budget <tokens>] [--auto]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` (section **Epics —
/task-flow:task-epic**) and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They
are the procedure; this command only says which part to run.

Arguments: `$ARGUMENTS`

- `new <epic-id> --title <title> --goal <goal> --base <branch> [--tasks <id>[,<id>...]]`: create the
  epic. The epic id must not match any existing `.work/<id>/` task folder (the script
  refuses the collision; Phase: new step 3 applies the same rule in the other direction).
  `--base` names the branch the epic's root tasks start from. With `--tasks`, the
  existing eligible tasks are grouped in the given order as a linear chain. Without
  `--tasks`, the epic starts empty: stage the decomposition in an ordinary autopilot
  planning folder, run the backlog agent, show the decomposition for approval, create
  the ordinary task folders, then attach them with `add`. No creation path starts
  implementation.
- `add <epic-id> --tasks <id>[,<id>...] [--depends-on <row-id>|-]`: append existing tasks
  while the epic is idle (default parent: the current last row). While a run is linked,
  `add` also regenerates that run's `## Tasks` table from the epic graph (see `sync-run`).
- `remove <epic-id> --tasks <id>[,<id>...]`: remove rows; a started task or a dangling
  dependency is refused. Like `add`, it regenerates the linked run's table while idle.
- `run <epic-id>`: create or resume the epic's autopilot run (`.work/_autopilot/<run-id>/`
  with `source: epic` and `epic: <epic-id>`), linked with
  `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/epics.py" link . <epic-id> <run-id>`,
  then follow **Autopilot** in orchestration.md unchanged — same role resolution,
  estimates, overview/start gate, task loop and circuit breaker. `--auto` skips only the
  start question.
- `status <epic-id>`: the deterministic status report (phases, rounds, verdicts, risk,
  tokens/USD, blocker chains, next actions, Mermaid diagram).
- `retry <epic-id> <task-id> --authorized`: archive the failed member's round evidence
  and spend snapshot and record the attempt; only with `--authorized` also reset the
  task's `status.md` for a new attempt at round 1.
- `sync-run <epic-id>`: regenerate the linked run's `run.md` `## Tasks` table from the
  epic graph after a hand edit of `epic.md` — existing rows keep their `Tokens used` /
  `External USD` cells and the header fields, new rows start at `0`, and the run state
  returns to `planning` so the estimate, overview and start gate run again.

All script calls run from the repository root, for example:

```
uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/epics.py" new . <epic-id> --title <title> --goal <goal> --base <branch> [--tasks <ids>]
uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/epics.py" add . <epic-id> --tasks <ids> [--depends-on <row-id>]
uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/epics.py" remove . <epic-id> --tasks <ids>
uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/epics.py" sync-run . <epic-id>
uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/epics.py" status . <epic-id>
uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/epics.py" retry . <epic-id> <task-id> --authorized
```

Show script output verbatim; on exit 2 show its stderr and change nothing. An epic never
has a `status.md` and is never listed or picked as a task. Retry of a failed member task
is offered through the epic (`retry <epic-id> <task-id> --authorized`): it archives the
prior round evidence and a spend snapshot under `.work/_epics/<epic-id>/attempts/`,
records the previous round count in the epic's History, and — only with the user's
explicit `--authorized` — resets the task's `status.md` (phase `approved`, round 0) so
`/task-flow:task-run <task-id>` starts a new attempt at round 1; the 3-round cap and
no-progress rule apply per authorized attempt. `--auto` never grants the reset. Without
`--authorized` the retry refuses and changes nothing. Epics grant no new permissions: no
push, PR, merge, deploy or worktree removal, and delivery preferences stay intent until
`agent-pr-path` merges.
