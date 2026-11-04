# task-flow orchestration — for the main session

The `/task-flow:*` commands run in the main session and follow these procedures. The
main session **orchestrates**: it creates files, runs subagents, updates `status.md`, and
talks to the user. It never plans, implements or reviews itself — that is what keeps each
role's context fresh.

Formats are defined in `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md`.

## Resolving the task

- An argument that names an existing `.work/<id>/` is that task.
- No argument: if exactly one task under `.work/` is not `done`, use it; otherwise list
  them (as `/task-flow:task-status` does) and ask which.
- A task is a `.work/<id>/` with a `status.md`. `.work/_autopilot/` holds autopilot runs
  and `.work/_epics/` holds epics; neither is a task — they have no `status.md` and are
  never listed or picked as one. An argument that names an epic id (`.work/_epics/<id>/`)
  is refused here: use `/task-flow:task-epic` for it, and never create a task whose id
  collides with an epic id (or an epic whose id collides with a task folder).
- Every subagent prompt gives absolute paths: the repository root, `.work/<id>/`, the
  worktree (or "none"), the base ref, and the round number. Nothing else — not this
  conversation, not a summary of earlier rounds.

## status.md

The orchestrator owns the header; agents only append under `## Log`.

```markdown
# Status: <id>
- phase: new | planned | approved | baseline | implementing | checking | reviewing | review-approved | blocked | escalated | done
- round: 0
- base: <branch or commit the task starts from>
- branch: task/<id>
- worktree: .work/<id>/worktree | none
- plan-approved: no | yes (user, <date>) | yes (--auto, <date>)
- last-verdict: none | APPROVED | CHANGES_REQUESTED
## History
- <ISO timestamp> <phase> — <one line>
## Log
```

After every phase, so any run can resume: **edit the header lines in place** and **append
one line at the end of `## History`**. Never rewrite the file from memory and never add a
second `## History` or `## Log` heading — read it, change the lines, keep the rest.

## Choosing each role's model

At the start of every phase that runs a subagent, run `models.py roles` once from the
repository root:

```
uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/models.py" roles --project <repo-root>
```

