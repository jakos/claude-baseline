---
name: task-plan
description: Plan a task-flow task with the planner agent, then ask the user to approve, edit or cancel the plan.
argument-hint: "[task-id] [--planner <profile>]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Follow **Phase: plan** for task `$ARGUMENTS`, including the approval gate. Never record approval the user did not give.

- `--planner <profile>` plans with that profile for this invocation only, instead of the
  configured `[roles] planner`; it must name an enabled configured profile of
  models.toml — never a free-form model or command (see **Phase: plan** step 0).
