# Autopilot

Paths such as `scripts/…`, `skills/…`, `agents/…`, `templates/…` and `bench/…` are inside `plugins/task-flow/`.

`/task-flow:task-autopilot` takes a project from requirements to a stack of finished
tasks. Every task it creates is an ordinary task-flow task: same files, phases, agents,
3-round cap and within-task stops. The run itself lives in `.work/_autopilot/<run-id>/`
(`requirements.md`, `backlog.md`, `run.md`, `report.md`); that folder is not a task.

1. **Preconditions.** The autopilot refuses when `.claude/task-flow.md` sets `worktree: no`
   or `Finish → commit` to anything but `yes` (stacked tasks need a worktree and a commit
   each). It runs `scripts/autopilot.py inspect`. A repository **without any commit**
   cannot get a worktree: if the index is empty it asks whether to create an empty
   initial commit (asked even with `--auto`), otherwise it stops and tells you to make a
   commit first.
2. **Gather** requirements from you (a short interview, at most 3 messages), from the
   repository (`--from-repo`: README, CLAUDE.md, planning files, TODOs, each requirement
   cited with `file:line`), or both.
3. **Decompose** with the `backlog` agent into at most 10 ordered tasks, then create and
   plan each one with the ordinary planner.
4. **Thin repository and the bootstrap task.** A repository is thin when it has no commit,
   or every tracked file is README, LICENSE, `.gitignore`, `.claude/`, `CLAUDE.md` or
   similar and `CLAUDE.md` has at most 40 non-blank lines. A thin repository gets a
   bootstrap task first (`bootstrap-project`: toolchain, test and lint commands, a fuller
   `CLAUDE.md`); every later task depends on it. The bootstrap task is planned first, and
   every later task is planned against its `plan.md`, so it uses the toolchain and
   verification commands the bootstrap sets up. The bootstrap task may follow
   dev-baseline's `project-bootstrap` skill when that plugin is installed; task-flow does
   not require it.
5. **Overview and start gate, in both modes.** Before anything runs you see every task
   with its dependencies, the output of `autopilot.py estimate` (per task and in total, at
   one round and at three) and the budget. Then one question: start / edit / cancel.
   Start approves every plan in the batch. `--auto` skips only that question.
6. **Loop.** Each task runs the `task-run` loop unchanged, then finishes with a commit but
   no push, PR or worktree question. Each task is based on the previous finished task's
   branch, so later tasks see earlier work; the last finished branch contains everything.
   Nothing is pushed, opened as a PR, deployed or removed; `report.md` lists what is left
   to you.

Modes: `--semi` (default) pauses after a failed task (escalated or blocked) and asks you
to continue past it (its dependents are skipped) or stop. `--full` keeps going, skipping
the tasks that depend on a failed one. To retry a failed task, run
`/task-flow:task-run <id>` yourself, then resume with `task-autopilot <run-id>`.

**Running prepared tasks.** `/task-flow:task-autopilot --tasks <id>[,<id>...]` runs tasks
that already exist in `.work/` as a stacked series, for example
`/task-flow:task-autopilot --tasks add-models-config,external-implementer,release-notes --full --auto`.
The listed order is the run order and each task is stacked on the previous one's branch.
Accepted phases are `new`, `planned` and `approved`; unplanned (`new`) tasks are planned
up front, before the overview. Thin repositories are refused — bootstrap the repository
first, then run `--tasks` again. Above 10 tasks `autopilot.py estimate` prints a warning;
there is no hard limit. Because each task depends on the previous one, `--full` skips
everything after a failed task. Resume a run with `/task-flow:task-autopilot <run-id>`.

**Circuit breaker.** Between tasks only, never inside one, `autopilot.py next` decides from
files whether the next task may start. Its four triggers, in both modes:

- wrong direction: the last two finished tasks both failed;
- cannot finish: every remaining task depends on a failed or skipped one;
- burn rate: the last finished task used more than 2 x its worst-case estimate;
- budget (with `--budget`): tokens spent plus the next task's worst case exceeds it.

All four count Claude tokens only; external USD cost is measured and reported, never a
trigger. A task already in progress always resumes. The breaker is decided by the script
but obeyed by procedure, not by a hook.

**Estimates are coarse.** Per task: check = 15000 + 3000 x commands, implement = 30000 +
12000 x steps, review = 30000 + 6000 x criteria, finish = 10000. A round is implement +
check + review. The 1-round estimate is check (baseline) + 1 round + finish; the 3-round
(worst case) estimate is check + 3 rounds + finish. The constants are invented, not
measured. With an external implementer, `autopilot.py estimate` and `next` take
`--implementer-kind profile` and count the implement term as 0 — the external round's
cost is measured in USD (the `External USD` column of `run.md`, totalled in `report.md`),
not estimated. Tokens of your main session are not counted, and Claude cannot see your plan's
session or weekly limit; check it with `/usage`.
