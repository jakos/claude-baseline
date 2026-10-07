---
name: task-check
description: Run the automated checks for the current task-flow round with the checker agent.
argument-hint: "[task-id]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Follow **Phase: check** for task `$ARGUMENTS`, for the current round. Show the resulting table.
