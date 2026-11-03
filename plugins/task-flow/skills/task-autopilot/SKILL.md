---
name: task-autopilot
description: Gather requirements from you or the repository, plan them as task-flow tasks (bootstrapping a thin repository first), show the tasks and token estimates, then run them one after another, semi- or fully automatic, with a circuit breaker between tasks; with --tasks, run prepared existing tasks stacked in the given order instead.
argument-hint: "[<requirements> | <run-id> | --tasks <id>[,<id>...]] [--from-repo] [--semi | --full] [--budget <tokens>] [--auto]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Arguments: `$ARGUMENTS`

- If the first argument names an existing `.work/_autopilot/<run-id>/`, resume that run from the state in its `run.md` — including a `source: epic` run, which stays linked to its epic id (see "Epics — /task-flow:task-epic" in orchestration.md).
- Otherwise it is a new run: the argument text (if any) is the requirements. `--semi` is the default; `--full` keeps going past failed tasks.
- `--tasks <id>[,<id>...]` runs existing tasks from `.work/` stacked in that order (see "Explicit task list (`--tasks`)" in orchestration.md); it cannot be combined with requirement text or `--from-repo`.
- `--auto` skips only the start question (start / edit / cancel), and only when the user typed it. It never permits an early stop, push, PR or deploy.

Follow **Autopilot** in orchestration.md. Obey `autopilot.py next` exactly between tasks. A failed external implementer round (exit 1 or 2 of `implement.py`) pauses the run, also with `--auto`; `/task-flow:task-autopilot <run-id>` resumes it. Never push, open a PR, deploy or remove a worktree, and never make a commit except the initial commit the user chose in Preconditions (each task's own finish commit is the task loop's).
