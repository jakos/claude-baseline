# task-flow

A multi-agent workflow for tasks and feature requests:

```
/task-flow:task-run "<request>"
  → planner (opus) writes plan.md
  → you approve, edit or cancel          ← nothing is built before this
  → checker (haiku) runs on the untouched base → checks-0.md
  → round 1..3:
       implementer (sonnet) changes code in a worktree
       checker (haiku) runs tests, lint, builds  → checks-n.md
       reviewer (opus) judges each criterion     → review-n.md
       APPROVED → stop   CHANGES_REQUESTED → next round
       same introduced failures as the round before (no progress), or round 3 → escalate to you
  → /task-flow:task-finish commits, asks before push or PR, then prunes the task folder to its .md files
```

Each role is a separate subagent with a fresh context. They share nothing but files in
`.work/<task-id>/`, so the reviewer sees the plan, the diff and the check results — never
the implementer's reasoning.

## Install

From the `claude-baseline` marketplace, in a repository where you want the workflow:

```
/plugin install task-flow@claude-baseline
```

It depends on `dev-baseline`, which is installed with it if missing. It needs
[uv](https://docs.astral.sh/uv/) on `PATH` for its guard hook, like `dev-baseline`.

The workspace ignores itself: `/task-flow:task-new` writes `.work/.gitignore` containing
`*`. To make that explicit for the team as well, add this to the repo's `.gitignore`:

```gitignore
# task-flow workspaces
.work/
```

## Permissions

The workflow runs under your normal permission settings; nothing in it bypasses them.
These rules in the project's `.claude/settings.json` let a run go from approval to verdict
without a prompt per command, and were enough for the validation runs:

```json
{
  "permissions": {
    "allow": [
      "Bash(uv run:*)", "Bash(git -C:*)", "Bash(git status:*)", "Bash(git diff:*)",
      "Bash(git log:*)", "Bash(git show:*)", "Bash(git grep:*)", "Bash(git ls-files:*)",
      "Bash(git add:*)", "Bash(git worktree add:*)", "Bash(git switch:*)",
      "Bash(git rev-parse:*)"
    ],
    "ask": ["Bash(git push:*)"]
  }
}
```

Add the project's own tools as needed (`Bash(yamllint:*)`, `Bash(just check)`). A command
an agent cannot run is reported as `Not run` or as a blocker, never silently skipped.

## Commands

Plugin commands are namespaced, so each is `/task-flow:<name>`:

| Command | What it does |
| --- | --- |
| `task-new <description>` | Creates `.work/<id>/task.md` and `status.md`; asks at most 2 questions |
| `task-plan [id]` | Runs the planner, shows the plan, asks you to approve, edit or cancel |
| `task-implement [id]` | Requires approval; runs the baseline check first (creates the branch and worktree); runs the implementer |
| `task-check [id]` | Runs the checker for the current round |
| `task-review [id]` | Runs the reviewer for the current round |
| `task-run <description \| id> [--auto]` | All of the above, with the approval gate and the 3-round cap |
| `task-autopilot [<requirements> \| <run-id>] [--from-repo] [--semi \| --full] [--budget <tokens>] [--auto]` | Gathers requirements, plans them as a stack of tasks, shows tasks and token estimates, then runs them one after another (see Autopilot) |
| `task-list [--all]` | Open tasks as a table, ordered by what to do next; `--all` includes done ones |
| `task-status [id]` | Phase, round and last verdict for one task or all |
| `task-finish [id]` | After `APPROVED`: summary, commit message, PR text; local commit; asks before push or PR; last, prunes `.work/<id>/` to its `.md` files (not asked) |

`task-list` runs `scripts/task_list.py` and sorts rows by phase group, then oldest last
History entry first, then id:

1. waiting on you: `planned`, `blocked`, `escalated`;
2. in progress: `approved`, `baseline`, `implementing`, `checking`, `reviewing`,
   `review-approved`;
3. not started: `new`;
4. unrecognised or missing phase;
5. `done` (only with `--all`).

The timestamp is the first word of the last `## History` bullet; one without a zone counts
as UTC, and a missing or unparseable one sorts last within its group. Round and last
verdict are shown but do not affect the order.

`--auto` skips only the plan approval question. It never allows an early stop, a push, a PR
or a deploy. A run stops only at the approval gate (skipped by `--auto`), a `BLOCKED`
agent, `APPROVED`, no progress, or the round cap; the last four apply with or without
`--auto`.
Every run can be resumed with `task-run <id>` or the single-step commands, because the
phase is in `status.md`.

## Autopilot

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

**Circuit breaker.** Between tasks only, never inside one, `autopilot.py next` decides from
files whether the next task may start. Its four triggers, in both modes:

- wrong direction: the last two finished tasks both failed;
- cannot finish: every remaining task depends on a failed or skipped one;
- burn rate: the last finished task used more than 2 x its worst-case estimate;
- budget (with `--budget`): tokens spent plus the next task's worst case exceeds it.

A task already in progress always resumes. The breaker is decided by the script but obeyed
by procedure, not by a hook.

**Estimates are coarse.** Per task: check = 15000 + 3000 x commands, implement = 30000 +
12000 x steps, review = 30000 + 6000 x criteria, finish = 10000. A round is implement +
check + review. The 1-round estimate is check (baseline) + 1 round + finish; the 3-round
(worst case) estimate is check + 3 rounds + finish. The constants are invented, not
measured. Tokens of your main session are not counted, and Claude cannot see your plan's
session or weekly limit; check it with `/usage`.

## What enforces what

Prompts are advice; a hook is a rule. `scripts/guard.py` runs before every edit and shell
command and applies only to task-flow's own agents (it reads `agent_type` from the hook
input):

