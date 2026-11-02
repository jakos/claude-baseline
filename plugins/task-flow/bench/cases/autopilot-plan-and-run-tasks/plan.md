# Plan: task-autopilot — gather requirements, plan them as task-flow tasks, run them in a loop

## Goal
Add `/task-flow:task-autopilot`, which takes a project from requirements to a stack of
finished task-flow tasks:

0. **Check the repository first** (new run only): a repository without any commit cannot
   get a worktree, so the autopilot detects it before gathering and offers an empty
   initial commit or stops with a clear message. A **thin** repository (empty, or little
   more than a minimal `CLAUDE.md`, by a checkable rule in `autopilot.py inspect`) gets a
   **bootstrap task first**: toolchain, test and lint commands, a fuller `CLAUDE.md`; every
   later task depends on it.
1. **Gather** requirements from the user (a short interview), from the repository
   (`--from-repo`), or both.
2. **Decompose** them into an ordered backlog of ordinary task-flow tasks (new `backlog`
   agent), create each task (`task.md`, `status.md`) and plan each one with the existing
   planner.
3. **Always, in both modes**, show the planned tasks and a token estimate per task and in
   total, with the budget, before the loop starts. Then one start question
   (start / edit / cancel), which approves every plan in the batch. `--auto` skips that
   question, exactly as `--auto` skips the plan approval in `task-run`.
4. **Loop** over the tasks: each task runs the existing `task-run` loop unchanged
   (baseline, up to 3 rounds, every within-task stop as today), then a finish without
   push, PR or worktree questions. Each task is based on the previous finished task's
   branch, so later tasks see earlier work.
5. **Modes**: `--full` keeps going past a failed task (escalated or blocked) and skips the
   tasks that depend on it. `--semi` (the default) pauses after a failed task and waits for
   the user's decision.
6. **Circuit breaker, both modes, between tasks only**: a script decides, from files, on
   four stated triggers, whether the run may start the next task.

How the breaker fits the contract's "never stop early on a prediction" rule: that rule
governs **one task's run** and stays unchanged. The breaker is never evaluated inside a
task. It is evaluated only by `autopilot.py next`, which the orchestrator runs between
tasks, and which always returns `run <id>` for a task that is already in progress. Its
triggers are mechanical (counts and token sums in files), not model judgment, so the
contract's "no agent may stop a run on its own judgment" also stays true.

## Context
- Procedures: `plugins/task-flow/skills/workflow/orchestration.md` (phases, The loop,
  Stops, Phase: finish, "Resolving the task"); contract:
  `plugins/task-flow/skills/workflow/SKILL.md` (Layout, Rounds and escalation, Stops).
- Command stubs are thin: `skills/task-run/SKILL.md` and `skills/task-list/SKILL.md`
  are the patterns (frontmatter `name`, `description`, `argument-hint`,
  `disable-model-invocation: true`; the body points at orchestration.md, or runs a script).
- Agents: `agents/planner.md` is the pattern for the new `agents/backlog.md` (frontmatter
  `tools`, `disallowedTools`, `model: opus`, `color`; numbered procedure; ends with a
  one-paragraph return). Planner, implementer and reviewer already have the `Skill` tool,
  so a bootstrap task's planner or implementer can use `dev-baseline:project-bootstrap`
  when that plugin is installed, with no agent change.
- `dev-baseline:project-bootstrap` lives at
  `plugins/dev-baseline/skills/project-bootstrap/SKILL.md` (CLAUDE.md, plugins wiring,
  verification gates). task-flow must work without it.
- Phase: baseline step 2 runs `git worktree add -b task/<id> .work/<id>/worktree <base>`,
  which fails on a repository with no commit (unborn `HEAD`); `git rev-parse --verify
  --quiet HEAD` exits non-zero exactly then.
- Guard: `scripts/guard.py` matches `ROLE = ^task-flow[:_](planner|implementer|checker|reviewer)$`.
  Non-implementer roles may write only under `.work/`; planner and reviewer may not run
  mutating git. `tests/test_guard.py` has `READ_ONLY_ROLES` and `ALL_ROLES` tuples that
  drive most tests.
- Script and test pattern: `scripts/task_list.py`, `scripts/prune_task.py` (PEP 723
  header, `requires-python = ">=3.14"`, stdlib only, `sys.stdout.reconfigure(encoding="utf-8")`,
  exit 2 on refusal with a stderr message) and `tests/test_task_list.py`,
  `tests/test_prune_task.py` (unittest, subprocess against temp `.work/` fixtures). Neither
  existing script calls git; `autopilot.py inspect` will, via `subprocess` (stdlib), and
  its tests create temp git repositories with `git init`.
- `task_list.py` and `task-status` list `.work/*/status.md`, so a run folder
  `.work/_autopilot/<run-id>/` (no `status.md` at `.work/_autopilot/`) is never listed as
  a task. Task ids are kebab slugs, so `_autopilot` cannot collide with one.
- Token figures: each subagent result in Claude Code reports its `total_tokens`. The main
  session's own tokens and the user's plan/session limit are not visible to the model; the
  plan therefore uses a user-given budget (`--budget`) and says plainly that the session
  limit must be checked with `/usage`.
- Versions: task-flow `0.4.0` in `plugins/task-flow/.claude-plugin/plugin.json` and the
  task-flow entry of `.claude-plugin/marketplace.json`; the last two features bumped minor.
