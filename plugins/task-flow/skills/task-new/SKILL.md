---
name: task-new
description: Start a task-flow task: create .work/<id>/task.md and status.md from a description or ticket text.
argument-hint: "<description or ticket text>"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Follow **Phase: new**. Request: `$ARGUMENTS`

Stop after writing the files. Show the task id and the normalized goal, and say that `/task-flow:task-plan <id>` is next.
