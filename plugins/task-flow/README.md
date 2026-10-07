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
  → /task-flow:task-finish commits, then asks before push or PR
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
| `task-status [id]` | Phase, round and last verdict for one task or all |
| `task-finish [id]` | After `APPROVED`: summary, commit message, PR text; local commit; asks before push or PR |

`--auto` skips only the plan approval question. It never allows an early stop, a push, a PR
or a deploy. A run stops only at the approval gate (skipped by `--auto`), a `BLOCKED`
agent, `APPROVED`, no progress, or the round cap; the last four apply with or without
`--auto`.
Every run can be resumed with `task-run <id>` or the single-step commands, because the
phase is in `status.md`.

## What enforces what

Prompts are advice; a hook is a rule. `scripts/guard.py` runs before every edit and shell
command and applies only to task-flow's own agents (it reads `agent_type` from the hook
input):

- planner, checker and reviewer can write only under `.work/`;
- no task-flow agent can push, open a PR, `kubectl`/`oc apply`, `helm install`, `docker
  push` or `terraform apply`;
- no task-flow agent can commit — changes stay staged until `/task-flow:task-finish`;
- planner and reviewer cannot run git commands that change the tree or branches.

Commands are matched as raw text, split into `;`/`&`/`|`/newline segments, without
regard to quotes. A `git` segment with whitespace followed by a mutating verb (`add`,
`checkout`, `reset`, `worktree add`, ...) is denied for planner and reviewer; one with
whitespace followed by a commit or push verb is denied for every task-flow agent.

Run the guard's test with
`uv run --script plugins/task-flow/tests/test_guard.py`.

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
  instructions, not hooks.

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
agents/                         planner, implementer, checker, reviewer
skills/workflow/                shared contract (SKILL.md) + orchestration.md for commands
skills/profile-*/               python, docs, config, generic
skills/task-*/                  the 8 commands
hooks/hooks.json, scripts/guard.py
templates/task-flow.md          project config example
```
