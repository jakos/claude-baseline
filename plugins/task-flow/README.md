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
| `task-autopilot [<requirements> \| <run-id>] [--from-repo] [--semi \| --full] [--budget <tokens>] [--auto]` | Gathers requirements, plans them as a stack of tasks, shows tasks and token estimates, then runs them one after another (see Autopilot) |
| `task-list [--all]` | Open tasks as a table, ordered by what to do next; `--all` includes done ones |
| `task-status [id]` | Phase, round and last verdict for one task or all |
| `task-finish [id]` | After `APPROVED`: summary, commit message, PR text; local commit; asks before push or PR; last, prunes `.work/<id>/` to its `.md` and `impl-*.json` files (not asked) |

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
`uv run --script plugins/task-flow/tests/test_prune_task.py`, the autopilot's with
`uv run --script plugins/task-flow/tests/test_autopilot.py`, the model config's with
`uv run --script plugins/task-flow/tests/test_models.py` and the external implementer's with
`uv run --script plugins/task-flow/tests/test_implement.py`.

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

## External implementer

`scripts/implement.py` performs ONE implementer round with an OpenAI-compatible chat model
instead of the Claude `implementer` subagent: the Mistral API, any OpenAI-compatible host,
or a local llama.cpp server. It follows the same contract as `agents/implementer.md` (that
file's body is its system prompt, plus the Conventions of the plan's profiles).

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
resumes it. An external round adds nothing to the task's `Tokens used`; the run's History
records `implementer <profile>: tokens n/a`, so the burn-rate and budget triggers
undercount such tasks until a later cost-accounting task.

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
external model's commands; `implement.py` applies its own text matching, with the limits
below.

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
`implementer` only, `script` for `checker` only. `implement.py` reads only the profiles and
`[roles] implementer`.

A minimal profile (the model id is a placeholder; take a real one from `GET /v1/models`):

```toml
[profiles.mistral]
driver = "openai-loop"
base_url = "https://api.mistral.ai/v1"
model = "<model id from GET /v1/models>"
api_key_env = "MISTRAL_API_KEY"
billing = "api"
```

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
ignored. `checker = "script"` is reserved for a later task-flow version and refused until
then. Caveat: with `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` on or
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
commands `guard.py` denies for the implementer (the regexes are imported from it, not
copied). On a retryable failure that outlasts `retries`, the run restarts on the next
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
- There is no shell: no pipes, redirects or `&&` in a command. On Windows, `.cmd` and
  `.bat` tools (`npm`, `npx`, `mvn.cmd`, `gradlew.bat`) cannot be started for that reason
  (follow-up task `windows-cmd-shim-commands`).
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
scripts/autopilot.py, scripts/implement.py, scripts/models.py
templates/task-flow.md          project config example
templates/models.toml           commented models.toml example (profiles, [roles])
```
