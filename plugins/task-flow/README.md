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
  → /task-flow:task-finish commits, asks before push or PR, then prunes the task folder to its .md files and impl-*.json metrics
```

The models in the diagram are the defaults from each agent file's frontmatter. An
optional `models.toml` can change the Claude model of any role and can hand the
implementer role to a non-Claude model; see
[Choosing Claude models per role](#choosing-claude-models-per-role) and
[External implementer](#external-implementer).

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
| `task-implement [id]` | Requires approval; runs the baseline check first (creates the branch and worktree); runs the implementer: the subagent, or `implement.py` when `[roles] implementer` names a profile |
| `task-check [id]` | Runs the checker for the current round |
| `task-review [id]` | Runs the reviewer for the current round |
| `task-run <description \| id> [--auto]` | All of the above, with the approval gate and the 3-round cap |
| `task-autopilot [<requirements> \| <run-id> \| --tasks <id>[,<id>...]] [--from-repo] [--semi \| --full] [--budget <tokens>] [--auto]` | Gathers requirements, plans them as a stack of tasks, shows tasks and token estimates, then runs them one after another; `--tasks` runs existing prepared tasks stacked in the given order (see Autopilot) |
| `task-list [--all]` | Open tasks as a table, ordered by what to do next; `--all` includes done ones |
| `task-status [id]` | Phase, round and last verdict for one task or all |
| `task-finish [id]` | After `APPROVED`: summary, commit message, PR text; local commit; asks before push or PR; last, prunes `.work/<id>/` to its `.md` and `impl-*.json` files (not asked) |
| `task-release [<version> \| --major \| --minor \| --patch] [--target <name>] [--since <ref>] [--dry-run]` | Releases one target: preconditions, version proposal, checks, changelog entry, one approval, version bump, release commit and annotated tag; push only after a separate question (see Releasing) |

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

## Releasing

`/task-flow:task-release` releases one target — a plugin or a project — in one controlled
procedure, and `scripts/release.py` computes every decision, so the command is a thin
wrapper around it. The procedure is **Release** in
[skills/workflow/orchestration.md](skills/workflow/orchestration.md): list the targets,
`check` (every precondition, one JSON report), `verify` (the target's checks, run in the
background), optionally `apply --dry-run`, then **one approval gate — always asked, there
is no `--auto` for it** — then `apply` (the version bump in every version file, the
changelog entry, the release commit `chore(release): <target> <version>` and the annotated
tag), and finally a separate push question, default no. Nothing changes before the
approval. `--dry-run` runs check, verify and `apply --dry-run` and asks nothing.

The targets live in `.claude/task-flow.release.toml` (override with `--config <path>`);
[`templates/release.toml`](templates/release.toml) is a commented example. The file is
parsed strictly with `tomllib` — unknown keys anywhere are a config error (exit 2). Schema:

```toml
[targets.<name>]
tag = "<name>-{version}"          # required; must contain {version} exactly once
changelog = "path/CHANGELOG.md"   # required, repo-relative
scope = ["path", ...]             # required, non-empty; only commits touching these count
branches = ["main"]               # optional, default ["main"]
checks = [["cmd", "arg"], ...]    # optional, default []; each an argument list, no shell quoting
[[targets.<name>.version_files]]  # required, at least one
path = "package.json"             # repo-relative
format = "json"                   # "json" | "toml"
selector = "version"              # dot-separated; a segment is key or key[field=value]
```

The selector picks the version value: `version` (plugin.json, package.json),
`plugins[name=task-flow].version` (a marketplace-style list, exactly one element must
match), `project.version` (pyproject.toml), `package.version` (Cargo.toml). TOML files
allow only plain `key` segments, because the write is a single-line replacement.

Version rule: strict `MAJOR.MINOR.PATCH`. With no explicit version and no flag, a breaking
change (`!` after the type or scope, or `BREAKING CHANGE:` in the body) gives major —
except below 1.0.0, where it gives minor and never an automatic 1.0.0 (`--major` on 0.x
does give 1.0.0); any `feat` gives minor; anything else gives patch. An explicit version is
taken as given and must be higher than the current one. The commit range starts at the
highest tag matching the target's tag pattern; `--since <ref>` overrides it. **The first
release has no tag: `check` refuses with `no-base` and asks for `--since <ref>`**, so the
first changelog entry states its starting point once, explicitly.

Two rollbacks: pin an earlier tag (check out `<target>-<old version>`, or pin an
installed plugin's marketplace to it as described in [Rolling back](#rolling-back)), or
simply not push — the release commit and the tag stay local, and you may remove them
yourself; the command never deletes or moves a tag, never force-pushes, and never pushes
without its own question.

The checks list has no shell glob: each `plugins/task-flow/tests/test_*.py` is one
`["uv", "run", "--script", <file>]` entry in `.claude/task-flow.release.toml`, so a new test
file must be added there by hand.

## What enforces what

Prompts are advice; a hook is a rule. The rules live in `scripts/rules.py`
(`check_command`, `check_write`) — one implementation used by the hook
`scripts/guard.py`, which runs before every edit and shell command and applies only to
task-flow's own agents (it reads `agent_type` from the hook input), and by
`scripts/implement.py` for the external implementer (role `external-implementer`) and
`scripts/gates.py` for the script checker (role `checker`):

- planner, backlog, checker and reviewer can write only under `.work/`;
- no task-flow agent can push, open a PR, `kubectl`/`oc apply`, `helm install`, `docker
  push` or `terraform apply`;
- no task-flow agent can commit — changes stay staged until `/task-flow:task-finish`;
- planner, backlog and reviewer cannot run git commands that change the tree or branches.

Commands are matched as raw text, split into `;`/`&`/`|`/newline segments, without
regard to quotes. A `git` segment with whitespace followed by a mutating verb (`add`,
`checkout`, `reset`, `worktree add`, ...) is denied for planner and reviewer; one with
whitespace followed by a commit or push verb is denied for every task-flow agent.

Run the rules' test with
`uv run --script plugins/task-flow/tests/test_rules.py`, the guard's with
`uv run --script plugins/task-flow/tests/test_guard.py`, the task list's with
`uv run --script plugins/task-flow/tests/test_task_list.py`, the prune's with
`uv run --script plugins/task-flow/tests/test_prune_task.py`, the autopilot's with
`uv run --script plugins/task-flow/tests/test_autopilot.py`, the deterministic gates' with
`uv run --script plugins/task-flow/tests/test_gates.py`, the model config's with
`uv run --script plugins/task-flow/tests/test_models.py`, the external implementer's with
`uv run --script plugins/task-flow/tests/test_implement.py`, the launcher's with
`uv run --script plugins/task-flow/tests/test_launch.py` and the release script's with
`uv run --script plugins/task-flow/tests/test_release.py`.

Limits of the guard, stated plainly (they apply to `implement.py`'s `run_command` too,
which uses the same rules from `scripts/rules.py` with the `external-implementer` role):

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

## External implementer

**Experimental.** `scripts/implement.py` performs ONE implementer round with an
OpenAI-compatible chat model instead of the Claude `implementer` subagent. It is written
for the Mistral API, any OpenAI-compatible host or a local llama.cpp server, but only the
Mistral API has been exercised so far (see [Limits, stated plainly](#limits-stated-plainly));
the configuration and the `impl-<n>.json` format may still change between versions. To
switch it off again, see [Rolling back](#rolling-back). It follows the same contract as
`agents/implementer.md` (that file's body is its system prompt, plus the Conventions of
the plan's profiles).

### Routing

With `[roles] implementer = "<profile>"` in `models.toml`, `task-run`, `task-implement` and
the autopilot loop run `implement.py` for the implement phase instead of the
`task-flow:implementer` subagent. With `claude`, any `claude:*` value or no config, the
subagent runs with the `claude:*` model passed as its subagent call's `model` argument
(see "Choosing Claude models per role"); with bare `claude` or no config, exactly as
before. The orchestrator resolves the roles with `models.py roles`. `implement.py` runs in
the background and the orchestrator reads its exit code and result line. A failed round
(exit 1 or 2) leaves the phase `implementing`; the same command (`task-run <id>` or
`task-implement <id>`) runs that round again, and whatever the failed round already staged
stays in the worktree, so the next run starts from that state. In an autopilot run a
failed round pauses the run (also with `--auto`) and `/task-flow:task-autopilot <run-id>`
resumes it. An external round adds nothing to the task's `Tokens used` (that column stays
Claude subagents only); after each external round that exits 0, 1 or 3 the orchestrator
adds the round's `cost_usd` from `impl-<n>.json` to the task's `External USD` column in
`run.md`, in plain decimal notation (`autopilot.py` also accepts scientific notation; 6
decimal places are enough because `implement.py` rounds `cost_usd` to 6) — exit 2 adds
nothing — and the run's History records `implementer <profile>: <cost_usd> USD external`.
`report.md` reports both currencies:
Claude tokens used and external USD. The burn-rate and budget triggers count Claude tokens
only, compared against estimates whose implement term is 0 when the implementer is an
external profile (`autopilot.py estimate|next --implementer-kind profile`).

Example `models.toml` (the model id is a placeholder):

```toml
[profiles.mistral]
driver = "openai-loop"
base_url = "https://api.mistral.ai/v1"
model = "<model id from GET /v1/models>"
api_key_env = "MISTRAL_API_KEY"
billing = "api"

