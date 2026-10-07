---
name: task-implement
description: Implement an approved task-flow plan with the implementer agent, on the task's branch or worktree.
argument-hint: "[task-id]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Follow **Phase: implement** for task `$ARGUMENTS`. The round is the current round + 1, or the current round if its implementation never finished. For round 1, run **Phase: baseline** first if `checks-0.md` is missing. Refuse if the plan is not approved, or if 3 rounds are already used.
