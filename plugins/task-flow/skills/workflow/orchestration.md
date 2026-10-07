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

Phase `reviewing`. Run subagent `task-flow:reviewer`. Read the Verdict line of
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
implementer), (3) `APPROVED`, (4) no progress, (5) the round cap (3). These apply
identically with and without `--auto`, which skips only the approval question.

**Never stop early on a prediction that the run cannot pass**, and never skip a round on
your own judgment. A check that fails every round is not a reason to stop; the no-progress
rule and the cap handle it.

Escalate: phase `escalated`, write `summary.md` (stop reason — `no progress` or `round
cap` — goal, what each round changed, which criteria pass, what still fails with the
reviewer's fix list, for no progress the repeated failing set, a recommendation), show it,
stop. The History line names the reason. The user may then edit the plan and run again;
the round count restarts only when they say so.

Resuming: `/task-flow:task-run <id>` continues from the phase in `status.md`.

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