[roles]
implementer = "mistral"
```

Set the key in the environment Claude Code is started from. PowerShell:

```powershell
$env:MISTRAL_API_KEY = "..."
# persistently, for new processes:
[Environment]::SetEnvironmentVariable("MISTRAL_API_KEY", "...", "User")
```

POSIX shells: `export MISTRAL_API_KEY=...`. Restart Claude Code from that environment.
The orchestrator needs the permission `Bash(uv run:*)` to start the scripts.

Privacy: the provider receives the worktree code and the task files. Opting out of
training is set at the provider, not here. The guard hook (`guard.py`) does not see the
external model's commands; `implement.py` calls the same rules from `scripts/rules.py`
with the `external-implementer` role, with the limits below.

To try it by hand:

```
uv run --script plugins/task-flow/scripts/models.py roles
uv run --script plugins/task-flow/scripts/models.py role implementer
uv run --script plugins/task-flow/scripts/models.py show <profile>
```

then the `implement.py` command:

```
uv run --script plugins/task-flow/scripts/implement.py \
    --task-dir .work/<task-id> --worktree <path|none> --round <n> [--profile <name>] \
    [--heartbeat <seconds>] [--verbose]
```

`--worktree none` means the repository root, which is the parent of the `.work` folder that
holds the task. Without `--profile`, `[roles] implementer` must name a profile.
`--heartbeat <seconds>` sets the interval of the heartbeat lines on stderr while a request or
a command runs (default 30, `0` disables, a negative value is refused); `--verbose` also
logs the model's text content of each response. Progress goes to stderr; stdout gets one line:

| Exit | stdout line | Meaning |
| --- | --- | --- |
| 0 | `ok: <k> files changed (<profile>/<model>, <turns> turns, $<cost>)` | round done, changes staged |
| 1 | `failed: <reason>` | the loop ran but did not finish: every profile unavailable, a non-retryable HTTP error, `max_turns`, `max_cost_usd` or `max_context` exceeded |
| 2 | `refused: <reason>` | nothing was sent to any model: bad arguments or config, plan not approved, `checks-0.md` or `review-<n-1>.md` missing, worktree missing, profile unknown, disabled or without its key |
| 3 | `blocked: <reason>` | the model reported that the plan is wrong |

Prerequisites when running by hand are listed under "Limits, stated plainly" below.

### Configuration: `models.toml`

Two optional TOML files: `~/.claude/task-flow/models.toml` (user) and
`<repo-root>/.claude/task-flow.models.toml` (project). Profiles merge by name, then key by
key; `[roles]` merges key by key; the project wins. Unknown tables and keys are errors that
name the file and the key. Keys of `[profiles.<name>]`:

| Key | Meaning | Default |
| --- | --- | --- |
| `driver` | `"openai-loop"` (required) | |
| `base_url` | `http(s)://` URL; `/chat/completions` is appended (required) | |
| `model` | model id (required) | |
| `api_key_env` | NAME of the environment variable holding the key, never the key; required unless `billing = "local"` | none |
| `billing` | `subscription`, `api` or `local` (required) | |
| `max_turns` | completion requests allowed on one profile | 60 |
| `max_cost_usd` | cap over the whole run, all profiles together | none |
| `max_context` | fail when a response reports more prompt tokens | none |
| `price_input_per_mtok`, `price_cached_input_per_mtok`, `price_output_per_mtok` | USD per 1M tokens, for the cost figure | 0 |
| `fallback` | other profiles to try, in order, when this one stays unavailable | `[]` |
| `enabled` | `false` disables the profile | `true` |
| `retries`, `retry_base_seconds` | retries per request on 408, 429, 5xx, timeouts and connection errors; exponential backoff, `Retry-After` honoured up to 60 s | 3, 2 |
| `request_timeout_seconds`, `command_timeout_seconds` | per request, per `run_command` call | 300, 600 |
| `temperature`, `max_output_tokens` | sent as `temperature` and `max_tokens` | provider default |
| `extra_body` | table merged into the request body, e.g. reasoning effort; `model`, `messages`, `tools`, `tool_choice` are reserved | none |

