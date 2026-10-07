---
name: task-run
description: Run a task end to end with task-flow: new, plan, approval gate, then up to 3 rounds of implement, check and review.
argument-hint: "<description | task-id> [--auto]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Arguments: `$ARGUMENTS`

- If the first argument names an existing `.work/<id>/`, resume that task from the phase in its `status.md`.
- Otherwise it is a new request: follow **Phase: new** with everything except `--auto`.
- `--auto` skips only the plan approval question, and only when the user typed it. It never permits push, PR or deploy.

Then follow **The loop**. Update `status.md` after every phase. Stop at the approval gate, at a blocked plan or implementer, at escalation after round 3, or at `review-approved`.
