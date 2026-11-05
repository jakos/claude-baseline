---
name: task-run
description: Run a task end to end with task-flow: new, plan, approval gate, a baseline check, then up to 3 rounds of implement, check and review.
argument-hint: "<description | task-id> [--auto] [--planner <profile>]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Arguments: `$ARGUMENTS`

- If the first argument names an existing `.work/<id>/`, resume that task from the phase in its `status.md`.
- Otherwise it is a new request: follow **Phase: new** with everything except `--auto`.
- `--auto` skips only the plan approval question, and only when the user typed it. It never permits an early stop, push, PR or deploy.
- `--planner <profile>` plans a new task (or a task still in phase `new`) with that profile for this invocation only, instead of the configured `[roles] planner`; it must name an enabled configured profile of models.toml. On resume without the flag, the current configuration applies — history is evidence, not a sticky selection — and an existing valid `plan.md` of a `planned`/`approved` task is never silently replanned.

Then follow **The loop**. Update `status.md` after every phase. Stop only at: the approval gate (unless `--auto`), `BLOCKED` from the planner or implementer, `review-approved`, no progress (escalation), or the round cap (escalation after round 3). Never stop on your own judgment that the run cannot pass. A failed external implementer round (exit 1 or 2 of `implement.py`) is not a stop: the same command resumes that round.
