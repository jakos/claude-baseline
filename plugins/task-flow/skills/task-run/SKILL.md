---
name: task-run
description: Run a task end to end with task-flow: new, plan, approval gate, a baseline check, then up to 3 rounds of implement, check and review.
argument-hint: "<description | task-id> [--auto]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Arguments: `$ARGUMENTS`

- If the first argument names an existing `.work/<id>/`, resume that task from the phase in its `status.md`.
- Otherwise it is a new request: follow **Phase: new** with everything except `--auto`.
- `--auto` skips only the plan approval question, and only when the user typed it. It never permits an early stop, push, PR or deploy.

Then follow **The loop**. Update `status.md` after every phase. Stop only at: the approval gate (unless `--auto`), `BLOCKED` from the planner or implementer, `review-approved`, no progress (escalation), or the round cap (escalation after round 3). Never stop on your own judgment that the run cannot pass.
