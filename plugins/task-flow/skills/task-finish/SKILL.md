---
name: task-finish
description: Finish an approved task-flow task: write summary.md, propose commit message and PR text, commit locally, and ask before push, PR or worktree removal.
argument-hint: "[task-id]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Follow **Phase: finish** for task `$ARGUMENTS`. Ask separately before each step that leaves the machine or deletes anything.
