---
name: task-plan
description: Plan a task-flow task with the planner agent, then ask the user to approve, edit or cancel the plan.
argument-hint: "[task-id]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Follow **Phase: plan** for task `$ARGUMENTS`, including the approval gate. Never record approval the user did not give.