It prints one JSON object with the five roles (`planner`, `implementer`, `checker`,
`reviewer`, `backlog`); each maps to `{"value": <configured value, or "claude" when unset>,
"kind": "claude" | "profile" | "script", "model": <opus|sonnet|haiku, or null>}`. Exit 2:
show its stderr and end the command without changing `status.md` — a precondition, not a
stop. Pass a role's subagent call the `model` argument when that role's `model` is not
null, and no `model` when it is null (the agent's frontmatter model then applies). With no
`models.toml`, every role is `{"kind": "claude", "model": null, "value": "claude"}` and
nothing changes. A `checker` entry of kind `script` means `scripts/gates.py` runs in
Phase baseline and Phase check (see those phases): never the checker subagent, and never
a silent fallback to it either way — kind `claude` always runs `task-flow:checker`.

## Phase: new

1. Read `.claude/task-flow.md` if it exists (format: `${CLAUDE_PLUGIN_ROOT}/templates/task-flow.md`).
2. Make sure `.work/` is ignored: if `.work/.gitignore` does not exist, write it with the
   single line `*`. The workspace then ignores itself — no edit to the repo's `.gitignore`
   and none to `.git/info/exclude`, which Claude Code protects from edits.
3. Id: a ticket id from the request if there is one, else a 3–6 word kebab slug of the
   goal. If `.work/<id>/` exists, or `.work/_epics/<id>/` exists (the id collides with an
   epic), add `-2`, `-3`.
4. If goal or scope is genuinely unclear, ask **at most 2** questions, in one message.
5. Write `task.md` (Goal, Context, Constraints — the user's words, normalized, nothing
   invented) and `status.md` with phase `new`, base = the current branch.

## Phase: plan

1. Run subagent `task-flow:planner` with the paths (its model as in "Choosing each role's
   model").
2. If `plan.md` starts its open questions with `BLOCKED: yes`: show the questions, set
   phase `new`, stop. After the user answers, append the answers to `task.md` and re-plan.
3. Otherwise set phase `planned` and show a short summary: goal, profiles, number of
   steps, the acceptance criteria verbatim, the verification commands.
4. **Approval gate.** Ask: approve / edit / cancel (with `AskUserQuestion` when
   available). Do not continue without an explicit approve. "Edit" means the user's
   changes go into `task.md` as constraints and the planner runs again.
5. On approve: `plan-approved: yes (user, <date>)`, phase `approved`.
   With `--auto` only: skip the question, record `yes (--auto, <date>)`, and say so.
   `--auto` skips only this question; it changes nothing else in the run and never permits
   an early stop.

## Phase: baseline (round 0)

1. Refuse unless `plan-approved` is yes.
2. Isolation — **skip this step only when the worktree path `.work/<id>/worktree`
   exists, or when `status.md` says `worktree: none` and branch `task/<id>` already exists
   (`git rev-parse --verify task/<id>`)**; reuse it and never re-create it. Otherwise,
   unless `.claude/task-flow.md` says `worktree: no`:
   `git worktree add -b task/<id> .work/<id>/worktree <base>`. If worktrees are disabled or
   fail, `git switch -c task/<id>` in the repository root and record `worktree: none`.
   If the root has uncommitted changes and there is no worktree, stop and ask (a
   precondition, not a stop).
3. Set round 0, phase `baseline`. When the `checker` entry of `models.py roles` (as in
   "Choosing each role's model") has kind `script`: from the repository root, in the
   background, run
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/gates.py" --task-dir <abs .work/<id>> --worktree <abs worktree | none> --round 0`
   and wait. Exit 0: continue exactly as after the checker subagent — show the table from
   `checks-0.md`; the History line names `checker: script`. Exit 2: show stderr and end
   the command without changing `status.md` (a precondition, not a stop; the same command
   resumes). Otherwise run subagent `task-flow:checker` with round 0 (its model as in
   "Choosing each role's model"). It runs the plan's verification commands on the
   untouched base and writes `checks-0.md`.
4. Failures in `checks-0.md` do not stop the run. They are recorded; continue to round 1.

On re-entry (`/task-flow:task-check` at round 0, or resuming from phase `baseline`) run
steps 1-3 again; step 2 reuses an existing worktree or branch and never re-creates it.

## Phase: implement (round n)

1. Refuse unless `plan-approved` is yes.
2. Round 1 requires `checks-0.md`; if it is missing, run **Phase: baseline** first.
3. Pick the implementer.
   - (a) Resolve the roles from the repository root with `models.py roles`:
     `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/models.py" roles --project <repo-root>`.
     Exit 2: show its stderr and end the command without changing `status.md` (a
     precondition, like the uncommitted-changes check, not a stop). Read the
     `implementer` entry of the JSON (as in "Choosing each role's model").
   - (b) Set round n, phase `implementing`.
   - (c) Kind `claude` (value `claude` or `claude:*`, also the result with no config):
     run subagent `task-flow:implementer` with the paths, and for n > 1 the path of
     `review-<n-1>.md`, passing the entry's `model` as the subagent call's `model`
     argument when it is not null, exactly as today otherwise.
   - (d) Kind `profile`: the entry's `value` is the profile name. From the repository
     root, in the background
     (the round can outlast the Bash tool's foreground limit), run
     `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/implement.py" --task-dir <abs .work/<id>> --worktree <abs worktree | none> --round <n> --profile <value>`
     and wait for it. Read its exit code and its one result line (the last stdout line
     starting with `ok:`, `failed:`, `refused:` or `blocked:`). The script writes the
     `## Log` lines and stages the changes; the orchestrator does not.
     Exit 0: continue (the next phase is check). Exit 3: the script wrote `BLOCKED:`;
     step 4 applies unchanged. Exit 1 or 2: show the line, leave phase `implementing` and
     round n, and end the command; the same command (`task-run <id>` /
     `task-implement <id>`) runs round n again. This is not one of the stops, does not count
     as a round, and is not a judgment that the run cannot pass.
   - (e) The History line names the implementer: `claude`, or `claude:<model>`, or
     `<profile> (external): <result line>`.
   - (f) **Execution metadata (records only).** When the selected worktree (or the
     repository root when `worktree: none`) has `.claude/task-flow.records.toml` with
     `[records] enabled = true`, a Claude implementer round also writes
     `<task-dir>/impl-<n>.json`:
     `{"kind": "claude", "profile": "claude", "model": "<model>"}` with the `model`
     argument passed to the subagent call, or the implementer agent file's frontmatter
     `model` when none was passed (null). It is resolved and recorded at implement
     time, never inferred from `models.toml` at finish; an unavailable model stays
     `unknown`. External rounds already get their `impl-<n>.json` from `implement.py`.
     Without the config, nothing is written.
4. If the implementer appended `BLOCKED:` to the Log: phase `blocked`, show the problem,
   stop.

## Phase: check (round n)

Phase `checking`. When the `checker` entry of `models.py roles` has kind `script`, run
`scripts/gates.py` exactly as in Phase baseline step 3 but with `--round <n>`: exit 0
continues (show the table from `checks-<n>.md`; the History line names `checker:
script`), exit 2 shows stderr and ends the command without changing `status.md` (a
precondition, not a stop; the same command resumes). Otherwise run subagent
`task-flow:checker` (its model as in "Choosing each role's
model"). It writes `checks-<n>.md`. When the
current round is 0 (approved, no round run yet), run **Phase: baseline** instead.

## Phase: review (round n)

1. Refuse when the current round is 0. Round 0 is the baseline and has no reviewer; tell
   the user to run `/task-flow:task-implement`.
2. Refuse when `checks-<n>.md` for the current round n >= 1 does not exist.
3. Phase `reviewing`. Run subagent `task-flow:reviewer` (its model as in "Choosing each
   role's model"). Read the Verdict line of
   `review-<n>.md` and set `last-verdict`.

## The loop — /task-flow:task-run

```
new → plan → approval gate → baseline(0) → for n in 1..3: implement(n) → check(n) → review(n)
    APPROVED            → phase review-approved; tell the user to run /task-flow:task-finish
    CHANGES_REQUESTED   → n = 3: escalate (round cap)
                          n >= 2: no-progress check first; if it fires, escalate
                          otherwise: next round
```

No-progress check, computed mechanically after review n (n >= 2) with verdict
`CHANGES_REQUESTED`: the failing set of round n is the **introduced** commands (Result FAIL
or ERROR in `checks-<n>.md` and not pre-existing per the contract; pre-existing ones are
excluded) plus the AC ids with Result FAIL in `review-<n>.md`. If it is
**non-empty** and **equal** to the failing set of round n-1, escalate. An empty set never
triggers it.

### Stops

The run stops only at: (1) the approval gate, (2) `BLOCKED` from an agent (planner or
implementer), (3) `APPROVED`, (4) no progress, (5) the round cap (3). `--auto`
skips the approval gate (recorded as `yes (--auto, <date>)`); the other four stops
apply identically with and without `--auto`, which never permits an early stop.

A failure or refusal of an external implementer (Phase: implement, step 3d) ends the
command like a failed precondition: `status.md` is unchanged and the same command resumes
the round. It is not a stop and not a judgment that the run cannot pass.

**Never stop early on a prediction that the run cannot pass**, and never skip a round on
your own judgment. A check that fails every round is not a reason to stop; the no-progress
rule and the cap handle it. This governs one task's run. The autopilot's circuit breaker is
not an exception: it runs only between tasks (see Autopilot).

Escalate: phase `escalated`, write `summary.md` (stop reason — `no progress` or `round
cap` — goal, what each round changed, which criteria pass, what still fails with the
reviewer's fix list, for no progress the repeated failing set, a recommendation), show it,
stop. The History line names the reason. The user may then edit the plan and run again;
the round count restarts only when they say so.

Resuming: `/task-flow:task-run <id>` continues from the phase in `status.md`.

## Autopilot — /task-flow:task-autopilot

Takes requirements to a stack of finished tasks. Every task is an ordinary task-flow task
and runs the unchanged loop above. The run lives in `.work/_autopilot/<run-id>/`:
`requirements.md`, `backlog.md`, `run.md`, `report.md`. The orchestrator owns `run.md`;
`autopilot.py` only reads it.

```markdown
# Autopilot: <run-id>
- mode: semi | full
- base: <branch the run starts from>
- budget: <integer tokens> | none
- planning-tokens: <integer> | unknown
- continue-past: none | <task-id>, <task-id>
- bootstrap: none | <task-id>
- source: backlog | tasks | epic
- epic: <epic-id>            (only with `source: epic`)
- state: gathering | planning | ready | running | paused | stopped | done
## Tasks
| # | Task | Depends on | Tokens used | External USD |
| --- | --- | --- | --- | --- |
| 1 | bootstrap-project | - | 0 | 0 |
| 2 | add-config-loader | bootstrap-project | unknown | unknown |
## History
- <ISO timestamp> <state> — <one line>
```

`Depends on` is `-` or comma-separated ids of earlier rows. `Tokens used` is the sum of
`total_tokens` of every subagent run for that task after approval (checker, implementer,
reviewer; Claude subagent runs only), an integer, or `unknown` once any result lacked a
figure (sticky). The optional fifth column `External USD` is written when `models.py
roles` reports implementer kind `profile` at Gather, and is otherwise absent, so runs
without `models.toml` keep today's 4-column format; if it is missing when a cost must
be recorded, add it to the header and every row with `0`. Its values are a USD sum in
plain decimal notation, never scientific notation — write `0.000012`, not `1.2e-05` (6
decimal places are enough, because `implement.py` rounds `cost_usd` to 6; `autopilot.py`
accepts either notation when reading) — or `unknown` (sticky) when an `impl-<n>.json`
that should exist is missing or unreadable, **or when its `cost_usd` is null** (a
`driver = "cli"` subscription round or unknown usage: do not add anything numerically and
do not write `0` — write `unknown`; a later numeric round adds to the known subtotal and
the run total is then reported `at least`). `budget` is Claude tokens. `bootstrap` names
the bootstrap task; it is row 1 and every other row depends on it directly. `source` is
optional and absent means `backlog`; `tasks` marks a run built from an explicit
`--tasks` list (see "Explicit task list" below) and requires `bootstrap: none`, row 1
depending on `-`, and every later row depending on exactly the previous row. `source:
epic` marks a run generated from an epic's graph (see "Epics — /task-flow:task-epic"
below) and requires the extra `epic: <epic-id>` header line; `autopilot.py` then validates
that the epic exists under `.work/_epics/<epic-id>/`, is linked to this run id, is not
completed, and that the `## Tasks` table matches the epic's rows and dependencies exactly.

1. **Preconditions** (new run only, before anything is written). Refuse, saying why, when
   `.claude/task-flow.md` sets `worktree: no` or `Finish → commit` to anything but `yes`
   (stacked tasks need a worktree and a commit per task). Run
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/autopilot.py" inspect .` from
   the repository root (exit 2: stop with its message).
   **`commits: no`**: `git worktree add` needs a commit to base tasks on. If the index is
   empty (`git ls-files` prints nothing), ask: create an empty initial commit / stop.
   Create runs `git commit --allow-empty -m "chore: initial commit"` (no attribution
   lines, hooks not skipped), then `inspect` again. If the index is not empty, or the user
   picks stop: stop with "This repository has no commit yet; task-flow bases each task's
   worktree on a commit. Make an initial commit, then run the autopilot again." This
   question is asked even with `--auto` (`--auto` skips only the start gate) and is not a
   stop of the run: no run folder exists yet. Remember `thin` for Decompose. Ensure
   `.work/.gitignore` as in Phase: new.
2. **Gather.** Run id = 3–6 word kebab slug, `-2`, `-3` on collision. Create
   `.work/_autopilot/<run-id>/run.md` (state `gathering`, mode `semi` unless `--full`,
   budget from `--budget` or `none`, `planning-tokens: 0`, `continue-past: none`,
   `bootstrap: none`, base = current branch). Resolve the roles once with
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/models.py" roles --project <repo-root>`
   (exit 2: show its stderr and end the command without changing `run.md` — a
   precondition, not a stop); when the implementer's `kind` is `profile`, create the
   `## Tasks` table with the `External USD` column (header plus `0` in every row),
   otherwise with today's 4 columns. From the user: write `requirements.md` from
   the argument text; if goal or scope is unclear, ask at most 3 messages of at most 4
   questions each, appending answers in the user's words, nothing invented. With
   `--from-repo`: the backlog agent extends `requirements.md` from the repository (each
   requirement cited; its model as in "Choosing each role's model"). Append the `inspect`
   output to `requirements.md` under
   `## Repository`.
3. **Decompose.** Run subagent `task-flow:backlog` (its model as in "Choosing each role's
   model"), telling it `thin: yes|no` from
   `inspect`. `BLOCKED: yes`: show the questions, append the answers to `requirements.md`,
   run it again (both modes; the user is present before the loop). With `thin: yes` the
   backlog's task 1 must be a bootstrap task that every later task depends on; if it is
   not, run the backlog agent again once with that instruction, then stop with the problem
   if it still is not.
4. **Create and plan.** For each backlog task, in order, follow Phase: new without
   questions (id from the backlog, de-duplicated; `task.md` from its Goal/Context/
   Constraints plus a Context line naming the run and its dependencies; `status.md` with
   the run's base) and add its row to `run.md`; with a bootstrap task set `bootstrap:` to
   its final id. Then Phase: plan steps 1–3 for each task **without the approval gate**, in
   table order; each planner runs with its model as in "Choosing each role's model" (via
   Phase: plan step 1). **With a bootstrap task, the bootstrap task is planned first**, completely
   (its planner `BLOCKED: yes` questions asked and answered into its `task.md`, and
   re-planned, until its `plan.md` is not blocked), before any other task is planned. Every
   later task's `task.md` Context then carries the line: "Toolchain and verification: read
   `.work/<bootstrap-id>/plan.md` (the bootstrap task's plan) and use the toolchain and
   Verification commands it sets up, not only its `task.md`." and the planner prompt for
   those tasks says the same; the planner agent itself is unchanged. For the remaining
   tasks, collect planner `BLOCKED: yes` questions, ask them in one message, append the
   answers to each `task.md`, re-plan those. Add every subagent's `total_tokens` to
   `planning-tokens` (`unknown` if any is missing). State `planning`, then `ready`. All
   tasks are still planned before the overview. Later plans are not re-planned after the
   bootstrap task finishes.
5. **Overview (always, both modes).** Run
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/models.py" roles --project <repo-root>`
   (exit 2: show its stderr and end the command without changing `run.md` — a
   precondition, not a stop) and read the implementer's `kind`. Show, per task, id, goal
   line and dependencies, marking the bootstrap task; then the output of
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/autopilot.py" estimate .work/_autopilot/<run-id> --implementer-kind <implementer kind>`
   verbatim; the mode; and the sentence: estimates are coarse; the main session's own
   tokens are not counted; Claude cannot see your plan's session or weekly limit, check it
   with `/usage`.
6. **Start gate.** Ask start / edit / cancel. Start: in every task's `status.md` set
   `plan-approved: yes (user, <date>, autopilot <run-id>)` and phase `approved`. Edit: the
   change goes into that task's `task.md` (or `requirements.md` for backlog changes),
   re-plan or re-decompose, show the overview again; an edit that re-plans the bootstrap
   task re-plans every later task after it, so they keep reading its current `plan.md`.
   With `--auto` only: skip the question, record `yes (--auto, <date>, autopilot
   <run-id>)` and say so. State `running`.
7. **Loop (between tasks).** Each iteration, run
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/models.py" roles --project <repo-root>`
   (exit 2: show its stderr and end the command without changing `run.md` — a
   precondition, not a stop) and read the implementer's `kind`, then run
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/autopilot.py" next .work/_autopilot/<run-id> --implementer-kind <implementer kind>`
   and obey it exactly; never pick another task or stop on your own judgment.
   - `run <id>`: if the task has not started (phase `approved`, no worktree), set its
     `status.md` `base:` to the output of
     `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/autopilot.py" base .work/_autopilot/<run-id> <id>`
     (that is `task/<id>` of the last `done` row of this run in table order, or the run's
     base if none; for a `source: epic` run: the epic's `base` for a root row and
     `task/<parent>` for a child of a done parent); then follow The loop for that task
     from its current phase.
     Every within-task stop (BLOCKED, APPROVED, no progress, round cap) and "never stop
     early" apply unchanged and end only that task. On `review-approved` follow Phase:
     finish steps 1–4, 6 and 7, and replace step 5 with: no push, no PR, keep the worktree,
     ask nothing; these go into `report.md`. After every subagent call add its
     `total_tokens` to the task's `Tokens used`; that column stays Claude subagents only,
     an external implementer round (Phase: implement, step 3d) adds nothing to it. After
     each external implementer round that exits 0, 1 or 3, read
     `.work/<id>/impl-<n>.json` and add its `cost_usd` to the task's `External USD`
     column, in plain decimal notation (6 decimal places are enough because
     `implement.py` rounds `cost_usd` to 6; `autopilot.py` also accepts scientific
     notation) (exit 2 adds nothing — a stale file from an earlier attempt may exist; if
     the column is absent, add it to the header and every row with `0` first), and record
     the History line `implementer <profile>: <cost_usd> USD external`. When that
     `cost_usd` is **null** (a `driver = "cli"` subscription profile, or usage the CLI did
     not report), skip the numeric addition: write `unknown` in the cell (sticky — later
     numeric rounds still add to the known subtotal and the totals read `at least`) and
     record the History line `implementer <profile>: unknown USD external (subscription
     or unreported usage)`. If an external
     round ends with exit 1 or 2, set state `paused` (also with `--auto`), show the line
     and append the History line `paused: external implementer failed (<id>)`; do not run
     `next`. Otherwise append a History line to `run.md`, then run `next` again with the
     same `--implementer-kind`.
   - `wait <id>: <phase>` (semi only): state `paused`, show the task's `summary.md` or
     `BLOCKED:` line, ask: continue past it (its dependents are skipped) / stop. To retry,
     the user runs `/task-flow:task-run <id>` themselves and then resumes the autopilot.
     Continue: add the id to `continue-past`, run `next` again with the same
     `--implementer-kind`. Stop: as `stop:`.
   - `stop: <reason>`: state `stopped`. `done`: state `done`.
8. **Report.** On `stopped` or `done` write `report.md`: the stop reason (the `next` line
   verbatim), one row per task (phase, rounds, the implementer profiles used — the
   distinct `profile` values of its `impl-*.json` in round order, or `claude` when there
   are none —, Claude tokens used, External USD, the 1-round and 3-round estimates), a
   totals line with the Claude tokens (`planning-tokens` plus the sum of `Tokens used`,
   `at least` when any is `unknown`) and the external USD (the sum of the `External USD`
   column, `at least` when any row is `unknown` — a null CLI cost is never added as 0 —,
   or `0` when the column is absent), the branch stack in order (the last `done`
   branch contains all finished work), skipped
   and failed tasks with their `summary.md`, and what is left to the user: push, PRs,
   worktree removal. Show it.

### Explicit task list (`--tasks`)

`/task-flow:task-autopilot --tasks <id>[,<id>...]` runs tasks that already exist in
`.work/` as a stacked series in the given order, instead of decomposing requirements; a
run without `--tasks` is unchanged. For such a run, steps 2–4 (Gather, Decompose,
Create and plan) are replaced by:

(a) **Preconditions** as in step 1, except: `--tasks` together with requirement text or
`--from-repo` is refused; `commits: no` or `thin: yes` is refused (no initial-commit
question) with the hint to bootstrap the repository first — a decomposed autopilot run
or a bootstrap task of its own — then run `--tasks` again.
(b) Resolve the roles with `models.py roles` (exit 2: as in step 5) and run
`uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/autopilot.py" stack . <ids> --implementer-kind <implementer kind>`
from the repository root. Exit 2: show the message and end the command before anything
is written (a precondition, not a stop). Exit 0: its output is the `## Tasks` table.
(c) Run id and `run.md` as in Gather, with state `planning`, `bootstrap: none`,
`source: tasks` and base = the current branch, and the `stack` output as `## Tasks`
(with the `External USD` column when the implementer's kind is `profile`, as in
Gather). Write `requirements.md`: the id list as given, and the `inspect` output under
`## Repository`. No `backlog.md`. Ensure `.work/.gitignore` as in Phase: new.
(d) Set each listed task's `status.md` `base:` to the run's base (all of them are `new`,
`planned` or `approved`).
(e) Plan every `new` task in table order with Phase: plan steps 1–3 without the approval
gate, collecting `BLOCKED: yes` questions in one message, appending the answers to each
`task.md` and re-planning those; `planned` and `approved` tasks keep their plan. Add
every planner's `total_tokens` to `planning-tokens` (`unknown` if any is missing).
State `planning`, then `ready`.
(f) **Overview, Start gate, Loop and Report** as in steps 5–8, with: no bootstrap task is
marked; the `warning:` line of `autopilot.py estimate` (more than 10 tasks) is shown
verbatim; Edit re-plans only that task — changing the list means cancel and start
again; Start approves the tasks that are not `approved` yet with
`yes (user, <date>, autopilot <run-id>)` (or `yes (--auto, <date>, autopilot <run-id>)`),
while tasks already `approved` keep their `plan-approved` line.
(g) Failure handling is the unchanged `next`: because every row depends on the previous
one, `--full` skips every task after a failed one (`stop: cannot finish: ...`), and in
`--semi` choosing "continue past it" also ends in `stop: cannot finish`. Documented,
not special-cased.

**Stops of an autopilot run:** the start gate (skipped by `--auto`), backlog or planner
`BLOCKED` before the loop, `wait` (semi), `stop:` (circuit breaker), `done`, and the pause
after a failed external implementer round (exit 1 or 2; a defined rule, with and without
`--auto`, because `next` would return `run <id>` for the same task again). The no-commit
question and the other Preconditions come before a run exists and are preconditions, not
stops. The breaker has exactly the triggers of `autopilot.py next` (wrong direction,
cannot finish, burn rate, budget — all four count Claude tokens only) and is evaluated
only between tasks, never inside a task's run; `next` returns `run <id>` for a task
already in progress.

**Resuming:** `/task-flow:task-autopilot <run-id>` continues from the `run.md` state;
`paused` by `wait` asks the wait question again; `paused` by the History line `paused:
external implementer failed (<id>)` skips the wait question and runs `autopilot.py next
--implementer-kind <implementer kind>`, which returns `run <id>`, so the task's round runs
again. State `planning` of a `source: tasks` run plans the remaining `new` tasks (step (e)
of "Explicit task list"); nothing is decomposed. Preconditions are not re-run. A
`source: epic` run resumes exactly like any other: `/task-flow:task-autopilot <run-id>`
continues from the `run.md` state, and the epic stays linked to that same run id — a
second run cannot claim the same epic (`autopilot.py` refuses).

## Epics — /task-flow:task-epic

An epic gives a group of tasks one persistent id, created before execution and run or
resumed through the ordinary autopilot loop. It lives in `.work/_epics/<epic-id>/` and is
never a task: no `status.md`, never listed or picked by `task-list`. The strict
`epic.md` contract (fields, states, ordered `## Tasks` table with at most one parent per
row, `## History`) is defined in SKILL.md → epics; `scripts/epics.py` parses and validates
it strictly before every command, writes UTF-8/LF atomically, and every refusal leaves
the existing files unchanged. Task ids and epic ids never collide in either creation
direction, and a task belongs to at most one non-completed epic.

All `epics.py` calls run from the repository root:
`uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/epics.py" <command> . <epic-id> ...`
(exit 2: show its stderr, change nothing).

1. **new.** `epics.py new . <epic-id> --title T --goal G --base B [--tasks a,b,...]`.
   - The epic id must not match any existing `.work/<id>/` task folder — `epics.py new`
     refuses the collision, so pick another id (Phase: new step 3 applies the same rule in
     the other direction).
   - With `--tasks`: the existing eligible tasks (`new`/`planned`/`approved`, plan where
     required, no worktree) are grouped in the given order as a linear chain — row n
     depends on row n-1. Existing `done` tasks may be members (they are never rerun);
     failed, in-progress or unknown tasks are refused.
   - Goal-only (no `--tasks`): create the empty epic, then stage the decomposition in an
     ordinary autopilot planning folder (`.work/_autopilot/<run-id>/` with only
     `requirements.md` written from the goal), invoke the existing `backlog` agent on it,
     show the resulting decomposition for approval, create the ordinary task folders with
     Phase: new, and attach them with `add`. **No creation path starts implementation.**
   - Explicit dependencies (including independent roots) are expressed by editing the
     strict `## Tasks` table of `epic.md` directly; every command re-validates it.
     Multiple parents are rejected — joining branches needs a separate integration design.
2. **add / remove (idle only).** `epics.py add . <epic-id> --tasks a,b [--depends-on <row-id>|-]`
   appends existing eligible tasks (default parent: the current last row; `-` makes an
   independent root); `epics.py remove . <epic-id> --tasks a,b` removes rows. Both refuse
   while the epic is `active` (execution freezes membership), when a task has started, and
   when a removal would leave a dangling dependency. Additions are allowed between
   attempts; adding a task to a `completed` epic reopens it as `pending`. While a run is
   linked, both commands also regenerate that run's `## Tasks` table from the epic graph:
   existing rows keep their `Tokens used` / `External USD` cells and every header field
   (including `planning-tokens`), new rows start at `0`, one History line is appended to
   `run.md`, and the run state returns to `planning` — so resuming the run goes through
   planning, the overview and the start gate again before anything executes, and no
   started-task state or historical total is overwritten. `epics.py sync-run . <epic-id>`
   does the same regeneration after a hand edit of `epic.md`.
3. **run.** Create (or resume) an ordinary autopilot run for the epic and link it:
   `epics.py link . <epic-id> <run-id>` (refused unless `run.md` declares `source: epic`
   and `epic: <epic-id>`). Write `run.md` as in Gather, but with `source: epic`,
   `epic: <epic-id>`, and the `## Tasks` table **generated from the epic's graph** (same
   rows, same `Depends on` cells) — never edited by hand. Then run steps 5–8 of Autopilot
   unchanged: role resolution, `estimate`, the overview, the start gate (`--auto` skips
   only that question), the `next`-driven loop and `report.md`. The same epic id always
   resumes the same linked run; idle additions are incorporated through a fresh
   planning/start gate — step 2's regeneration of the linked run's table sets its state
   back to `planning` — without overwriting started-task state or historical totals.
   - Base selection for a `run <id>` is `autopilot.py base`, which for a `source: epic`
     run returns the **epic's `base` for root rows** and **`task/<parent>` for a row whose
     single parent is done** — irrespective of unrelated completed rows.
   - Newly added or changed plans cannot inherit a stale approval: the start gate approves
     the batch again.
4. **status.** `epics.py status . <epic-id>` prints the deterministic report: per task its
   phase, rounds, verdict, risk (unavailable until a merged risk schema exists), known
   tokens/external USD with `at least` labels for unknowns, blocker chains, concrete human
   next actions, and an escaped, state-coloured Mermaid diagram. The spend section is
   cumulative — the linked run's `planning-tokens` plus its task rows, or the archived
   attempt `spend.md` snapshots when a task has no row in the linked run — and never
   double counts across repeated status/retry calls. `task-list --epic <id>`
   filters tasks by membership. When the epic is completed, `epics.py report . <epic-id>`
   writes `.work/_epics/<epic-id>/report.md` (goal, results, cumulative spend,
   failures/retries, **all** delivery branches); partial completion is never reported as
   success. `delivery` (per-task | epic) is intent only until `agent-pr-path` merges: this
   task performs no push, PR, merge or aggregate integration commit, and a branched forest
   reports all of its branches rather than claiming the last one contains the epic.
5. **retry.** A failed member is retried through the epic: `epics.py retry . <epic-id>
   <task-id> --authorized` archives the task's current round artifacts
   (`checks-*.md`, `review-*.md`, `impl-*.json`, `summary.md`) under
   `.work/_epics/<epic-id>/attempts/<task-id>/attempt-<k>/` — task pruning only prunes
   `.work/<task-id>/`, so the archive survives — together with a `spend.md` snapshot of
   the task's tokens/USD at that moment, records the attempt and the previous round count
   in the epic's History, and is idempotent (re-running with nothing new changes
   nothing). With `--authorized` — the user's explicit authorization of a round reset;
   **`--auto` never passes it** — the command also resets the task's `status.md` in
   place (phase `approved`, round 0, one History line), so the ordinary task loop
   `/task-flow:task-run <task-id>` starts a **new attempt at round 1**; the 3-round cap
   and the no-progress rule apply unchanged per authorized attempt. Without
   `--authorized` the command refuses and changes nothing, including the task's
   `status.md`. Then delegate to the ordinary task loop. Declining a retry keeps the
   dependents blocked; a retry never silently clears breaker history and never
   authorizes outbound actions. Status/report spend is cumulative: the linked run's
   `planning-tokens` plus its per-task rows (cumulative per task, so archived attempts
   are already inside them), or the archived `spend.md` snapshots when a task has no row
   in the linked run — `unknown` stays sticky (`at least`), and a run without the
   `External USD` column counts as `0`, exactly like `autopilot.py`.

## Phase: finish — /task-flow:task-finish

1. Refuse unless `last-verdict: APPROVED`.
2. **SDLC status refresh (opt-in, before summary and commit).** When the selected
   worktree (or the repository root when `worktree: none`) contains
   `docs/task-flow/sdlc-status.toml`, run from the repository root, in the background,
   and wait:
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc_status.py" finish --repo <abs worktree | abs repository root> --task-file <abs .work/<id>/task.md>`
   The script validates the whole batch declared by the optional
   `- SDLC criteria closed by this task: <stage>/<criterion>[, ...]` bullet of
   `task.md` before changing anything, marks those criteria done in the source,
   re-renders the `sdlc:status` block of `README.md` and the page
   `docs/task-flow/ai-native-sdlc.md`, and re-checks them. Exit 0: continue — name the
   changed source, README block and page in `summary.md` and commit them together with
   the task's change. Any non-zero exit: show stderr and end the command without
   changing `status.md` — a validation failure stops finish before the commit (a
   precondition, not a stop; fix and run the same command again). With no source file
   or no declaration the script is a byte-for-byte no-op and changes nothing, and
   repeated finish is idempotent. A repository without `docs/task-flow/sdlc-status.toml`
   skips this step entirely.
3. Write `summary.md` with the fixed headings of the summary contract (SKILL.md →
   summary.md): `## Goal`, `## Changes`, `## Verification`, `## Rounds`,
   `## Out of scope` — goal, what changed (files — including any SDLC status files
   from step 2), how each criterion was verified, rounds taken, anything left out of
   scope.
4. **Task record (opt-in, before the commit).** When the selected worktree (or the
   repository root when `worktree: none`) contains `.claude/task-flow.records.toml`
   with `[records] enabled = true`, run from the repository root, in the background,
   and wait:
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/task_record.py" render --repo <abs repository root> --task-dir <abs .work/<id>> --worktree <abs worktree | none>`
   then the same command with `trailers` instead of `render`; its stdout is the
   sanitized trailer block (`Task-Id`, `Task-Rounds`, `Task-Implementer`,
   `Task-Plan-Approved`, and exactly one `Task-Epic: <epic-id>` when the task is a member
   of an epic under `.work/_epics/`; a disabled or non-epic repository is unchanged). Exit 0: continue — stage `docs/tasks/<id>.md` in the
   worktree together with the task's other changes, and append the trailer block once
   at the end of the proposed commit message, after a blank line, keeping the
   conventional subject and body and any unrelated trailers intact. Any non-zero
   exit: show stderr and end the command before the commit — a precondition, not a
   stop; fix and run the same command again. No record file and no trailers are
   produced on failure, an identical re-run is a no-op, and a different existing
   `docs/tasks/<id>.md` is refused. With no config file or `enabled = false` the
   script is a no-op that writes nothing and prints no trailers, and finish follows
   the old procedure unchanged — no extra approval gate. Record generation never
   commits, pushes, opens a PR, contacts a model or prunes; it reads only the root
   task artifacts listed in SKILL.md → Task records.
5. Propose a conventional commit message and a PR description built from `summary.md`
   (plus the trailer block of step 4 when it produced one).
6. Commit on the task branch (in the worktree) — allowed without asking unless the
   config says otherwise — including the staged `docs/tasks/<id>.md` from step 4.
   Show the commit.
7. **Ask** before each of: push, open a PR, remove the worktree. Do only what is
   confirmed, or what `.claude/task-flow.md → finish` allows without asking.
8. Phase `done`.
9. **Prune** (not asked): from the repository root run
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/prune_task.py" .work/<id>` and
   show its output. Every top-level `.md` file stays (task, plan, status, summary, checks,
   reviews) and so do the `impl-*.json` files, everything else in the folder is deleted, and `worktree/` is left to the
   worktree question in step 5. If the script exits non-zero, show stderr: exit 2 means it
   refused and deleted nothing, exit 1 means some entries (`failed: <name>`) could not be
   removed and the rest was pruned. Do not retry or delete by hand.

## Release — /task-flow:task-release

A release is not a task run: the five stops of a run (SKILL.md → Stops) do not apply to it;
it has exactly one gate of its own, the approval in step 5, which `--auto` does not exist
to skip.

All `release.py` calls run from the repository root:
`uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/release.py" <subcommand> ...`.
The targets and their version files, tag patterns, changelog paths, scopes and checks
live in `.claude/task-flow.release.toml` (override with `--config <path>`).

1. **Target.** Run `release.py targets`. When `$ARGUMENTS` has no `--target` and the
   config holds more than one target, ask which one; every later command then gets
   `--target <t>` in addition to `$ARGUMENTS`.
2. **Check.** Run `release.py check $ARGUMENTS` minus `--dry-run` (plus `--target <t>`
   when needed) — `check` has no `--dry-run` option. Exit 2 (a config or usage error):
   show stderr and end. Exit 1 (a refusal): show the failures and end — nothing has
   changed.
3. **Verify.** Run `release.py verify --target <t>` in the background (the checks can take
   a minute) and wait for it. Non-zero exit: show the output and end.
4. **Dry run** (only when `$ARGUMENTS` has `--dry-run`): run
   `release.py apply $ARGUMENTS` with `--dry-run`, show its output, and end. A dry run
   asks nothing.
5. **Approval gate — always asked, no `--auto`.** Show current → new version and the
   bump, the files that change, the changelog entry, the commit message and the tag; ask
   approve / edit / cancel. Edit version: remember that explicit `<version>` and re-run
   step 2 with it (replacing any version or `--major` / `--minor` / `--patch` in
   `$ARGUMENTS`). Edit entry: write the edited entry to
   `.work/_release/<target>-entry.md` (`.work/` is ignored, so the tree stays clean)
   and pass `--entry-file .work/_release/<target>-entry.md` in step 6. Cancel: end.
6. **Apply.** Run `release.py apply` with exactly the arguments of the last successful
   step-2 `check` run, minus `--dry-run` — that is the approved explicit `<version>` (or
   the original version or `--major` / `--minor` / `--patch` when it was not edited),
   plus `--target <t>`, `--since <ref>` and `--date` when they were given — plus
   `--entry-file .work/_release/<target>-entry.md` when the entry was edited; show its
   one result line. Never retry on your own.
7. **Push question — separate, default no.** Yes: `git push origin <branch>`, then
   `git push origin refs/tags/<tag>` — never `--tags`, never `--force`, never tag deletion
   or moving a tag. No: print both commands for later.

## Doctor — /task-flow:task-doctor

1. From the repository root, run
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" $ARGUMENTS`.
2. Show stdout verbatim — do not re-run the script, and do not summarise, re-sort, add or
   drop rows. Exit 1 means the FAIL rows are the result, not an error of the command;
   exit 2 is a usage error: show stderr and end.
3. Change nothing and offer no fixes beyond the printed ones: the doctor is read-only
   (there is no `--fix`), and it never prints a secret — key values are redacted.
4. If the command cannot start because `uv` is not found, print the uv installer
   one-liners for PowerShell
   (`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`)
   and POSIX (`curl -LsSf https://astral.sh/uv/install.sh | sh`) and "restart Claude Code".
5. `--online` adds one authenticated request per profile in use,
   `GET <base_url>/models` — the provider's model list, which costs no tokens — and says
   whether the configured `model` is among the listed ids.
