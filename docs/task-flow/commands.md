# Commands

Paths such as `scripts/…`, `skills/…`, `agents/…`, `templates/…` and `bench/…` are inside `plugins/task-flow/`.

Plugin commands are namespaced, so each is `/task-flow:<name>`:

| Command | What it does |
| --- | --- |
| `task-new <description>` | Creates `.work/<id>/task.md` and `status.md`; asks at most 2 questions |
| `task-plan [id]` | Runs the planner, shows the plan, asks you to approve, edit or cancel |
| `task-implement [id]` | Requires approval; runs the baseline check first (creates the branch and worktree); runs the implementer: the subagent, or `implement.py` when `[roles] implementer` names a profile |
| `task-check [id]` | Runs the checker for the current round |
| `task-review [id]` | Runs the reviewer for the current round |
| `task-run <description \| id> [--auto]` | All of the above, with the approval gate and the 3-round cap |
| `task-autopilot [<requirements> \| <run-id> \| --tasks <id>[,<id>...]] [--from-repo] [--semi \| --full] [--budget <tokens>] [--auto]` | Gathers requirements, plans them as a stack of tasks, shows tasks and token estimates, then runs them one after another; `--tasks` runs existing prepared tasks stacked in the given order (see Autopilot) |
| `task-list [--all]` | Open tasks as a table, ordered by what to do next; `--all` includes done ones |
| `task-status [id]` | Phase, round and last verdict for one task or all |
| `task-finish [id]` | After `APPROVED`: summary, commit message, PR text; local commit; asks before push or PR; last, prunes `.work/<id>/` to its `.md` and `impl-*.json` files (not asked) |
| `task-release [<version> \| --major \| --minor \| --patch] [--target <name>] [--since <ref>] [--dry-run]` | Releases one target: preconditions, version proposal, checks, changelog entry, one approval, version bump, release commit and annotated tag; push only after a separate question (see Releasing) |
| `task-doctor [--online]` | Read-only check of the environment and configuration (uv, git, permissions, `models.toml`, keys); prints a fix for every WARN and FAIL (see Doctor) |

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
