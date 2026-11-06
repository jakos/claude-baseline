---
name: task-review
description: Run the independent reviewer on the current task-flow round.
argument-hint: "[task-id]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Follow **Phase: review** for task `$ARGUMENTS`, for the current round. Refuse at round 0: it is the baseline and has no reviewer, so tell the user to run `/task-flow:task-implement`. Refuse if `checks-<n>.md` for this round does not exist yet — the reviewer judges with check results, not without them. Show the verdict and fix list.

Phase: review resolves the configured reviewer: Claude uses its existing subagent/model override; an external profile uses role_run.py with --role reviewer and --round. Refused or invalid external output leaves phase and verdict unchanged; never silently fall back to Claude.