- No `.claude/task-flow.md` in this repo. No pyproject; Python is checked with `uvx ruff`
  and each script's own unittest file. Root `README.md` has a Layout block listing
  task-flow's agents, skill count ("9 commands"), scripts and tests (lines ~241–247).
- This is a re-plan: task.md "Decisions after plan approval (user, 2026-10-24)" adds the
  no-commit precondition and the bootstrap task; its last bullet ("Plan v2 gap, fixed")
  makes the bootstrap task planned first and every later task's planner read the
  bootstrap task's `plan.md`. Everything decided at the earlier approval (start question,
  the four breaker triggers and thresholds, default mode semi) is kept unchanged.

### Formats this plan fixes (the script and the procedure both depend on them)

Run folder `.work/_autopilot/<run-id>/`: `requirements.md`, `backlog.md`, `run.md`,
`report.md`. Tasks themselves live at `.work/<task-id>/` as usual.

`run.md` (orchestrator owns it; `autopilot.py` reads it, never writes it):

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

`Depends on` is `-` or comma-separated ids of **earlier** rows. `Tokens used` is the sum
of `total_tokens` of every subagent run for that task after approval (checker, implementer,
reviewer), an integer, or `unknown` once any result lacked a figure (sticky).
`bootstrap` names the bootstrap task when the repository was thin at the start of the run;
it must then be row 1, and every other row's `Depends on` must list it directly.

`backlog.md` (written by the backlog agent):

```markdown
# Backlog: <run-id>
## Tasks
### 1. <task-id>
- depends-on: - | <task-id>, <task-id>
#### Goal
#### Context
#### Constraints
### 2. <task-id>
...
## Risks / open questions
```

At most 10 tasks (a bootstrap task counts toward the 10); each one reviewable change that
one task-flow plan can cover; ordered so dependencies come first. Blocking questions:
first line under Risks is `BLOCKED: yes`.

**Thin repository rule** (`autopilot.py inspect`): consider the tracked files
(`git ls-files`, so only what a worktree base contains), ignoring anything under `.work/`.
The **meta set** is: `CLAUDE.md`, `AGENTS.md`, anything under `.claude/`, `README`,
`README.*`, `LICENSE`, `LICENSE.*`, `.gitignore`, `.gitattributes`, `.editorconfig`
(top-level paths only, except `.claude/`). The repository is **thin** iff every tracked file
is in the meta set **and** `CLAUDE.md` is absent or has at most 40 non-blank lines. A
repository with no commit is reported as `commits: no` and is also thin (nothing is
tracked in a commit).

`autopilot.py` estimate formula (tokens; constants in one place at the top of the script,
documented as coarse defaults):

```
CHECK     = 15000 + 3000 * commands      # lines in plan.md's Verification commands block
IMPLEMENT = 30000 + 12000 * steps        # numbered items under ## Steps
REVIEW    = 30000 + 6000 * criteria      # distinct AC<n> ids under ## Acceptance criteria
FINISH    = 10000
ROUND     = IMPLEMENT + CHECK + REVIEW
one_round = CHECK + ROUND + FINISH       # baseline + 1 round + finish
max       = CHECK + 3 * ROUND + FINISH   # baseline + 3 rounds + finish
```

Example: steps 2, criteria 3, commands 2 → CHECK 21000, IMPLEMENT 54000, REVIEW 48000,
ROUND 123000, one_round 154000, max 400000.

## Profiles
- python: plugins/task-flow/scripts/autopilot.py, plugins/task-flow/tests/test_autopilot.py, plugins/task-flow/scripts/guard.py, plugins/task-flow/tests/test_guard.py
- docs: plugins/task-flow/agents/backlog.md, plugins/task-flow/skills/task-autopilot/SKILL.md, plugins/task-flow/skills/workflow/orchestration.md, plugins/task-flow/skills/workflow/SKILL.md, plugins/task-flow/README.md, README.md
- config: plugins/task-flow/.claude-plugin/plugin.json, .claude-plugin/marketplace.json