- planner, backlog, checker and reviewer can write only under `.work/`;
- no task-flow agent can push, open a PR, `kubectl`/`oc apply`, `helm install`, `docker
  push` or `terraform apply`;
- no task-flow agent can commit — changes stay staged until `/task-flow:task-finish`;
- planner, backlog and reviewer cannot run git commands that change the tree or branches.

Commands are matched as raw text, split into `;`/`&`/`|`/newline segments, without
regard to quotes. A `git` segment with whitespace followed by a mutating verb (`add`,
`checkout`, `reset`, `worktree add`, ...) is denied for planner and reviewer; one with
whitespace followed by a commit or push verb is denied for every task-flow agent.

Run the guard's test with
`uv run --script plugins/task-flow/tests/test_guard.py`, the task list's with
`uv run --script plugins/task-flow/tests/test_task_list.py`, the prune's with
`uv run --script plugins/task-flow/tests/test_prune_task.py`, and the autopilot's with
`uv run --script plugins/task-flow/tests/test_autopilot.py`.

Limits of the guard, stated plainly:

- It sees edit tools and shell command text. A shell redirect such as `echo x > file`
  from the checker or reviewer is not caught; their prompts forbid it, nothing enforces it.
- **Known false positive:** planner and reviewer are denied when a mutating git phrase
  appears with whitespace before the verb anywhere in the command text, including inside
  quotes, e.g. `git grep -e "worktree add"`. Quotes are not parsed. Quoted single words
  such as `git log --grep 'commit'` pass. Use the Grep tool for such searches. The test
  pins this.
- The approval gate, the 3-round cap, the no-progress stop and the list of stops are
  enforced by the orchestration procedure and recorded in `status.md`; they are
  instructions, not hooks. The autopilot's circuit breaker is the same: `autopilot.py`
  computes the decision, the procedure obeys it.

## Project configuration

Optional `.claude/task-flow.md` in the target repository — copy
[`templates/task-flow.md`](templates/task-flow.md). It declares extra profiles and
path→profile mappings, exact verification commands per profile (replacing the
defaults), architecture rules, base branch, branch naming, whether to use worktrees, and
what `task-finish` may do without asking. It is Markdown because only agents read it.

The planner copies what applies into `plan.md → Project rules` and `Verification
commands`, so the other agents never read the config themselves.

## Profiles

| Profile | Detects |
| --- | --- |
| `profile-python` | `*.py`, `pyproject.toml`, `requirements*.txt` — thin: defers to `python-tooling`, and to `python-service` / `pytest-suite` when claude-python-service is installed |
| `profile-docs` | `*.md`, `*.rst`, `docs/`, `mkdocs.yml`, ADRs |
| `profile-config` | YAML manifests, Helm, kustomize, compose, `Jenkinsfile`, `Dockerfile`, `.env*`, CI files |
| `profile-generic` | everything else — discovers conventions and commands from the repo |
| `workflow` | not a profile: the shared contract — layout, formats, round limit |

Profiles are skills marked `disable-model-invocation` and `user-invocable: false`: they
never load in an ordinary session, never compete with `python-tooling` for triggers, and
cost no context. Agents read them by path.

### Adding a profile

1. Create `skills/profile-<name>/SKILL.md` with the same frontmatter as the others and
   the four sections: **Detect**, **Conventions**, **Verify**, **Review checklist**.
2. Add its path to the Profiles list in `skills/workflow/SKILL.md`, and its name to the
   allowed values under `plan.md → Profiles`.
3. Run one `task-run` on a scratch repo with a file it should detect, and check that
   `plan.md → Profiles` names it.

## Layout

```
.claude-plugin/plugin.json      manifest; depends on dev-baseline
agents/                         planner, implementer, checker, reviewer, backlog
skills/workflow/                shared contract (SKILL.md) + orchestration.md for commands
skills/profile-*/               python, docs, config, generic
skills/task-*/                  the 10 commands
hooks/hooks.json, scripts/guard.py, scripts/task_list.py, scripts/prune_task.py,
scripts/autopilot.py
templates/task-flow.md          project config example
```
