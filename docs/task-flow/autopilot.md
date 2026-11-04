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
but obeyed by procedure, not by a hook. An external round whose `impl-<n>.json` has a null
`cost_usd` (a `driver = "cli"` subscription profile, or unknown usage) is not added
numerically: its `External USD` cell is `unknown` (sticky — later numeric rounds add to
the known subtotal and the total is reported `at least`), exactly like `autopilot.py`
already reads it. The planner's external USD counts into the same cell: a
`role-planner-*.json` with a null `cost_usd` makes it `unknown` the same way, and when a
task's folder carries invocation files (`impl-<n>.json`, `role-*.json`), `estimate` and
the epic spend recompute the totals from those invocation ids, so a resume never double
counts.

**Estimates are coarse.** Per task: check = 15000 + 3000 x commands, implement = 30000 +
12000 x steps, review = 30000 + 6000 x criteria, finish = 10000. A round is implement +
check + review. The 1-round estimate is check (baseline) + 1 round + finish; the 3-round
(worst case) estimate is check + 3 rounds + finish. The constants are invented, not
measured. With an external implementer, `autopilot.py estimate` and `next` take
`--implementer-kind profile` and count the implement term as 0 — the external round's
cost is measured in USD (the `External USD` column of `run.md`, totalled in `report.md`),
not estimated; a null `cost_usd` (a CLI subscription round or unknown usage) is recorded
as `unknown` in the column, not as 0. `estimate` and `next` also take `--planner-kind
profile` when the planner is an external profile: the planner's cost is likewise measured
per invocation (`role-planner-*.json`), never estimated, and the estimate prints a
`planner: external profile; ...` line, a `spent by stage (Claude tokens): ...` line and,
when invocation files exist, an `external by stage: planner: ..., implementer: ...`
line. `task-autopilot --planner <profile>` plans every newly planned task of the run with
that enabled profile (recorded once in the run's History); resuming without the flag uses
the current configuration, and `planned`/`approved` tasks keep their plan. Tokens of your
main session are not counted, and Claude cannot see your plan's
session or weekly limit; check it with `/usage`.

## Epics

An epic gives a group of tasks one persistent id that is created **before** execution, can
be run or resumed through the ordinary autopilot loop, and can be inspected for
dependencies, progress, cost and failures. It lives in `.work/_epics/<epic-id>/` and is
never a task: no `status.md`, never listed by `task-list` (which instead shows an Epic
column and a `--epic <epic-id>` filter). `scripts/epics.py` parses and validates the strict
file before every command; every refusal leaves the existing files unchanged.

```markdown
# Epic: <epic-id>
- title: <one line>
- goal: <one line>
- base: <branch the root tasks start from>
- state: pending | active | paused | completed
- delivery: per-task | epic
- active-run: none | <autopilot run-id>
## Tasks
| # | Task | Depends on |
| --- | --- | --- |
| 1 | first-task | - |
| 2 | second-task | first-task |
## History
- <ISO timestamp> <event> — <one line>
```

Exactly those six header fields in that order, then `## Tasks` and `## History`. Rows are
numbered from 1 and `Depends on` is `-` or one **earlier** row — at most one parent;
joining branches needs a separate integration design, and forward, dangling or cyclic
dependencies are refused. Task ids and epic ids never collide in either direction, and a
task belongs to at most one non-completed epic.

- **Create.** `/task-flow:task-epic new <epic-id> --title "..." --goal "..." --base <branch>` creates an
  empty epic (goal-only: the decomposition is staged in an ordinary autopilot planning
  folder, shown for approval, and the created tasks are attached with `add`). With
  `--tasks a,b,c` the existing eligible tasks are grouped in the given order as a linear
  chain. No creation path starts implementation. Explicit dependencies, including
  independent roots, are declared by editing the `## Tasks` table; it is re-validated
  before every command.
- **Mutate while idle.** `add <epic-id> --tasks ... [--depends-on <row>|-]` and
  `remove <epic-id> --tasks ...` work only while the epic is not active; a started task is
  never removed and no dangling edge is left. Adding a task to a completed epic reopens
  it. While a run is linked, `add`/`remove` also regenerate that run's `## Tasks` table
  from the epic graph (existing rows keep their `Tokens used` / `External USD` cells and
  the header fields, new rows start at `0`, the run state returns to `planning`);
  `sync-run <epic-id>` does the same after a hand edit of `epic.md`.
- **Run / resume.** `run <epic-id>` creates or resumes an ordinary autopilot run
  (`.work/_autopilot/<run-id>/run.md` with `source: epic` and `epic: <epic-id>`, its
  `## Tasks` table generated from the epic graph) and links the epic to it
  (`epics.py link`). Then the unchanged Autopilot procedure runs: same role resolution,
  estimates, overview and start gate (`--auto` skips only that question), same task loop
  and circuit breaker. The same epic id always resumes the same linked run; idle additions
  regenerate the linked run's table (run state back to `planning`) and go through a fresh
  planning/start gate. Root rows are based on the epic's `base`;
  a child is based on its completed parent's branch (`task/<parent>`), irrespective of
  unrelated completed rows.
- **Retry.** A failed member is retried through the epic: `epics.py retry <epic-id>
  <task-id> --authorized` archives the prior round evidence and a `spend.md` snapshot
  under `.work/_epics/<epic-id>/attempts/<task-id>/attempt-<k>/` (task pruning cannot
  delete it), records the attempt and the previous round count in the epic's History, and
  resets the task's `status.md` (phase `approved`, round 0) so `/task-flow:task-run
  <task-id>` starts a new attempt at round 1 — the 3-round cap and no-progress rule apply
  per authorized attempt. A round reset needs your explicit authorization — `--auto` never
  grants it, and without `--authorized` the retry refuses and changes nothing. Declining
  a retry keeps the dependents blocked.
- **Status / report.** `status <epic-id>` shows each task's phase, rounds, verdict, risk
  (unavailable until a merged risk schema exists), known tokens and external USD (`at
  least` when unknown), blocker chains, the next human action, and an escaped,
  state-coloured Mermaid diagram; its spend section is cumulative — the linked run's
  `planning-tokens` plus its task rows, or the archived attempt spend when a task has no
  row in the linked run. A completed epic gets a concise `report.md` with the
  goal, results, cumulative spend, failures/retries and **all** delivery branches;
  partial completion is never reported as success.
- **Costs and records.** Budget, burn-rate and the other breaker triggers count Claude
  tokens exactly as for any run (planning spend plus every task's `Tokens used`, `at
  least` when unknown; external USD is measured, never a trigger). With records enabled,
  a member task's finish also emits exactly one `Task-Epic: <epic-id>` commit trailer.
- **Delivery is intent only.** `delivery: per-task | epic` records a preference until
  `agent-pr-path` merges; epics never push, open a PR, merge or make an aggregate
  integration commit, and a branched forest reports all of its branches.
