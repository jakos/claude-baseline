---
name: task-status
description: Show phase, round and last verdict for one task-flow task, or for every task under .work/.
argument-hint: "[task-id]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

If `$ARGUMENTS` names a task, show its `status.md` header and the last 5 History lines. Otherwise list every `.work/*/status.md` as a table: id, phase, round, last verdict, last History line. Change nothing.