## Steps
1. **Add `plugins/task-flow/scripts/autopilot.py`** (python). Stdlib only, PEP 723 header
   like `task_list.py`, module docstring summarising the three subcommands, the formula,
   the thin rule and the breaker triggers. UTF-8 stdout. CLI:
   `autopilot.py inspect <repo-root>`, `autopilot.py estimate <run-dir>` and
   `autopilot.py next <run-dir>`.
   - **`inspect <repo-root>`**: exit 2 with a stderr message and empty stdout when
     `<repo-root>` is not the top level of a git work tree (`git -C <repo-root> rev-parse
     --show-toplevel` fails or names another directory) or `git` is not on PATH. Otherwise
     print exactly four lines and exit 0:
     ```
     commits: yes|no
     tracked: <n> files outside .work/
     claude-md: absent | <n> non-blank lines
     thin: yes|no
     ```
     `commits` from `git rev-parse --verify --quiet HEAD`; `tracked` and `thin` per the
     Thin repository rule above (with `commits: no`, `tracked` counts the index, and
     `thin` is `yes`). `claude-md` reads the working-tree `CLAUDE.md` at the root as UTF-8.
   - **Refuse with exit 2** (`estimate`, `next`) and a stderr message, printing nothing on
     stdout, when: `<run-dir>` is not an existing directory whose parent is named
     `_autopilot` and whose grandparent is named `.work`; `run.md` is missing or
     undecodable; a header key `mode`, `budget`, `planning-tokens`, `continue-past`,
     `bootstrap` is missing; `mode` is not `semi`/`full`; `budget` is not a positive integer
     or `none`; a task id repeats; a `Depends on` id is not an earlier row; `Tokens used` is
     neither a non-negative integer nor `unknown`; `bootstrap` is not `none` and either is
     not row 1 or some other row's `Depends on` does not list it; a task has no
     `.work/<id>/status.md` or no `.work/<id>/plan.md`. For `next` only: a task whose phase
     is `new` or `planned` (not approved). Unknown argument: argparse's exit 2.
   - Phase of a task = the `- phase:` header line of `.work/<id>/status.md`. Classes:
     **finished-ok** `done`; **failed** `escalated`, `blocked`; **in progress**
     `baseline`, `implementing`, `checking`, `reviewing`, `review-approved`; **pending**
     `approved`. **Skipped** = a pending task with a direct or transitive dependency that
     is failed or skipped.
   - Plan counts: steps = lines matching `^\d+\.\s` under `## Steps`; criteria = distinct
     `AC\d+` ids at the start of a line (after optional `-`, `*`, `**` or spaces) under
     `## Acceptance criteria`; commands = non-empty lines not starting with `#` inside the
     first fenced block under `## Verification commands`. Section = from its `## ` heading
     to the next `## ` heading.
   - **`estimate`** prints exactly:
     ```
     | # | task | steps | criteria | commands | 1 round | 3 rounds |
     | --- | --- | --- | --- | --- | --- | --- |
     | 1 | <id> | <n> | <n> | <n> | <one_round> | <max> |
     | total | | | | | <sum one_round> | <sum max> |
     spent: <S> tokens
     budget: <B> tokens; pending at 3 rounds each: fits|does not fit; pending at 1 round each: fits|does not fit
     ```
     Integers without separators. `spent` = `planning-tokens` + every `Tokens used`;
     an `unknown` counts as 0 and the line then reads `spent: at least <S> tokens`.
     "pending" sums cover tasks that are pending or in progress; `fits` iff
     `S + sum <= B`. With `budget: none` the last line is exactly
     `budget: none set; compare the totals with your plan's usage limit (/usage)`.
     Exit 0.
   - **`next`** prints exactly one line and exits 0. Rules, first match wins, rows in
     table order:
     1. a task is in progress → `run <id>` (resume; no breaker inside a task);
     2. **wrong direction**: the last two finished tasks (finished-ok or failed, table
        order, skipped ones ignored) are both failed →
        `stop: wrong direction: <id>, <id> failed in a row`;
     3. mode `semi` and a failed task is not in `continue-past` →
        `wait <id>: <phase>` (the first such task);
     4. no pending, non-skipped task remains: if any task is skipped →
        `stop: cannot finish: <skipped ids> depend on failed <failed ids>`; otherwise
        `done`;
     5. **burn rate**: the last finished task has an integer `Tokens used` greater than
        2 × its `max` estimate → `stop: burn rate: <id> used <U>, more than 2 x its estimate <E>`;
     6. **budget**: budget is an integer and spent + the candidate's `max` > budget →
        `stop: budget: spent <S> + next worst case <E> > budget <B>`;
     7. otherwise `run <candidate>`, the first pending, non-skipped task.
     A bootstrap task needs no special rule: because every other task depends on it, a
     failed bootstrap skips all of them (full → rule 4 `stop: cannot finish`; semi → rule
     3 `wait`).
2. **Add `plugins/task-flow/tests/test_autopilot.py`** (python). unittest, PEP 723 header,
   subprocess against temp `.work/_autopilot/<run>/` plus `.work/<id>/status.md` and
   `plan.md` fixtures, same style as `test_task_list.py`; `inspect` tests use temp git
   repositories (`git init`, local `user.name`/`user.email` config, `git add`, `git commit`)
   and are skipped with a reason when `git` is not on PATH. Test methods (names are part
   of the criteria): `test_estimate_formula`, `test_estimate_totals_and_budget_fits`,
   `test_estimate_budget_does_not_fit`, `test_estimate_without_budget`,
   `test_estimate_unknown_tokens_says_at_least`, `test_next_runs_first_pending`,
   `test_next_resumes_in_progress_before_any_breaker`, `test_next_done_when_all_finished`,
   `test_next_semi_waits_on_failure`, `test_next_semi_continue_past`,
   `test_next_full_skips_dependents_of_failed`, `test_breaker_wrong_direction`,
   `test_breaker_cannot_finish`, `test_breaker_burn_rate`,
   `test_unknown_tokens_never_trip_burn_rate`, `test_breaker_budget`,
   `test_failed_bootstrap_skips_every_task` (full mode: bootstrap `escalated` →
   `stop: cannot finish: …`; semi: `wait <bootstrap-id>: escalated`),
   `test_malformed_run_exits_2` (subTests: bad mode, bad budget, dependency on a later
   row, duplicate id, missing plan.md, unapproved task for `next`, missing `bootstrap`
   key, bootstrap not row 1, a row not depending on the bootstrap task),
   `test_rejects_dir_outside_autopilot`,
   `test_inspect_no_commit` (fresh `git init` → `commits: no`, `thin: yes`),
   `test_inspect_thin_empty_commit` (one empty commit → `commits: yes`, `tracked: 0 …`,
   `claude-md: absent`, `thin: yes`),
   `test_inspect_thin_minimal_claude_md` (committed 10-line `CLAUDE.md`, `README.md`,
   `.gitignore` → `thin: yes`),
   `test_inspect_not_thin_with_code` (adds a committed `src/app.py` → `thin: no`),
   `test_inspect_not_thin_long_claude_md` (committed `CLAUDE.md` with 41 non-blank lines
   only → `thin: no`),
   `test_inspect_ignores_work_and_untracked` (an untracked `main.py` and a force-added, committed
   `.work/x/task.md` do not make it non-thin),
   `test_inspect_rejects_non_repo` (plain temp dir → exit 2, empty stdout).
