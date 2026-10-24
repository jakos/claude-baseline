---
name: task-autopilot
description: Gather requirements from you or the repository, plan them as task-flow tasks (bootstrapping a thin repository first), show the tasks and token estimates, then run them one after another, semi- or fully automatic, with a circuit breaker between tasks.
argument-hint: "[<requirements> | <run-id>] [--from-repo] [--semi | --full] [--budget <tokens>] [--auto]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Arguments: `$ARGUMENTS`

- If the first argument names an existing `.work/_autopilot/<run-id>/`, resume that run from the state in its `run.md`.
- Otherwise it is a new run: the argument text (if any) is the requirements. `--semi` is the default; `--full` keeps going past failed tasks.
- `--auto` skips only the start question (start / edit / cancel), and only when the user typed it. It never permits an early stop, push, PR or deploy.

Follow **Autopilot** in orchestration.md. Obey `autopilot.py next` exactly between tasks. Never push, open a PR, deploy or remove a worktree, and never make a commit except the initial commit the user chose in Preconditions (each task's own finish commit is the task loop's).
