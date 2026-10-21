---
name: task-finish
description: Finish an approved task-flow task: write summary.md, propose commit message and PR text, commit locally, ask before push, PR or worktree removal, then prune .work/<id>/ to its Markdown files.
argument-hint: "[task-id]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Follow **Phase: finish** for task `$ARGUMENTS`. Ask separately before each step that leaves the machine (push, PR) and before removing the worktree. The final prune of `.work/<id>/` to its `.md` files is the one deletion that is not asked.