`[roles]` keys are `planner`, `implementer`, `checker`, `reviewer`, `backlog`. Values:
`claude`, `claude:opus`, `claude:sonnet`, `claude:haiku` for any role, a profile name for
`implementer` only, `script` for `checker` only (see
[Deterministic gates](#deterministic-gates-scriptsgatespy)). `implement.py` reads only
the profiles and `[roles] implementer`.

`[gates]` holds the settings of `scripts/gates.py`, the script checker: only
`command_timeout_seconds` (number > 0), the per-command timeout of a verification
command, default 600. It merges user → project key by key like `[roles]`; unknown keys
are errors.

A minimal profile (the model id is a placeholder; take a real one from `GET /v1/models`):

```toml
[profiles.mistral]
driver = "openai-loop"
base_url = "https://api.mistral.ai/v1"
model = "<model id from GET /v1/models>"
api_key_env = "MISTRAL_API_KEY"
billing = "api"
```

This repository's own [`.claude/task-flow.models.toml`](../../.claude/task-flow.models.toml)
is an example of a project file: four Mistral profiles (`mistral-large`, `mistral-small`,
`glm-flash`, `deepseek-flash`), each with `max_turns = 60` and `max_cost_usd = 1.50`, and
`[roles] implementer = "mistral-large"`. It overrides `~/.claude/task-flow/models.toml`
key by key and holds no secrets: `api_key_env` names the environment variable
`MISTRAL_API_KEY`, never the key itself.

`uv run --script plugins/task-flow/scripts/models.py show <profile> [--project <repo-root>]`
prints the resolved profile and `api_key: set`, `api_key: missing` or `api_key: none needed` (no `api_key_env`), never the key. It
exits 2 for an unknown profile, no config or invalid config.
`models.py role <role> [--project <repo-root>]` prints the resolved value of one `[roles]`
key (`claude` when unset or without config) and exits 2 for invalid config or an unknown role.
`models.py roles [--project <repo-root>]` prints all five roles as one JSON object with
sorted keys — the orchestrator reads it to pass each subagent's `model` — e.g.
`{"backlog": {"kind": "claude", "model": null, "value": "claude"}, "checker": {"kind": "claude", "model": "haiku", "value": "claude:haiku"}, ...}`;
it exits 2 for invalid config.

### Choosing Claude models per role

`[roles]` also picks the Claude model of every Claude-run role. Values: `claude`,
`claude:opus`, `claude:sonnet` or `claude:haiku` for any role; a profile name for
`implementer` only; `script` for `checker` only. A `claude:<model>` value is passed to the
subagent call as its `model` argument and takes precedence over the agent file's frontmatter;
bare `claude` passes nothing, so the frontmatter default applies; with no `models.toml` at
all, nothing changes. A misspelled role or value is refused with exit 2, never silently
ignored. `checker = "script"` runs `scripts/gates.py` instead of the checker subagent —
see [Deterministic gates](#deterministic-gates-scriptsgatespy). Caveat: with
`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` on or
`CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` set, Claude Code ignores the per-call model.

| Role | Frontmatter default |
| --- | --- |
| `planner` | opus |
| `implementer` | sonnet |
| `checker` | haiku |
| `reviewer` | opus |
| `backlog` | opus |

[templates/models.toml](templates/models.toml) is a commented example with every key, each
role at its frontmatter default, so copying it changes no model. Example — the reviewer on
Sonnet to cut cost, the same reason dev-baseline's `code-reviewer` runs on Sonnet:

```toml
[roles]
reviewer = "claude:sonnet"
```

### What it does and records

The model gets seven tools: `read_file`, `write_file`, `edit_file` (the old text must occur
exactly once), `list_files`, `grep`, `run_command` and `blocked`. The file tools are
confined to the worktree and cannot write under `.git` or `.work`. `run_command` denies the
commands `rules.py` denies for the `external-implementer` role — the same rules the
`guard.py` hook applies to the Claude agents. On a retryable failure that outlasts `retries`, the run restarts on the next
`fallback` profile with the same prompt and a note that the worktree may hold partial
changes; any other 4xx fails without fallback.

After the loop it stages what the round produced and never commits:

- tracked modifications and deletions (`git add -u`), including the ones commands made;
- a pre-existing untracked file only when the model wrote or edited it with `write_file` /
  `edit_file`;
- any other new file — one the file tools created or one a command created (`run_command`,
  e.g. a generated source file or a lock file) — unless `.gitignore` or `.git/info/exclude`
  ignores it;
- never a build artifact: a path with a `__pycache__/`, `.pytest_cache/`, `.ruff_cache/` or
  `.mypy_cache/` component, a `*.pyc` / `*.pyo` file, `.DS_Store` or `Thumbs.db`. An
  artifact is never staged while untracked, even when the model wrote it; a tracked
  artifact is a project decision and follows `git add -u`.

It appends to `status.md` only under
`## Log`: one `round <n> implementer: <path> (A|M|D)` line per changed file (or `no files
changed`) on ok, `BLOCKED: <reason>` on blocked, nothing on failed. It also writes
`<task-dir>/impl-<n>.json` for exit 0, 1 and 3: `profile`, `model`, `turns`, `tokens`
(`input`, `cached`, `output`), `cost_usd`, `duration_seconds`, `status`, `reason`,
`fallback_chain`, `settings` (`temperature`, `max_output_tokens`, `extra_body`) and
`files_changed`, plus the timing keys `model_seconds` (sum of all HTTP attempt durations,
successful or not, excluding backoff waits), `retry_wait_seconds` (sum of the backoff sleeps),
`command_seconds` (sum of the `run_command` durations), `slowest_request` (`turn`, `profile`,
`seconds`, `outcome` — `ok` or the retry reason — of the slowest single HTTP attempt, or
`null`) and `slowest_command` (`turn`, `command`, `seconds`, `timed_out`, or `null`); every
`fallback_chain` entry also carries `retries`, one `turn`, `reason`, `attempt_seconds`,
`wait_seconds` object per failed attempt that was followed by another one on the same
profile (`[]` when none).

While it runs, stderr shows one line per tool call and one for the final message, each
starting `[implement] turn N (<req> s, <in> in / <out> out, $<cost> total):` — `<req>` the
wall seconds of that turn's request including retries and waits, `<in>`/`<out>` the turn's
prompt/completion tokens (`12.3k` from 1000 on), `<cost>` the running total — and a
`run_command` line reads `run_command [<secs> s] -> <result>`. With `--heartbeat` it also
logs `waiting for <profile> ... <elapsed> s` while a request is in flight and
`running <command> ... <elapsed> s` while a `run_command` runs, so a slow round is
distinguishable from a hung one. `--verbose` additionally logs `turn N text: <content>` for
each response whose content is a non-empty string.

Each counted turn also appends one compact JSON line to
`<task-dir>/impl-<n>.progress.jsonl` — `turn`, `profile`, `tools`, `request_seconds`,
`retries`, `command_seconds`, `tokens` (`input`, `cached`, `output`), `cost_usd`,
`elapsed_seconds` — opened, written, flushed and closed per line, so a killed round still
leaves its complete turns behind. The file is created empty only after the preconditions
pass (a refusal writes nothing) and a re-run of the same round starts it afresh. It is
scratch: `/task-finish` deletes it, because prune keeps only `impl-*.json`.

### Limits, stated plainly

- Running by hand needs a prepared task: the baseline run is done (`checks-0.md` exists),
  the worktree exists and `status.md` says `plan-approved: yes`.
- Commands the model runs are matched as text only. A `python -c` or a script can still
  push or commit; nothing here can see what a program does.
- There is no shell: no pipes, redirects or `&&` in a command, for any tool. On Windows,
  a program that resolves to a `.cmd`/`.bat` file (`npm`, `npx`, `mvn.cmd`,
  `gradlew.bat`) is started through `cmd.exe /d /s /c` with one quoted command line
  (`scripts/launch.py`), and such a command is rejected if it contains `& | < > ^ % !`
  or a newline, inside quotes too — so e.g. `npm run build -- --flag=%X%` or an argument
  with `&` cannot be passed to a `.cmd` tool; call the underlying `.exe` directly
  instead.
- The network access of commands is not blocked.
- The code in the worktree, and the task files, are sent to the provider you configured.
- Commands get only an allowlist of the environment: `PATH`, `PATHEXT`, `SYSTEMROOT`,
  `COMSPEC`, `TEMP`, `TMP`, `TMPDIR`, `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`,
  `LANG`, `LC_*`, `TERM`, `CI`, `VIRTUAL_ENV`, `UV_*` and `PYTHON*`. Tokens, cloud
  credentials and API keys of any other name never reach them.
- `implement.py` stages only what the round produced (see above): pre-existing untracked
  files and build artifacts such as `__pycache__/` are never staged. The Claude
  `implementer` subagent still runs `git add -A` (`agents/implementer.md` step 8), so in a
  repository without a `.gitignore` it stages pre-existing untracked files such as
  `__pycache__/` — add a `.gitignore` entry or unstage them before finishing.
- A new file a command creates is staged unless it is ignored or on the artifact list
  above: in a repository without a `.gitignore`, a `node_modules/` or `dist/` a command
  creates would be staged.
- There is no context compaction. Every request resends the whole conversation so far;
  only each tool result is truncated. With `max_context` set, a response that reports
  more prompt tokens than `max_context` fails the round (exit 1); without it, the round
  runs until the provider refuses the request, which fails it too.
- Only the Mistral API (`https://api.mistral.ai/v1`) has been exercised. Other
  OpenAI-compatible hosts and local llama.cpp servers are untested; their tool calls,
  usage figures and error codes may differ.
- The provider decides a model's status: a model can be in public preview, renamed or
  retired without task-flow knowing. Check the provider's model list (`GET /v1/models`)
  and documentation before you set `model`, and give the profile a `fallback`.

### Rolling back

To stop using the external implementer, set this in the project file
`.claude/task-flow.models.toml`, which wins over the user file:

```toml
[roles]
implementer = "claude"
```

or remove the `implementer` key from `[roles]` in both files (without any `models.toml`,
nothing changes either). `uv run --script plugins/task-flow/scripts/models.py role
implementer` then prints `claude`, and the next round runs the `task-flow:implementer`
subagent again.

To go back to an earlier task-flow release, pin the marketplace to that release's tag.
[`/task-flow:task-release`](#releasing) creates the tags as `task-flow-<version>` (the
`tag` pattern in this repository's `.claude/task-flow.release.toml`), so this works only
for versions released that way; before the first release there is no tag to pin. In a
Claude Code session:

```
/plugin marketplace add jakos/claude-baseline#task-flow-<version> --scope user
```

then, in a shell, `claude plugin update task-flow@claude-baseline`; the pinned version
loads in the next session or after `/reload-plugins`. `#<ref>` takes a branch or a tag,
and `jakos/claude-baseline@<ref>` is the same; see
[Host and maintain a marketplace](https://code.claude.com/docs/en/plugins/host-marketplace)
in the Claude Code documentation. A marketplace is registered once per `name`, so the
pinned catalog takes the place of the unpinned `claude-baseline` one, and the pin applies
to the whole catalog: `dev-baseline` is held at the same tag. To return to the latest
version, add `jakos/claude-baseline` again without `#<ref>` and update the plugin the
same way. Do not use `/plugin marketplace remove` for this: removing a marketplace
uninstalls every plugin installed from it.

## Deterministic gates (scripts/gates.py)

`scripts/gates.py` is a checker with no model in it. With `[roles] checker = "script"`
in `models.toml`, the baseline and check phases run this script instead of the
`task-flow:checker` subagent (see
[orchestration](skills/workflow/orchestration.md)); with any other value or no config,
the subagent runs exactly as before. It reads the first fenced block under
`## Verification commands` of the task's `plan.md`, runs each line from the worktree (or
the repository root), applies `rules.check_command` with the `checker` role, and writes
`.work/<id>/checks-<n>.md` in the contract's format — the same table, failures and
not-run sections the checker agent writes.

```toml
[roles]
checker = "script"
```

Run by hand (round 0 is the baseline; a human may grant rounds above 3):

```
uv run --quiet --script plugins/task-flow/scripts/gates.py \
    --task-dir .work/<task-id> --worktree <path|none> --round <n> [--timeout-seconds S]
```

Exit 0 means `checks-<n>.md` was written, whatever the results (failures are data), and
stdout is one summary line. Exit 2 is a precondition (missing plan, empty command list,
bad arguments, invalid `models.toml`): the reason goes to stderr, nothing is written.

Each non-blank line of the block that does not start with `#` is one command: one
program plus its arguments, no shell. Whitespace outside quotes separates arguments;
`'...'` and `"..."` group their content verbatim and join with adjacent parts
(`--x="a b"` → `--x=a b`); there are no escapes, so a backslash is literal and Windows
paths (`C:\tools\x.exe`) work unchanged on every platform. Unquoted `| & ; < > ( ) $ * ?
` or backticks are refused (they need a shell), and so is a first word of the form
`NAME=value`. Inside quotes they are ordinary characters, so
`git grep --untracked -nE "a|b" -- path` runs `git` with the argument `a|b`. A refused
line is `ERROR` with a `refused:` reason naming the character and is never started.

Order of decisions per line: `rules.check_command(line, "checker")` first (`ERROR`,
`denied: <reason>`), then the split rule above, then program resolution — a word with a
path separator resolves against the run directory (on Windows also via `PATHEXT`),
otherwise `shutil.which`; not found is `Not run`, not FAIL. On Windows a `.cmd`/`.bat`
program starts through `scripts/launch.py`'s `cmd_command_line`, and a cmd
metacharacter in its arguments is `ERROR`. Then the command runs: exit 0 is `PASS`,
non-zero `FAIL`, a timeout `ERROR` (the process tree is killed), an `OSError` at start
`ERROR`.

The per-command timeout is `--timeout-seconds`, else `[gates] command_timeout_seconds`
from `models.toml` (merged user → project, default 600):

```toml
[gates]
command_timeout_seconds = 600
```

stdout and stderr of each command go to one temporary file (never a pipe, so a
surviving grandchild cannot hang the script); the environment is inherited unchanged.
`FAIL` and timeout `ERROR` get an excerpt in `checks-<n>.md`: the output lines matching
a fixed pattern set (pytest/unittest failure names, assertion and exception lines, ruff
diagnostics, compiler-style and `error:`/`fatal:` lines), at most 30 of them plus a
`[... k more ...]` marker; when nothing matches, the last 30 non-blank lines. A timeout
excerpt starts with `timed out after <s> s; the process tree was killed`.

Limits, stated plainly: gates.py runs only the plan's commands — no profile defaults,
so the planner must list every command it wants checked; there is no shell of any kind
(pipes, redirects, `&&`, variables, globs, `cd`); the matching patterns are heuristics,
and a tool with unusual output falls back to its last 30 lines.

## Project configuration

Optional `.claude/task-flow.md` in the target repository — copy
[`templates/task-flow.md`](templates/task-flow.md). It declares extra profiles and
path→profile mappings, exact verification commands per profile (replacing the
defaults), architecture rules, base branch, branch naming, whether to use worktrees, and
what `task-finish` may do without asking. It is Markdown because only agents read it.

Optional `.claude/task-flow.release.toml` configures `/task-flow:task-release` — the
release targets, their version files, tag patterns, changelogs, scopes and checks (see
Releasing, and [`templates/release.toml`](templates/release.toml) for a commented
example). It is TOML because a script parses it strictly, unlike `task-flow.md`.

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
skills/task-*/                  the 11 commands
hooks/hooks.json, scripts/rules.py, scripts/guard.py, scripts/task_list.py,
scripts/prune_task.py, scripts/autopilot.py, scripts/implement.py, scripts/launch.py,
scripts/gates.py, scripts/models.py, scripts/release.py
templates/task-flow.md          project config example
templates/models.toml           commented models.toml example (profiles, [roles], [gates])
templates/release.toml          commented release config example (targets, version files)
```
