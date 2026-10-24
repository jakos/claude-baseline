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
  and is not a task; it has no `status.md` and is never listed or picked as one.
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

## Phase: new

1. Read `.claude/task-flow.md` if it exists (format: `${CLAUDE_PLUGIN_ROOT}/templates/task-flow.md`).
2. Make sure `.work/` is ignored: if `.work/.gitignore` does not exist, write it with the
   single line `*`. The workspace then ignores itself — no edit to the repo's `.gitignore`
   and none to `.git/info/exclude`, which Claude Code protects from edits.
3. Id: a ticket id from the request if there is one, else a 3–6 word kebab slug of the
   goal. If `.work/<id>/` exists, add `-2`, `-3`.
4. If goal or scope is genuinely unclear, ask **at most 2** questions, in one message.
5. Write `task.md` (Goal, Context, Constraints — the user's words, normalized, nothing
   invented) and `status.md` with phase `new`, base = the current branch.

## Phase: plan

1. Run subagent `task-flow:planner` with the paths.
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
3. Set round 0, phase `baseline`. Run subagent `task-flow:checker` with round 0. It runs
   the plan's verification commands on the untouched base and writes `checks-0.md`.
4. Failures in `checks-0.md` do not stop the run. They are recorded; continue to round 1.

On re-entry (`/task-flow:task-check` at round 0, or resuming from phase `baseline`) run
steps 1-3 again; step 2 reuses an existing worktree or branch and never re-creates it.

## Phase: implement (round n)

1. Refuse unless `plan-approved` is yes.
2. Round 1 requires `checks-0.md`; if it is missing, run **Phase: baseline** first.
3. Set round n, phase `implementing`. Run subagent `task-flow:implementer` with the paths,
   and for n > 1 the path of `review-<n-1>.md`.
4. If the implementer appended `BLOCKED:` to the Log: phase `blocked`, show the problem,
   stop.

## Phase: check (round n)

Phase `checking`. Run subagent `task-flow:checker`. It writes `checks-<n>.md`. When the
current round is 0 (approved, no round run yet), run **Phase: baseline** instead.

## Phase: review (round n)

1. Refuse when the current round is 0. Round 0 is the baseline and has no reviewer; tell
   the user to run `/task-flow:task-implement`.
2. Refuse when `checks-<n>.md` for the current round n >= 1 does not exist.
3. Phase `reviewing`. Run subagent `task-flow:reviewer`. Read the Verdict line of
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
- state: gathering | planning | ready | running | paused | stopped | done
## Tasks
| # | Task | Depends on | Tokens used |
| --- | --- | --- | --- |
| 1 | bootstrap-project | - | 0 |
| 2 | add-config-loader | bootstrap-project | unknown |
## History
- <ISO timestamp> <state> — <one line>
```

`Depends on` is `-` or comma-separated ids of earlier rows. `Tokens used` is the sum of
`total_tokens` of every subagent run for that task after approval (checker, implementer,
reviewer), an integer, or `unknown` once any result lacked a figure (sticky). `bootstrap`
names the bootstrap task; it is row 1 and every other row depends on it directly.

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
   `bootstrap: none`, base = current branch). From the user: write `requirements.md` from
   the argument text; if goal or scope is unclear, ask at most 3 messages of at most 4
   questions each, appending answers in the user's words, nothing invented. With
   `--from-repo`: the backlog agent extends `requirements.md` from the repository (each
   requirement cited). Append the `inspect` output to `requirements.md` under
   `## Repository`.
3. **Decompose.** Run subagent `task-flow:backlog`, telling it `thin: yes|no` from
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
   table order. **With a bootstrap task, the bootstrap task is planned first**, completely
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
5. **Overview (always, both modes).** Show, per task, id, goal line and dependencies,
   marking the bootstrap task; then the output of
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/autopilot.py" estimate .work/_autopilot/<run-id>`
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
7. **Loop (between tasks).** Run
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/autopilot.py" next .work/_autopilot/<run-id>`
   and obey it exactly; never pick another task or stop on your own judgment.
   - `run <id>`: if the task has not started (phase `approved`, no worktree), set its
     `status.md` `base:` to `task/<id>` of the last `done` task of this run in table order,
     or the run's base if none; then follow The loop for that task from its current phase.
     Every within-task stop (BLOCKED, APPROVED, no progress, round cap) and "never stop
     early" apply unchanged and end only that task. On `review-approved` follow Phase:
     finish steps 1–4, 6 and 7, and replace step 5 with: no push, no PR, keep the worktree,
     ask nothing; these go into `report.md`. After every subagent call add its
     `total_tokens` to the task's `Tokens used`. Append a History line to `run.md`, then run
     `next` again.
   - `wait <id>: <phase>` (semi only): state `paused`, show the task's `summary.md` or
     `BLOCKED:` line, ask: continue past it (its dependents are skipped) / stop. To retry,
     the user runs `/task-flow:task-run <id>` themselves and then resumes the autopilot.
     Continue: add the id to `continue-past`, run `next` again. Stop: as `stop:`.
   - `stop: <reason>`: state `stopped`. `done`: state `done`.
8. **Report.** On `stopped` or `done` write `report.md`: the stop reason (the `next` line
   verbatim), one row per task (phase, rounds, tokens used, 1-round and 3-round estimates),
   the branch stack in order (the last `done` branch contains all finished work), skipped
   and failed tasks with their `summary.md`, and what is left to the user: push, PRs,
   worktree removal. Show it.

**Stops of an autopilot run:** the start gate (skipped by `--auto`), backlog or planner
`BLOCKED` before the loop, `wait` (semi), `stop:` (circuit breaker), `done`. The no-commit
question and the other Preconditions come before a run exists and are preconditions, not
stops. The breaker has exactly the triggers of `autopilot.py next` (wrong direction,
cannot finish, burn rate, budget) and is evaluated only between tasks, never inside a
task's run; `next` returns `run <id>` for a task already in progress.

**Resuming:** `/task-flow:task-autopilot <run-id>` continues from the `run.md` state;
`paused` asks the wait question again. Preconditions are not re-run.

## Phase: finish — /task-flow:task-finish

1. Refuse unless `last-verdict: APPROVED`.
2. Write `summary.md`: goal, what changed (files), how each criterion was verified, rounds
   taken, anything left out of scope.
3. Propose a conventional commit message and a PR description built from `summary.md`.
4. Commit on the task branch (in the worktree) — allowed without asking unless the config
   says otherwise. Show the commit.
5. **Ask** before each of: push, open a PR, remove the worktree. Do only what is
   confirmed, or what `.claude/task-flow.md → finish` allows without asking.
6. Phase `done`.
7. **Prune** (not asked): from the repository root run
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/prune_task.py" .work/<id>` and
   show its output. Every top-level `.md` file stays (task, plan, status, summary, checks,
   reviews), everything else in the folder is deleted, and `worktree/` is left to the
   worktree question in step 5. If the script exits non-zero, show stderr: exit 2 means it
   refused and deleted nothing, exit 1 means some entries (`failed: <name>`) could not be
   removed and the rest was pruned. Do not retry or delete by hand.
