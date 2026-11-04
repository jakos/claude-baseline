# Setting up task-flow

Paths such as `scripts/…`, `skills/…`, `agents/…`, `templates/…` and `bench/…` are inside `plugins/task-flow/`.

## What you need

- [uv](https://docs.astral.sh/uv/) on `PATH` — the guard hook and every
  `uv run --script` need it;
- git, with worktree support;
- Claude Code;
- optionally, a Mistral account, if you want the external implementer (see
  [External implementer](external-implementer.md#external-implementer)).

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

Then run `/task-flow:task-doctor`: it checks this machine, this session and this
repository (uv, git, permissions, `models.toml`, API keys) and prints the fix for every
WARN and FAIL — see [Doctor](#doctor).

## Starting Claude Code with the plugin directories

To run the working tree of this repository instead of the installed plugin, start Claude
Code with both plugin directories in `CLAUDE_CODE_PLUGIN_DIRS` (see
[Working on this repo](../development.md#working-on-this-repo)). PowerShell:

```powershell
$env:CLAUDE_CODE_PLUGIN_DIRS = "$PWD\plugins\dev-baseline;$PWD\plugins\task-flow"; claude
```

POSIX shells:

```bash
CLAUDE_CODE_PLUGIN_DIRS="$PWD/plugins/dev-baseline:$PWD/plugins/task-flow" claude
```

The separator is `;` on Windows and `:` elsewhere; one-off,
`claude --plugin-dir <dir>` also loads one directory.

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

## Keep ANTHROPIC_API_KEY out of the Claude Code session

If `ANTHROPIC_API_KEY` is set in the environment of a Claude Code session, Claude Code
itself bills by API token instead of the subscription. Check whether it is set, and unset
it for the session. PowerShell: `Remove-Item Env:ANTHROPIC_API_KEY`. POSIX:
`unset ANTHROPIC_API_KEY`. A Claude API profile for the external implementer names
another variable in `api_key_env` (e.g. `TASK_FLOW_ANTHROPIC_KEY`); `models.py show`
prints a warning for `api_key_env = "ANTHROPIC_API_KEY"`.

## Mistral account and API key

Create the key in Mistral's console (an external website). Mistral bills pay-as-you-go
per token; see the provider's own pages for pricing and limits. Privacy: the worktree
code and the task files are sent to the provider configured in the profile; the
training opt-out is set at the provider, not here.

## Setting the key

Set the key in the environment Claude Code is started from, then restart Claude Code
from that environment. PowerShell, for the session and persistently in `User` scope:

```powershell
$env:MISTRAL_API_KEY = "..."
# persistently, for new processes:
[Environment]::SetEnvironmentVariable("MISTRAL_API_KEY", "...", "User")
```

POSIX shells, in the current shell and in your shell profile (e.g. `~/.zshrc`):

```bash
export MISTRAL_API_KEY=...
```

The values are placeholders (`...`): paste the real key once, and never commit it.

## models.toml for the external implementer

The file lives at `~/.claude/task-flow/models.toml` (user scope, every repository) or
`.claude/task-flow.models.toml` in the repository (project scope); the project file wins
key by key. A minimal example with a placeholder model id — see
[Configuration](configuration.md) and
[templates/models.toml](../../plugins/task-flow/templates/models.toml) for every key:

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

## Doctor

`/task-flow:task-doctor` runs `scripts/doctor.py`, a stdlib-only, read-only check of this
machine, this Claude Code session and this repository: `uv` on `PATH`, the Python the
scripts need, `git` with worktree support, the repository (work tree, a commit, a clean
tree), which task-flow the session loaded, `.claude/task-flow.md`, the settings files, the
recommended permission rules, `ANTHROPIC_API_KEY`, `models.toml`, and every profile the
`[roles] implementer` uses (enabled, key, `base_url`, limits, driver; for a `driver = "cli"`
profile the executable of its `command` instead of `base_url`/limits). Each check is one
row — PASS, WARN or FAIL — and every WARN and FAIL has its exact fix under `## Fixes`. The
exit code is 0 when no check is FAIL, 1 otherwise. `--online` adds one authenticated
`GET <base_url>/models` per API profile in use — the provider's model list, which costs no
tokens — and says whether the configured `model` is among the listed ids; a `cli` profile
is never probed online (no `/models` request, no agent run — both would spend
subscription or API capacity), so its saved login stays unverified until you run the CLI
yourself. There is no
`--fix`: the doctor changes nothing, and it never prints a secret (key values are
redacted).

## First smoke test

1. `/task-flow:task-doctor` — every check PASS, or follow the printed fixes;
2. with a key set, `/task-flow:task-doctor --online` — the provider's model list contains
   your `model` id;
3. `uv run --script plugins/task-flow/scripts/models.py roles` — the roles resolve and
   `[roles] implementer` shows your profile;
4. a tiny `/task-flow:task-run "<request>"` — it plans, you approve, one round runs, and
   the result is APPROVED or a concrete fix list.

## Running a task and an autopilot run with the external implementer

`/task-flow:task-run "<request>"` (or `task-run <id>`) runs one task end to end;
`/task-flow:task-autopilot [<requirements> | <run-id> | --tasks <id>[,<id>...]] [--from-repo] [--semi | --full] [--budget <tokens>] [--auto]`
runs a stack of them. A failed external round (exit 1 or 2 of `scripts/implement.py`)
leaves the phase `implementing`: rerun the same command and the round is not counted
again; in an autopilot run the run pauses and `/task-flow:task-autopilot <run-id>`
resumes it. The `External USD` column in `run.md` accumulates the `cost_usd` of every
finished external round from `impl-<n>.json` (a null `cost_usd` — a CLI subscription
round or unknown usage — is recorded as `unknown`, not 0).

## What to look at in /usage

`/usage` shows the session and weekly limits of the Claude roles (planner, reviewer,
checker, and the Claude implementer subagent). External cost is not in `/usage`; it is
in `impl-<n>.json` per round and in the provider's console.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| The key is not visible to Claude Code | it was set in another shell, or Claude Code was not restarted from that environment | set `$env:MISTRAL_API_KEY` / `export MISTRAL_API_KEY` in the starting shell and restart Claude Code; a refused round says `refused: … without its key`, and `/task-flow:task-doctor` reports the missing key |
| A round runs longer than 600 s | the external round runs in the background; `request_timeout_seconds` (default 300) bounds a request and `command_timeout_seconds` (default 600) a command | watch `--heartbeat` and `impl-<n>.progress.jsonl` to see what the slow round is doing |
| `__pycache__` got staged | the Claude implementer subagent runs `git add -A`, which also stages untracked artifacts in a repository without a `.gitignore` | add a `.gitignore` entry, or unstage the artifacts before finishing |
| A command was refused | `scripts/rules.py` denies it for the role: no shell metacharacters, no push, PR, deploy or commit, and on Windows a `.cmd` tool goes through `scripts/launch.py` | run the command in a form the rules allow (for example the underlying `.exe` directly, without shell metacharacters), or do that step yourself outside the task run |