3. **Add the `backlog` role to `plugins/task-flow/scripts/guard.py`** (python): `ROLE`
   also matches `backlog`; the read-only git rule applies to `{"planner", "reviewer",
   "backlog"}`; the write rule (non-implementer → only under `.work/`) then covers it
   unchanged. Docstring lists the backlog agent with planner and reviewer. In
   `tests/test_guard.py` add `"backlog"` to `READ_ONLY_ROLES` and `ALL_ROLES` and add
   `test_backlog_writes_only_under_work` (Write to `.work/_autopilot/r/backlog.md` allowed,
   Write to `src/x.py` denied).
4. **Add `plugins/task-flow/agents/backlog.md`** (docs). Frontmatter like the planner:
   `name: backlog`, description (task-flow backlog agent: turns requirements into an
   ordered backlog of task-flow tasks; never edits source; run by task-autopilot),
   `tools: Read, Grep, Glob, Bash, PowerShell, Write`, `disallowedTools: Edit, MultiEdit,
   NotebookEdit`, `model: opus`. Procedure:
   1. read `workflow/SKILL.md` (for what a task and a plan are) and `.claude/task-flow.md`
      if present;
   2. with `--from-repo` (the prompt says so): read README, CLAUDE.md, CONTRIBUTING,
      `docs/`, planning files (`*PLAN*.md`, `TODO*`, `ROADMAP*`, `drafts/`), `TODO`/`FIXME`
      via `git grep`, and open `.work/*/task.md` (to avoid duplicates); write or extend
      `requirements.md` with one bullet per requirement, each citing `file:line`; never
      invent requirements the sources do not state;
   3. read `requirements.md`, explore the repo only as far as sizing needs, write
      `backlog.md` in the format above (≤ 10 tasks, dependencies first, Goal/Context/
      Constraints in the requirement's words);
   4. **thin repository** (the prompt says `thin: yes`, from `autopilot.py inspect`):
      task 1 is `bootstrap-project` with `depends-on: -`, and every later task lists
      `bootstrap-project` in `depends-on`. Its Goal: set up the toolchain the requirements
      imply, test and lint commands that run, and a fuller `CLAUDE.md` (what the project
      is, how to build and check it, what must never be done). Its Context names
      `requirements.md` and states: "If the `dev-baseline:project-bootstrap` skill is
      available, the planner and implementer may follow it; task-flow does not require
      it." It implements no product feature. Not thin → no bootstrap task;
   5. ambiguity that changes the backlog → questions under Risks with first line
      `BLOCKED: yes`, stop;
   6. return one paragraph: number of tasks, dependencies, whether a bootstrap task was
      added, blocked or not.
   Writes only under `.work/_autopilot/<run-id>/`; read-only git only.
5. **Add `plugins/task-flow/skills/task-autopilot/SKILL.md`** (docs). Frontmatter:
   `name: task-autopilot`, a description (gather requirements from you or the repo, plan
   them as task-flow tasks, bootstrapping a thin repo first, show tasks and token estimates,
   then run them one after another, semi- or fully automatic, with a circuit breaker
   between tasks),
   `argument-hint: "[<requirements> | <run-id>] [--from-repo] [--semi | --full] [--budget <tokens>] [--auto]"`,
   `disable-model-invocation: true`. Body: read orchestration.md and workflow/SKILL.md
   first; follow **Autopilot**; an argument naming `.work/_autopilot/<run-id>/` resumes
   it; `--auto` only skips the start question and only when the user typed it; never
   push, PR, deploy or remove a worktree; never make a commit except the initial commit
   the user chose in Preconditions; obey `autopilot.py next` exactly.
6. **Add a section `## Autopilot — /task-flow:task-autopilot` to
   `plugins/task-flow/skills/workflow/orchestration.md`** (docs), after "The loop", with
   these numbered phases:
   - **Preconditions** (new run only, before anything is written): refuse, saying why,
     when `.claude/task-flow.md` sets `worktree: no` or `Finish → commit` to anything but
     `yes` (stacked tasks need a worktree and a commit per task). Run
     `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/autopilot.py" inspect .`
     from the repository root (exit 2 → stop with its message).
     **`commits: no`**: `git worktree add` needs a commit to base tasks on. If the index is
     empty (`git ls-files` prints nothing), ask: create an empty initial commit /
     stop. Create → `git commit --allow-empty -m "chore: initial commit"` (no attribution
     lines, hooks not skipped), then run `inspect` again. If the index is not empty, or the
     user picks stop: stop with "This repository has no commit yet; task-flow bases each
     task's worktree on a commit. Make an initial commit, then run the autopilot again."
     This question is asked even with `--auto` (`--auto` skips only the start gate) and is
     not a stop of the run: no run folder exists yet.
     Remember `thin` for Decompose. Ensure `.work/.gitignore` as in Phase: new.
   - **Gather**: run id = 3–6 word kebab slug, `-2`, `-3` on collision; create
     `.work/_autopilot/<run-id>/run.md` (state `gathering`, mode `semi` unless `--full`,
     budget from `--budget` or `none`, `planning-tokens: 0`, `continue-past: none`,
     `bootstrap: none`, base = current branch). From the user: write `requirements.md`
     from the argument text; if goal or scope is unclear, ask at most 3 messages of at most
     4 questions each, appending answers in the user's words, nothing invented. With
     `--from-repo`: the backlog agent extends `requirements.md` from the repository
     (cited). Append the `inspect` output to `requirements.md` under `## Repository`.
   - **Decompose**: run subagent `task-flow:backlog`, telling it `thin: yes|no` from
     `inspect`. `BLOCKED: yes` → show the questions, append answers to `requirements.md`,
     run it again (both modes; the user is present before the loop). With `thin: yes`
     the backlog's task 1 must be a bootstrap task that every later task depends on; if it
     is not, run the backlog agent again once with that instruction, then stop with the
     problem if it still is not.
   - **Create and plan**: for each backlog task, in order, follow Phase: new without
     questions (id from the backlog, de-duplicated; `task.md` from its Goal/Context/
     Constraints plus a Context line naming the run and its dependencies; `status.md` with
     the run's base) and add its row to `run.md`; with a bootstrap task set `bootstrap:`
     to its final id. Then Phase: plan steps 1–3 for each task **without the approval
     gate**, in table order. **With a bootstrap task, the bootstrap task is planned first**,
     completely (its planner `BLOCKED: yes` questions asked and answered into its
     `task.md`, and re-planned, until its `plan.md` is not blocked), before any other task
     is planned. Every later task's `task.md` Context then carries the line: "Toolchain
     and verification: read `.work/<bootstrap-id>/plan.md` (the bootstrap task's plan) and
     use the toolchain and Verification commands it sets up, not only its `task.md`." and
     the planner prompt for those tasks says the same; the planner agent itself is
     unchanged. For the remaining tasks, collect planner `BLOCKED: yes` questions, ask
     them in one message, append answers to each `task.md`, re-plan those. Add every
     subagent's `total_tokens` to `planning-tokens` (`unknown` if any is missing). State
     `planning`, then `ready`. All tasks are still planned before the overview. (Later
     plans are not re-planned after the bootstrap task finishes.)
   - **Overview (always, both modes)**: show, per task, id, goal line and dependencies,
     marking the bootstrap task; then the output of
     `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/autopilot.py" estimate .work/_autopilot/<run-id>`
     verbatim; the mode; and the sentence: estimates are coarse; the main session's own
     tokens are not counted; Claude cannot see your plan's session or weekly limit, check
     it with `/usage`.
   - **Start gate**: ask start / edit / cancel. Start → in every task's `status.md`
     `plan-approved: yes (user, <date>, autopilot <run-id>)`, phase `approved`. Edit →
     the change goes into that task's `task.md` (or `requirements.md` for backlog
     changes), re-plan or re-decompose, show the overview again; an edit that re-plans
     the bootstrap task re-plans every later task after it, so they keep reading its
     current `plan.md`. With `--auto` only:
     skip the question, record `yes (--auto, <date>, autopilot <run-id>)` and say so.
     State `running`.
   - **Loop (between tasks)**: run `autopilot.py next .work/_autopilot/<run-id>` and obey
     it exactly; never pick another task or stop on your own judgment.
     `run <id>`: if the task has not started (phase `approved`, no worktree), set its
     `status.md` `base:` to `task/<id>` of the last `done` task of this run in table
     order, or the run's base if none; then follow The loop for that task from its
     current phase. Every within-task stop (BLOCKED, APPROVED, no progress, round cap) and
     "never stop early" apply unchanged and end only that task. On `review-approved`
     follow Phase: finish steps 1–4, 6 and 7, and replace step 5 with: no push, no PR,
     keep the worktree, ask nothing; these go into `report.md`. After every subagent call
     add its `total_tokens` to the task's `Tokens used`. Append a History line to
     `run.md`, then run `next` again.
     `wait <id>: <phase>` (semi only): state `paused`, show the task's `summary.md` or
     `BLOCKED:` line, ask: continue past it (its dependents are skipped) / stop. To retry,
     the user runs `/task-flow:task-run <id>` themselves and then resumes the autopilot.
     Continue → add the id to `continue-past`, run `next` again. Stop → as `stop:`.
     `stop: <reason>`: state `stopped`. `done`: state `done`.
   - **Report**: on `stopped` or `done` write `report.md`: stop reason (the `next` line
     verbatim), one row per task (phase, rounds, tokens used, 1-round and 3-round
     estimates), the branch stack in order (the last `done` branch contains all finished
     work), skipped and failed tasks with their `summary.md`, and what is left to the
     user: push, PRs, worktree removal. Show it.
   - **Stops of an autopilot run**: the start gate (skipped by `--auto`), backlog or
     planner `BLOCKED` before the loop, `wait` (semi), `stop:` (circuit breaker), `done`.
     The no-commit question and the other Preconditions come before a run exists and are
     preconditions, not stops. The breaker has exactly the triggers of `autopilot.py next`
     (wrong direction, cannot finish, burn rate, budget) and is evaluated only between
     tasks.
   - **Resuming**: `/task-flow:task-autopilot <run-id>` continues from `run.md` state;
     `paused` asks the wait question again. Preconditions are not re-run.
   Also in orchestration.md: under "### Stops", after "Never stop early …", add that this
   governs one task's run and the autopilot's circuit breaker is not an exception because
   it runs only between tasks (see Autopilot); under "Resolving the task", state that a
   task is a `.work/<id>/` with a `status.md` and `.work/_autopilot/` is not a task.
7. **Update `plugins/task-flow/skills/workflow/SKILL.md`** (docs): in Layout, one line that
   autopilot runs live in `.work/_autopilot/<run-id>/` (`requirements.md`, `backlog.md`,
   `run.md`, `report.md`) and their tasks are ordinary `.work/<task-id>/` folders; in
   `### Stops`, one sentence that these are the stops of one task's run and that
   `task-autopilot` adds run-level stops evaluated only between tasks
   (orchestration.md → Autopilot). Nothing else in the contract changes.
8. **Update `plugins/task-flow/README.md`** (docs): flow diagram unchanged; a
   `task-autopilot` row in the Commands table; a new section "Autopilot" (modes, the
   no-commit precondition, the thin-repository rule and the bootstrap task — planned
   first, later tasks planned against its `plan.md`; optional use of dev-baseline's
   `project-bootstrap`, not required —, the always-shown overview and
   start gate, what `--auto` skips, the stacked branches, the four breaker triggers with
   their thresholds, the estimate formula and its limits — main-session tokens not
   counted, session limit not visible, check `/usage`);
   "What enforces what": the backlog agent is guarded like the planner, and the breaker is
   decided by `autopilot.py` but obeyed by procedure, not a hook; the test paragraph adds
   `uv run --script plugins/task-flow/tests/test_autopilot.py`; Layout lists
   `agents/backlog.md`, the 10 commands, `scripts/autopilot.py`.
   **Root `README.md`**: Layout block — agents line adds backlog, "9 commands" → "10
   commands", a `scripts/autopilot.py` line with a short comment, tests line adds
   `tests/test_autopilot.py`.
9. **Bump task-flow `0.4.0` → `0.5.0`** (config) in
   `plugins/task-flow/.claude-plugin/plugin.json` and the task-flow entry of
   `.claude-plugin/marketplace.json`. dev-baseline unchanged.

## Acceptance criteria
AC1. `estimate` applies the formula exactly: a task with 2 steps, 3 criteria and 2
commands shows `154000` and `400000`; the total row sums the tasks.
Verify: `test_estimate_formula`, `test_estimate_totals_and_budget_fits` pass in
`uv run --script plugins/task-flow/tests/test_autopilot.py`.

AC2. The budget line says `fits` / `does not fit` per the rule in Step 1, prints the exact
no-budget sentence with `budget: none`, and says `at least` when a `Tokens used` is
`unknown`.
Verify: `test_estimate_budget_does_not_fit`, `test_estimate_without_budget`,
`test_estimate_unknown_tokens_says_at_least` pass.

AC3. `next` returns `run <id>` for the first pending task, `done` when all are finished,
and `run <id>` for an in-progress task even when two earlier tasks failed and the budget
is exceeded (breaker never inside a task).
Verify: `test_next_runs_first_pending`, `test_next_done_when_all_finished`,
`test_next_resumes_in_progress_before_any_breaker` pass.

AC4. Semi mode returns `wait <id>: <phase>` for a failed task until it is in
`continue-past`; full mode never waits and skips the dependents (direct and transitive) of
a failed task while still running independent ones.
Verify: `test_next_semi_waits_on_failure`, `test_next_semi_continue_past`,
`test_next_full_skips_dependents_of_failed` pass.

AC5. Each breaker trigger produces its `stop:` line exactly as in Step 1: wrong direction,
cannot finish, burn rate, budget; an `unknown` token figure never trips burn rate.
Verify: `test_breaker_wrong_direction`, `test_breaker_cannot_finish`,
`test_breaker_burn_rate`, `test_unknown_tokens_never_trip_burn_rate`,
`test_breaker_budget` pass.

AC6. Malformed input and a folder outside `.work/_autopilot/` exit 2 with a stderr message
and empty stdout, including a missing `bootstrap` key, a bootstrap task that is not row 1,
and a row that does not depend on the bootstrap task.
Verify: `test_malformed_run_exits_2` (all subTests), `test_rejects_dir_outside_autopilot`
pass.

AC7. The guard treats `task-flow:backlog` like the planner: writes only under `.work/`,
no mutating git, no commit or push; every existing guard test still passes.
Verify: `uv run --script plugins/task-flow/tests/test_guard.py` passes, including
`test_backlog_writes_only_under_work`; `READ_ONLY_ROLES` and `ALL_ROLES` contain
`"backlog"`.

AC8. `agents/backlog.md` exists with `name: backlog`, `model: opus`, no Edit tool, the
`backlog.md` format and the 10-task cap, the cited-sources rule for `--from-repo`, the
thin-repository rule (task 1 `bootstrap-project`, every later task depends on it, optional
`dev-baseline:project-bootstrap` sentence), and the `BLOCKED: yes` rule.
Verify: verification command 8; manual: read the agent file against Step 4.

AC9. `skills/task-autopilot/SKILL.md` has `disable-model-invocation: true`, the
`argument-hint` from Step 5, points at orchestration.md → Autopilot, and says `--auto`
skips only the start question, that nothing is pushed, opened as a PR, deployed or
removed, and that no commit is made except the user-chosen initial commit.
Verify: verification command 7; manual: read the file.

AC10. orchestration.md → Autopilot contains every phase of Step 6 in order; the overview
(tasks + `autopilot.py estimate` + the `/usage` sentence) is shown in both modes before
the loop; the loop obeys `autopilot.py next` and is the only place the breaker is
evaluated; within-task stops are unchanged; finish skips push/PR/worktree questions; and
"### Stops" and "Resolving the task" carry the two clarifications.
Verify: verification commands 7 and 9; manual: read Autopilot, Stops and Resolving the
task.

AC11. workflow/SKILL.md Layout names `.work/_autopilot/<run-id>/` and its four files;
Stops says the autopilot's run-level stops are evaluated only between tasks; no other
contract text changed.
Verify: verification command 9; manual: `git diff main -- plugins/task-flow/skills/workflow/SKILL.md`
shows only those two additions.

AC12. Plugin README has the command row, the Autopilot section (modes, no-commit
precondition, thin rule and bootstrap task, overview, breaker triggers with thresholds,
estimate limits), the guard and test notes; root README layout names backlog, 10
commands, `autopilot.py` and `test_autopilot.py`.
Verify: verification commands 10 and 13; manual: read the Autopilot section.

AC13. task-flow is `0.5.0` in both manifests and the manifests validate.
Verify: verification commands 11 and 12.

AC14. Existing tests and lint pass: task-list and prune tests, ruff check and format over
task-flow scripts and tests.
Verify: verification commands 3–6.

AC15. `inspect` reports a repository without a commit as `commits: no` / `thin: yes`,
applies the thin rule exactly (empty commit, ≤ 40-line `CLAUDE.md` with only meta files →
thin; any tracked non-meta file or a 41-line `CLAUDE.md` → not thin; untracked files and
`.work/` ignored), and exits 2 with empty stdout outside a git work tree root.
Verify: `test_inspect_no_commit`, `test_inspect_thin_empty_commit`,
`test_inspect_thin_minimal_claude_md`, `test_inspect_not_thin_with_code`,
`test_inspect_not_thin_long_claude_md`, `test_inspect_ignores_work_and_untracked`,
`test_inspect_rejects_non_repo` pass (not skipped: `git` is on PATH in this repo's
environment).

AC16. A failed bootstrap task stops every dependent task: full mode returns
`stop: cannot finish: …` naming all other tasks, semi mode returns
`wait <bootstrap-id>: escalated`.
Verify: `test_failed_bootstrap_skips_every_task` passes.

AC17. orchestration.md → Autopilot → Preconditions runs `autopilot.py inspect` before
Gather; on `commits: no` offers the empty initial commit only when the index is empty,
otherwise (or on stop) stops with the stated message; says the question is asked even
with `--auto`; and Decompose passes `thin` to the backlog agent and requires task 1 to be
the bootstrap task that every later task depends on; `run.md` has the `bootstrap` key.
Create and plan says the bootstrap task is planned first (including its `BLOCKED`
re-plan) before any other task, that every later task's `task.md` Context and planner
prompt point at `.work/<bootstrap-id>/plan.md` for toolchain and Verification commands,
that all tasks are still planned before the overview, and that a start-gate edit
re-planning the bootstrap task re-plans the later tasks.
Verify: verification commands 13 and 14; manual: read Preconditions, Gather, Decompose and
Create and plan.

## Verification commands
```
uv run --script plugins/task-flow/tests/test_autopilot.py
uv run --script plugins/task-flow/tests/test_guard.py
uv run --script plugins/task-flow/tests/test_task_list.py
uv run --script plugins/task-flow/tests/test_prune_task.py
uvx ruff@0.16.9 check plugins/task-flow/scripts plugins/task-flow/tests
uvx ruff@0.16.9 format --check plugins/task-flow/scripts plugins/task-flow/tests
git grep --untracked -nE "autopilot\.py|disable-model-invocation: true|argument-hint" -- plugins/task-flow/skills/task-autopilot/SKILL.md plugins/task-flow/skills/workflow/orchestration.md
git grep --untracked -nE "^name: backlog|^model: opus|BLOCKED: yes|bootstrap-project|project-bootstrap" -- plugins/task-flow/agents/backlog.md
git grep --untracked -nE "between tasks|_autopilot" -- plugins/task-flow/skills/workflow/orchestration.md plugins/task-flow/skills/workflow/SKILL.md
git grep --untracked -nE "task-autopilot|autopilot\.py|test_autopilot\.py|backlog" -- plugins/task-flow/README.md README.md
claude plugin validate .
git grep --untracked -nE "bootstrap task is planned first|<bootstrap-id>/plan\.md" -- plugins/task-flow/skills/workflow/orchestration.md
```
All commands run from the worktree root. The `git grep` lines read working-tree files;
`--untracked` covers new files not yet added. On the untouched base, commands 1, 7, 8, 10,
11, 13 and 14 are expected to fail (files absent, version still 0.4.0) and command 9 to find
only pre-existing text or nothing; that is the round-0 baseline, not a blocker. Command 12
is `Not run` when the `claude` CLI is not on PATH.

## Project rules
No `.claude/task-flow.md` in this repo. Rules that apply, from `task.md`, the contract and
the repo:
- Tasks the autopilot creates are ordinary task-flow tasks: same `task.md`, `status.md`,
  `plan.md`, phases, agents and within-task loop. No change to planner, implementer,
  checker or reviewer, or to the 3-round cap, no-progress rule or within-task stops. The
  bootstrap task is an ordinary task too.
- task-flow must not require dev-baseline: the bootstrap task may use
  `dev-baseline:project-bootstrap` when it is available, and works without it (user
  decision, 2026-10-24).
- The circuit breaker is evaluated between tasks only, never inside a task's run (user
  decision, 2026-10-24). "Never stop early on a prediction" stays as is for a task's run.
- Both modes always show the planned tasks and estimates before the loop (user decision,
  2026-10-24).
- Kept from the earlier approval (user, 2026-10-24): start / edit / cancel after the
  overview, skipped by `--auto`; the four counted breaker triggers with thresholds 2 failed
  in a row, nothing runnable, more than 2x estimate, budget, no agent judgment; default
  mode `semi`.
- Nothing leaves the machine: the autopilot never pushes, opens a PR, deploys or removes a
  worktree; it lists those for the user. Its only commits are the per-task finish commits
  and, when the user chooses it, the empty initial commit.
- Never record an approval the user did not give; `--auto` counts only when the user typed
  it.
- Commit messages and PR text carry no attribution lines (`Co-Authored-By`,
  `Claude-Session` or similar). A dev-baseline hook enforces this. Never skip hooks.
- Scripts run on macOS, Linux and Windows without Git Bash: stdlib only, started by
  `uv run --script`, `requires-python = ">=3.14"`, UTF-8 read and written explicitly, no
  bash, `jq` or `sed`. `git` is called via `subprocess` with an argument list, never a
  shell string.
- Do not change `disable-model-invocation` on any existing skill; the new skill has it
  `true`.
- Tests use temp directories only; nothing touches a real `.work/<id>/` or this
  repository's git state.
- Line endings LF (`.gitattributes`: `* text=auto eol=lf`).

## Out of scope
- A live end-to-end autopilot run (it costs a full multi-task session); recommended as a
  manual follow-up after finish, on a scratch repo (an empty one exercises the bootstrap
  path).
- Automatic retries or re-planning of an escalated or blocked task, and restarting its
  round count; the user retries with `/task-flow:task-run <id>`.
- Re-planning later tasks after the bootstrap task finishes.
- Parallel tasks, merging branches of independent tasks, or bases other than the linear
  stack; `worktree: no` support for the autopilot (it refuses).
- USD cost, model pricing, calibrating the estimate from past runs, config keys for the
  formula, breaker thresholds or the thin rule, reading the plan's real session or weekly
  limit.
- Pulling requirements from issue trackers or the network (`gh issue`, web).
- Changes to `task_list.py`, `prune_task.py`, `task-status`, `task-run` and the other
  existing command stubs, `templates/task-flow.md`, hooks.json, or the four existing
  agents; any change to dev-baseline, including `project-bootstrap`.
- Bootstrapping outside an autopilot run (e.g. from `task-new`), creating a remote, or
  committing untracked files the user already has.
- Pruning `.work/_autopilot/<run-id>/`; it is not a task folder.

## Risks / open questions
- **Re-approval needed.** The decisions after plan approval reopened planning; this plan
  needs a fresh approval. The user chose "Fix gap, then approve" for the v2 gap; this
  version carries that fix only. Earlier decisions (start question, breaker triggers and
  thresholds, default `semi`) are kept as they were.
- **Thin threshold is a guess.** "Little more than a minimal `CLAUDE.md`" is fixed as: only
  meta files tracked and `CLAUDE.md` ≤ 40 non-blank lines. A repo with a long `CLAUDE.md`
  but no code gets no bootstrap task; a repo with any tracked code gets none either. Raise
  at the gate if a different rule is wanted.
- **Tracked files only.** The thin rule uses `git ls-files`, because task worktrees are
  based on commits; uncommitted code in the root is ignored by the rule and is not in any
  task's worktree. The overview does not warn about it (out of scope).
- **Initial commit.** Offered as a question only when the index is empty, so it commits
  nothing of the user's; otherwise the autopilot stops and the user commits. Asked even
  with `--auto` because it changes the repository's history before any plan exists.
- **Later tasks follow the bootstrap's plan, not its result.** They are planned after
  the bootstrap task's `plan.md` exists and use its toolchain and Verification commands,
  but before the bootstrap is implemented. If the implementation departs from its plan
  (for example a reviewer-driven change of a command), later verification commands can
  still mismatch, showing as round-0 or round-1 failures inside those tasks; re-planning
  after the bootstrap finishes stays out of scope. Planning the bootstrap first also
  serialises its `BLOCKED` questions before the others' (one extra question message at
  most). A start-gate edit that re-plans the bootstrap re-plans the later tasks, which
  costs planner tokens.
- **Estimates are coarse.** Constants are invented, not measured; main-session tokens are
  excluded; `total_tokens` per subagent result is assumed to be reported, and missing
  figures become `unknown` (sticky), which disables burn rate for that task and makes the
  budget check optimistic. Planning all tasks before the overview spends planner tokens
  before the user sees estimates; the 10-task cap bounds that.
- **Stacked branches** make each task's diff include earlier tasks only through its base;
  a failed task's work is not in later bases, so a later independent task cannot rely on
  it. The report names the stack so the user can push or PR in order.
- Size: 9 steps, mostly prose, one ~250-line script with three subcommands. If round 1
  runs long, the script and its tests (Steps 1–2) are the part most likely to need a
  second round.
