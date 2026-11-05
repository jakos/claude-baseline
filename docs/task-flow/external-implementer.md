# External implementer

Paths such as `scripts/…`, `skills/…`, `agents/…`, `templates/…` and `bench/…` are inside `plugins/task-flow/`.

**Experimental.** `scripts/implement.py` performs ONE implementer round with an external
model instead of the Claude `implementer` subagent: an OpenAI-compatible chat model
(`openai-loop` — the Mistral API, any OpenAI-compatible host, a local llama.cpp server) or
Anthropic through the native Messages API with prompt caching (`anthropic-loop`); the
per-provider wire formats live in `scripts/drivers.py`. Or an installed agent CLI
(`cli` — e.g. Codex on a ChatGPT subscription or an invocation-scoped API key), run as one
process by `scripts/cli_driver.py`. Only the Mistral API has been exercised so far (see
[Limits, stated plainly](#limits-stated-plainly));
the configuration and the `impl-<n>.json` format may still change between versions. To
switch it off again, see [Rolling back](#rolling-back). It follows the same contract as
`agents/implementer.md` (that file's body is its system prompt, plus the Conventions of
the plan's profiles).

Optional and experimental: the implementer role can run on a per-token, non-Claude model
through `plugins/task-flow/scripts/implement.py` — Mistral or any OpenAI-compatible host,
or Anthropic through the native Messages API via `scripts/drivers.py` (so far only the
Mistral API has been exercised) — or on an agent CLI (`driver = "cli"`, see
[Agent CLI driver](#agent-cli-driver-cli-codex)) — while Claude plans, checks and reviews by default. A `models.toml` turns it on.

## External planner (a producer role)

The planner role can also name a profile (`[roles] planner = "<profile>"`, or a
`--planner <profile>` override on one invocation of `task-plan`, `task-run`,
`task-autopilot` or `task-epic run`). `scripts/role_run.py` then runs the same planner
agent body and contract as the Claude subagent, but through a transport adapter: the
model gets only `read_file`, `list_files`, `grep` and an argv-list `run_command` held to
`rules.READ_ONLY_PROGRAMS` (checked with `scripts/rules.py` under the real
`external-planner` role: read-only git subcommands and flags only, no `find`/`rg`
action that deletes, executes, preprocesses or writes) — no write or edit tools, no
shell syntax, no redirects, no interpreter execution, no arbitrary commands, and every
resolved path and symlink, in the file tools and in every path-looking argv word alike,
is contained to the selected repository roots. The runner validates the returned artifact
with `scripts/role_contracts.py` against the plan.md contract and the actual stack base's
verification gate, makes exactly one repair attempt on an invalid result, and atomically
publishes only the validated `plan.md` (rejecting unsafe destinations and symlink
escapes); exit 0 published, 3 a valid `BLOCKED: yes` plan, 1 validation/runtime
exhaustion, 2 a usage/config/preflight refusal — 1 and 2 preserve the previous artifact
bytes, phase, round and approval, and never fall back to Claude silently. A
`driver = "cli"` producer profile must carry a verified `read_only_command` (today only
`codex exec --sandbox read-only ... -o {last_message_file}`, with no `--full-auto`,
approval bypass or `-c` override) and its fallbacks too; the final message is captured
by the host, not written by the model. The read-only sandbox is the CLI's own boundary,
not a task-flow guarantee: task-flow refuses unsupported or conflicting producer
commands, but it does not claim complete isolation of every CLI setting or platform.

Each invocation — successful, repaired or failed — writes exactly one bounded
`role-planner-<invocation-id>.json` next to the task artifacts (kept at `/task-finish`;
only scratch/progress is pruned), with the actual profile/model/billing, the
attempt/retry/fallback chain, repair usage, timestamps/duration and nullable
tokens/cost. A `driver = "cli"` subscription round's cost stays `unknown` (null), never
`0`; known subtotals remain additive next to it. In an autopilot or epic run the totals
are recomputed from these invocation ids on resume (idempotent), the planner's USD counts
into each task's existing `External USD` cell, and `autopilot.py estimate` shows the
per-stage Claude and external subtotals (`--planner-kind`). See
[Configuration](configuration.md#external-planner) for examples and override/resume
semantics, and the doctor's `## Stages` table for the method, model and billing of all
five stages.

## Billing modes

What `billing` changes at run time: who pays, and what a failure does. Only the effects
the code has today are drawn — cost comes from the `price_*` keys in every mode, and
`max_cost_usd` caps the whole run.

```mermaid
flowchart TD
  subgraph subg["billing = subscription"]
    claude["Claude roles and the Claude subagent"]
    usage["at the plan limit Claude Code stops; check /usage"]
    breaker["autopilot token breaker uses persisted budget scope"]
    claude --> usage
    claude --> breaker
  end
  subgraph apig["billing = api"]
    key["API key in the runner environment"]
    retry["429 or 5xx: retries, then the fallback profiles"]
    cap["max_cost_usd reached"]
    key --> retry
    key --> cap
  end
  subgraph loc["billing = local"]
    nokey["no key; cost from price_* (0)"]
    lretry["timeout or unreachable server: retries, then fallback"]
    nokey --> lretry
  end
  subgraph clib["driver = cli"]
    clilogin["saved login, or api_key_env via pass_env"]
    clilim["deadline timeout_seconds; cost null (unknown) unless billing = api reports usage"]
    clilogin --> clilim
  end
  exit1["exit 1: the round failed; it pauses or reruns"]
  retry --> exit1
  cap --> exit1
  lretry --> exit1
  clilim --> exit1
```

The diagram cannot carry: the bench labels a `subscription` profile `subscription` in
its cost columns and draws no cost bar for it; a `driver = "cli"` round's cost is null
(unknown) for a subscription profile and is recorded as `unknown`, not 0, in the
autopilot's `External USD` column.

## Two cascades

Where escalation exists today: only to you, the human. Escalation to a stronger model is
not built.

```mermaid
flowchart TD
  subgraph avail["availability cascade"]
    prof["Starting profile: fixed or plan-routed attempt pin"]
    retries["retries: 408, 429, 5xx, timeouts, connection errors"]
    climiss["cli: missing executable or a limit_patterns match"]
    fb["fallback profiles, in order; a disabled one is skipped"]
    unavail["all unavailable: exit 1"]
    paused["paused: the same command reruns the round"]
    prof --> retries
    prof --> climiss
    retries --> fb
    climiss --> fb
    fb --> unavail
    unavail --> paused
  end
  subgraph qual["quality cascade"]
    review["review: CHANGES_REQUESTED"]
    next["next round reuses pinned starting profile"]
    esc["round 3 or no progress: escalate to you"]
    strong["stronger model (planned)"]
    review --> next
    next --> esc
    review -.->|"planned"| strong
  end
  classDef planned stroke-dasharray: 5 5
  class strong planned
```

The diagram cannot carry: after the retries of one profile the next `fallback` profile
is tried within the same round; exit 1 or 2 leaves the phase `implementing`.

## Local model (proof of concept)

This is untested: no local server has been exercised (see
[Limits, stated plainly](#limits-stated-plainly)). A profile for one would be
`[profiles.local]` with `driver = "openai-loop"`, `base_url = "http://<host>:<port>/v1"`
(your placeholders, written here in prose, not in a diagram label), the served `model`
id, `billing = "local"` — which needs no `api_key_env` — and `enabled = false` until you
opt in. The second machine runs an external local OpenAI-compatible server, e.g.
llama.cpp's `llama-server` with a model file (an external tool, not part of this
repository), behind an inbound firewall rule for the port (external: a Windows firewall
cmdlet). Before the first round, check the server from the development machine:
`/task-flow:task-doctor --online` sends one `GET <base_url>/models` per enabled profile
in use, which for a local profile needs no key; if the server is llama.cpp, its
`/health` endpoint is the equivalent external check.

```mermaid
flowchart TD
  dev["dev machine: Claude Code and implement.py"]
  prof["profile with billing = local"]
  lan["LAN"]
  win["second Windows machine"]
  fw["inbound firewall rule for the port"]
  srv["local OpenAI-compatible server and model file"]
  health["health check: task-doctor --online"]
  dev --> prof
  dev --> lan
  lan --> win
  win --> fw
  win --> srv
  health -->|"GET base_url/models"| srv
  dev -->|"before the first round"| health
```

## Routing

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
`/task-flow:task-doctor --online` then verifies the key and the model id against the
provider's model list.
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

## Providers

`scripts/drivers.py` holds one driver per `driver` value; `implement.py` sends the same
prompt, tools, conversation and results through either of them:

| Provider | `driver` | `base_url` | Key variable | Prompt caching | Status |
| --- | --- | --- | --- | --- | --- |
| Mistral | `openai-loop` | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` | provider-side; cached tokens reported in the usage | exercised (this repo's own profiles) |
| OpenAI | `openai-loop` | `https://api.openai.com/v1` | `OPENAI_API_KEY` | provider-side; cached tokens reported in the usage | untested against the real API |
| Anthropic | `anthropic-loop` | `https://api.anthropic.com/v1` | e.g. `TASK_FLOW_ANTHROPIC_KEY` | `cache_control` breakpoints on the request; cache-aware cost | untested against the real API |
| any other OpenAI-compatible host, local llama.cpp | `openai-loop` | the host's base URL | the host's variable | depends on the host | untested |
| an agent CLI, e.g. Codex | `cli` | none (refused) | saved login, or `api_key_env` + `pass_env` with `billing = "api"` | none: `usage_format = "codex-jsonl"` sums the CLI's own reported usage | codex-cli 0.166.0 `exec` flags verified offline; no authenticated run performed |

Mistral (as in this repository's [`.claude/task-flow.models.toml`](../../.claude/task-flow.models.toml)):

```toml
[profiles.mistral]
driver = "openai-loop"
base_url = "https://api.mistral.ai/v1"
model = "mistral-large-4-0"          # or "mistral-small-2603"
api_key_env = "MISTRAL_API_KEY"
billing = "api"
max_turns = 60
max_cost_usd = 1.50
price_input_per_mtok = 0.70
price_cached_input_per_mtok = 0.07
price_output_per_mtok = 2.10
```

OpenAI — **unverified until a first real call**. Reasoning models want `temperature`
unset, `max_tokens_param = "max_completion_tokens"` and the effort in `extra_body`:

```toml
[profiles.openai]
driver = "openai-loop"
base_url = "https://api.openai.com/v1"
model = "<model id from GET /v1/models>"
api_key_env = "OPENAI_API_KEY"
billing = "api"
max_tokens_param = "max_completion_tokens"
# extra_body = { reasoning_effort = "medium" }
```

Claude on the native Messages API, Sonnet 5.5 or Haiku 5.5 — prices are USD per 1M tokens
as of 2026-11-01, check the provider's pricing page; Sonnet 5.5: $2.00 input / $10.00
output / $0.20 cache read, Haiku 5.5: $0.10 / $0.50 / $0.01, cache write (5-minute TTL)
1.25x the input price:

```toml
[profiles.claude]
driver = "anthropic-loop"
base_url = "https://api.anthropic.com/v1"
model = "claude-sonnet-5-5"           # or "claude-haiku-5-5"
api_key_env = "TASK_FLOW_ANTHROPIC_KEY"
billing = "api"
price_input_per_mtok = 2.00
price_cached_input_per_mtok = 0.20
price_cache_write_per_mtok = 2.50     # 1.25x the input price; Haiku: 0.125
price_output_per_mtok = 10.00
# no temperature: the pinned API version rejects it on every model
# extra_body = { output_config = { effort = "medium" } }   # optional
```

Fallback across providers — try Mistral first, then Claude:

```toml
[profiles.mistral]
driver = "openai-loop"
base_url = "https://api.mistral.ai/v1"
model = "mistral-large-4-0"
api_key_env = "MISTRAL_API_KEY"
billing = "api"
fallback = ["claude"]

[profiles.claude]
driver = "anthropic-loop"
base_url = "https://api.anthropic.com/v1"
model = "claude-haiku-5-5"
api_key_env = "TASK_FLOW_ANTHROPIC_KEY"
billing = "api"
```

Never use `api_key_env = "ANTHROPIC_API_KEY"`: in a Claude Code session that variable
makes Claude Code itself bill by API token instead of the subscription. `models.py show`
prints a warning on stderr for such a profile and `implement.py` logs it before the
round; use another name, e.g. `TASK_FLOW_ANTHROPIC_KEY`.

Anthropic also serves an OpenAI-compatible endpoint at the same `base_url`, which works
through `openai-loop` as a quick test path — but without prompt caching and with empty
cached-token usage, so `cache_tokens` stays zero and cost is not cache-aware. Use
`anthropic-loop` for real work.

## Agent CLI driver (`cli`): Codex

With `driver = "cli"` there is no HTTP loop: `scripts/implement.py` builds one prompt —
the implementer instructions, the plan's profile Conventions, `prompt_extras`, the
workflow contract, plan, status, `checks-0.md` and (round n > 1) the previous review's
Fix list — and hands it to ONE agent-CLI process through `scripts/cli_driver.py`, which
runs the configured `command` in the worktree with a deadline (`timeout_seconds`) and
decides the round from the exit code, the final message and (with
`usage_format = "codex-jsonl"`) the summed usage of the CLI's JSONL events. The CLI agent
gets a CLI adapter instead of the API tool protocol: it has its own tools, and the whole
round is that one process. `model` is metadata only — the `-m` argument in `command`
picks the real model, so keep the two consistent.

Only `{worktree}`, `{prompt_file}` and `{last_message_file}` are expanded in `command`
arguments, each argument on its own, with no shell of any kind. `prompt_via` decides how
the prompt travels: `"stdin"` (default) writes it to the child's stdin; `"file"` writes a
UTF-8 temporary file outside the worktree whose path replaces `{prompt_file}` (required
for that transport); `"arg"` appends the whole prompt as one final argument. On Windows a
program that resolves to a `.cmd`/`.bat` (the npm `codex` shims) runs through the guarded
`cmd.exe` line of `scripts/launch.py`, which refuses `& | < > ^ % !` and newlines — so
with a shim use `"stdin"` or `"file"` (never `"arg"`, which would put the whole prompt on
that command line); a configured native executable path (e.g. the `codex.exe` inside a
npm-global install) takes the plain argv path. Which one you use is a per-machine choice;
do not hard-code an installation path in a committed `models.toml`.

Codex on a ChatGPT subscription — the saved login, no key anywhere:

```toml
[profiles.codex]
driver = "cli"
model = "gpt-5.2-codex"              # metadata only; the -m argument below picks the model
billing = "subscription"             # saved login (codex login); no api_key_env needed
command = ["codex", "exec", "--sandbox", "workspace-write", "--json", "--ephemeral", "-m", "gpt-5.2-codex", "-"]
timeout_seconds = 600                # the whole CLI run's deadline (default 600)
prompt_via = "stdin"                 # "stdin" (default), "file" (needs {prompt_file}) or "arg"
usage_format = "codex-jsonl"         # sum turn.completed usage from stdout JSONL; "none" (default) leaves usage unknown
pass_env = []                        # extra env NAMES passed through (default []); the allowlist applies first
limit_patterns = []                  # regexes; a match on the bounded stderr/final output tries the next fallback

[roles]
implementer = "codex"
```

The same CLI on an invocation-scoped API key instead of the saved login:

```toml
[profiles.codex-api]
driver = "cli"
model = "gpt-5.2-codex"
billing = "api"                      # requires api_key_env, passed through pass_env
api_key_env = "CODEX_API_KEY"
pass_env = ["CODEX_API_KEY"]         # the key reaches the CLI; no other secret does
command = ["codex", "exec", "--sandbox", "workspace-write", "--json", "--ephemeral", "-m", "gpt-5.2-codex", "-o", "{last_message_file}", "-"]
usage_format = "codex-jsonl"
timeout_seconds = 900
```

A `cli` profile refuses the API-loop keys (`base_url`, `max_turns`, `max_cost_usd`,
`max_context`, `retries`, `retry_base_seconds`, `request_timeout_seconds`,
`command_timeout_seconds`, `temperature`, `max_output_tokens`, `max_tokens_param`,
`extra_body`, `post_edit`): the CLI owns its internal loop, and `timeout_seconds` is its
deadline — `max_turns`, per-request retries, `post_edit` hooks and live cost/context caps
cannot govern it. `billing = "api"` requires `api_key_env`; `billing = "subscription"`
refuses one.

Setup and a manual smoke test (codex-cli 0.166.0; `--full-auto` no longer exists — use
the flags below, verified against `codex exec --help`):

1. `codex login` in the environment Claude Code starts from (the saved login lives under
   `CODEX_HOME`/`~/.codex`, which the runner's allowlist passes through);
2. in any scratch git repository, one non-interactive run with the prompt on stdin
   (`-`), the sandbox, a fresh ephemeral session, JSONL events and the final message
   written to a file:

```
echo "List the repository's changed files, change nothing, and answer OK" | codex exec --sandbox workspace-write --json --ephemeral -m gpt-5.2-codex -o codex-last-message.txt -
```

The prompt goes in on stdin and the trailing `-` is the only positional argument; the
final message lands in `codex-last-message.txt` in the scratch repository (a relative
path, so the same line works in a POSIX shell and in PowerShell or `cmd`).

No authenticated run was performed for this plugin's own tests; the flags and the
JSONL/final-message behaviour above are the documented, help-verified interface of
codex-cli 0.166.0, not a promise about your subscription's capacity — check the
provider's terms for what your plan includes.

Outcomes: exit 0 is a successful round unless the final message (the last
`item.completed` agent message, or the `{last_message_file}` when the command names
one; with `usage_format = "none"` the final plain stdout) starts with `BLOCKED:`, which
exits 3 and writes the usual `BLOCKED:` Log line. A timeout, a non-zero exit or an
explicit failure event (`turn.failed`/`error`) exits 1 with no Log mutation. A missing
executable or a `limit_patterns` match on the bounded stderr/final output makes that
attempt unavailable and advances the `fallback` chain — exhausted chains end as the usual
failed round, never silently as Claude. Configuration/preflight refusals stay exit 2.
While the process runs, `impl-<n>.progress.jsonl` gets one bounded record per parsed
event, or heartbeat records when `usage_format = "none"`.

Accounting: with `usage_format = "codex-jsonl"`, every `turn.completed` event's usage is
summed once per event (input, cached input, output, and optional reasoning output counted
separately — never double-counted), across turns. `impl-<n>.json` keeps every existing
key and adds a `cli` section with the redacted argv, `prompt_via`, `billing`,
`usage_available` and per-run `cost_usd`: `cost_usd` is null for a `subscription` profile
(its cost is not measurable) and for unknown usage, and for a `billing = "api"` CLI it is
computed from the reported usage and the configured `price_*` keys when available,
otherwise null. Unknown stays unknown — never zero: `tokens` and `cost_usd` are null, the
known-cost subtotal is in `cost_known_usd`, and the stdout line renders `$unknown`. The
autopilot records a null `cost_usd` as `unknown` in the `External USD` column (sticky,
summed as `at least`), not as a number.

Staging after a CLI round is the same as after an API round, plus one recovery: because a
CLI owns its tools it may commit, and when it left a descendant commit on the same task
branch, `implement.py` verifies the ancestry and restores the initial HEAD with
`git reset --soft` before staging, so the CLI's result stays visible to
`git diff --cached`; incompatible history movement (another branch, a non-ancestor HEAD)
fails the round explicitly with the observed head and branch as recovery evidence — no
hard reset, no clean. Pre-existing untracked files and build artifacts stay excluded;
the runner cannot attribute edits to pre-existing untracked files, so they stay
unstaged.

## Post-edit feedback and prompt extras

Two optional profile keys give the external model the after-edit feedback that Claude
subagents get from dev-baseline's `format_python.py` PostToolUse hook. Without them,
nothing the external implementer does changes.

`post_edit` is a list of commands run after each *successful* `write_file` /
`edit_file` call — not after `run_command`, and not after a denied or failed write.
Every literal `{path}` in a command is replaced by the written file's worktree-relative,
`/`-separated path, quoted for the platform; a command without `{path}` still runs
after each write. Each command goes through the same path as `run_command` — the same
`rules.py` checks with the `external-implementer` role, no shell, the worktree as the
working directory, `command_timeout_seconds`, and its duration counted in
`command_seconds` / `slowest_command` — so on Windows a `.cmd`/`.bat` `post_edit`
command has the same limits as a `run_command` one (see
[Limits, stated plainly](#limits-stated-plainly)). Per command, appended to that tool
call's result after a blank line:

```
post_edit: <command as run> (exit code <n>)
<output, capped to 40 lines, then ...[<k> more lines] when cut>
```

or `(denied: <reason>)`, `(error: <message>)` or `(timed out after <s> s and was
killed)` instead of the exit code. A denial, a missing program, a timeout or a non-zero
exit is feedback text only — it never fails, blocks or ends the round. A command that
exits 0 with empty output appends nothing (a clean hook is silent). Because a
`post_edit` command such as a formatter can change the file after the model wrote it,
the model's next `edit_file` may find its `old` text no longer present; the feedback
output tells it.

`prompt_extras` is a list of files read before the round; each file's contents are
appended to the system prompt as `# Extra instructions: <path as configured>`, in list
order, **before** the runner adapter section — so an extra file cannot override the
adapter's rules. `~` is expanded; a relative path resolves against the repository root
(the parent of `.work/`), not the worktree, so an untracked local file works. A missing,
unreadable or non-UTF-8 file refuses the round (exit 2, nothing sent, nothing written):
a typo must not silently drop part of the prompt. A `fallback` profile reuses the
starting profile's `prompt_extras` (one system prompt per round) but runs its own
`post_edit`.

Example — run the project's formatter and habit-hooks on every written file (check
habit-hooks' own documentation for its flags):

```toml
[profiles.mistral]
# ... the keys from the table above ...
post_edit = [
  "uvx ruff format {path}",
  "uvx ruff check --fix {path}",
  "habit-hooks --file {path}",
]
```

habit-hooks' exit codes are all returned as feedback, never as a failure of the round:
0 clean, 1 an enforced finding, 2 the tool itself failed. Caveat: task-flow keeps the
changes staged and uncommitted in the worktree, so with `[scope] changedOnly = true` a
plain `habit-hooks` run (without `--file`) covers every file the task has changed so
far, not only the one just written; `--file` checks one file and ignores snoozes. The
snooze denial above applies to the external implementer too, so `post_edit` cannot run
`habit-snooze --snooze` or `--prune`.

Nothing here is installed or vendored, and the round does not depend on these tools: a
missing program is reported as `error:` feedback, never as a round failure.

To try [ponytail](https://github.com/DietrichGebert/ponytail)'s `AGENTS.md` — downloaded
by you, not vendored here — with the external implementer, reference it via
`prompt_extras`:

```toml
prompt_extras = ["AGENTS.md"]
```

Its benchmark numbers are the author's own.

## What it does and records

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
(`input`, `cached`, `output`), `cache_tokens` (`read`, `write`: prompt-cache read and
write tokens summed over all profiles; on `anthropic-loop` `tokens.input` is one
request's total prompt — uncached + cache-creation + cache-read — and `cache_tokens`
separates the cache parts), `cost_usd`, `duration_seconds`, `status`, `reason`,
`fallback_chain`, `settings` (`temperature`, `max_output_tokens`, `extra_body`) and
`files_changed`, plus the timing keys `model_seconds` (sum of all HTTP attempt durations,
successful or not, excluding backoff waits), `retry_wait_seconds` (sum of the backoff sleeps),
`command_seconds` (sum of the `run_command` durations), `slowest_request` (`turn`, `profile`,
`seconds`, `outcome` — `ok` or the retry reason — of the slowest single HTTP attempt, or
`null`) and `slowest_command` (`turn`, `command`, `seconds`, `timed_out`, or `null`); every
`fallback_chain` entry also carries `retries`, one `turn`, `reason`, `attempt_seconds`,
`wait_seconds` object per failed attempt that was followed by another one on the same
profile (`[]` when none). A `driver = "cli"` round additionally carries a `cli` object —
`driver`, the per-run list (redacted expanded `command`, `shim`, `prompt_via`,
`usage_format`, `billing`, `usage_available`, `cost_usd`, `timed_out`, `exit_code`,
`failure_event`) and `usage_available` — while `tokens`/`cache_tokens`/`reasoning_tokens`
are null when the CLI reported no recognizable usage, `cost_usd` is null when any used
profile's cost is unknown (a `subscription` CLI always), and `cost_known_usd` keeps the
known subtotal next to that null.

While it runs, stderr shows one line per tool call and one for the final message, each
starting `[implement] turn N (<req> s, <in> in / <out> out, $<cost> total):` — `<req>` the
wall seconds of that turn's request including retries and waits, `<in>`/`<out>` the turn's
prompt/completion tokens (`12.3k` from 1000 on), `<cost>` the running total — and a
`run_command` line reads `run_command [<secs> s] -> <result>`. With `--heartbeat` it also
logs `waiting for <profile> ... <elapsed> s` while a request is in flight and
`running <command> ... <elapsed> s` while a `run_command` runs, so a slow round is
distinguishable from a hung one. `--verbose` additionally logs `turn N text: <content>` for
each response whose content is a non-empty string. A `driver = "cli"` round instead logs
one `cli <profile>: exit <n>, <s> s, usage known|unknown` line per process, plus
`running <profile> ... <elapsed> s` heartbeats on stderr while the CLI runs.

Each counted turn also appends one compact JSON line to
`<task-dir>/impl-<n>.progress.jsonl` — `turn`, `profile`, `tools`, `request_seconds`,
`retries`, `command_seconds`, `tokens` (`input`, `cached`, `output`), `cost_usd`,
`elapsed_seconds` — opened, written, flushed and closed per line, so a killed round still
leaves its complete turns behind. A `driver = "cli"` round appends one bounded record per
parsed CLI event (`event`, `elapsed_seconds`, plus `usage` on `turn.completed`), or
`heartbeat` records while no structured events are available (`usage_format = "none"`),
each with `profile` and `elapsed_seconds`. The file is created empty only after the
preconditions pass (a refusal writes nothing) and a re-run of the same round starts it
afresh. It is scratch: `/task-finish` deletes it, because prune keeps only `impl-*.json`.
The inlined prompt itself is never persisted in any artifact; known secret values are
redacted from diagnostics, progress records, final reasons and the recorded command.

## Limits, stated plainly

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
- `post_edit` commands run only after the file tools (`write_file` / `edit_file`), never
  after `run_command`.
- The code in the worktree, and the task files, are sent to the provider you configured —
  and to a `driver = "cli"` profile, the whole prompt goes to the CLI process, which may
  forward it to its own provider.
- Commands get only an allowlist of the environment: `PATH`, `PATHEXT`, `SYSTEMROOT`,
  `COMSPEC`, `TEMP`, `TMP`, `TMPDIR`, `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`,
  `LANG`, `LC_*`, `TERM`, `CI`, `VIRTUAL_ENV`, `UV_*`, `PYTHON*` and `CODEX_HOME`. Tokens,
  cloud credentials and API keys of any other name never reach them; a `cli` profile's
  `pass_env` adds named variables on top of that allowlist (e.g. `CODEX_API_KEY`), and a
  passed credential may reach the CLI's child commands.
- A `driver = "cli"` agent owns its tools and its sandbox: `rules.py` cannot police its
  commits, pushes, network activity or child processes. The runner controls only its
  environment (allowlist + `pass_env`), its working directory (the worktree), its
  deadline (`timeout_seconds`, enforced by killing the process tree) and its result
  capture; `--sandbox workspace-write` in the example `command` is the CLI's own
  confinement, chosen by you, not by task-flow. On your own machine, with your own
  subscription, that is the intended use — check the provider's terms for what your plan
  includes; task-flow makes no capacity guarantee. The doctor never launches the CLI and
  sends no `/models` request for a `cli` profile, so a saved login stays unverified until
  you run the CLI yourself.
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
- Only the Mistral API (`https://api.mistral.ai/v1`) has been exercised against a real
  provider. `openai-loop` on other hosts (OpenAI itself, any OpenAI-compatible host, a
  local llama.cpp server) and `anthropic-loop` are untested against the real APIs; their
  tool calls, usage figures and error codes may differ. `anthropic-loop` pins
  `anthropic-version: 2026-10-21` on every request, so a future API version needs a code
  change.
- Haiku 5.5 (`claude-haiku-5-5`) costs more above 100k prompt tokens per request
  ($0.50 / $2.50 per 1M instead of $0.10 / $0.50); flat profile prices under-count such
  requests. Set `max_context = 100000` on a Haiku profile when the price ceiling matters —
  that fails the round (exit 1) rather than limiting it.
- The provider decides a model's status: a model can be in public preview, renamed or
  retired without task-flow knowing. Check the provider's model list (`GET /v1/models`)
  and documentation before you set `model`, and give the profile a `fallback`.

## Comparing planner variants manually (frozen cases)

There is no paid or automated benchmark for the planner, and none is claimed here. To
compare two planner configurations (e.g. the Claude subagent vs. an external profile)
by hand, keep the comparison frozen and fair:

1. Fix the case set: a handful of real past requests from this repository (their
   `task.md` Goal/Context/Constraints), stored once, never edited between runs.
2. Fix the implementer and the checker for every run — the same role configuration for
   them in all variants, and the same verification commands (the plan's list must retain
   the project gate's inherited checks, which `role_contracts.py` enforces).
3. Fix the probes: run the same hidden checks on every produced `plan.md` — e.g. "every
   acceptance criterion has a `Verify:` reference", "the verification list retains every
   inherited check", "no step edits files outside its declared paths" — plus your own
   acceptance of the plan quality.
4. Repeat each variant at least twice (three times when the verdicts disagree) on the
   frozen inputs, alternating the order, and record every run: profile, model, method,
   turns, tokens, cost or `unknown`, duration, repairs, and the `role-*.json` /
   `impl-*.json` metrics task-flow writes.
5. Accept a variant only when it passes the hidden probes and your acceptance on every
   repeat, and record costs as measured when known and `unknown` when not — never as 0.
   A single run, or a run on inputs edited between variants, is not evidence.

No paid runs are required for acceptance of this feature: the offline suites
(`test_role_run.py`, `test_role_contracts.py`, `test_models.py`) cover the runner,
validation and role resolution with fakes; the protocol above is for a human who wants a
measured quality comparison.

## Rolling back

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
[`/task-flow:task-release`](releasing.md#releasing) creates the tags as `task-flow-<version>` (the
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

## Fixed selection for an attempt

Opt-in deterministic routing uses the approved plan, with the schema and disabled
threshold default described in [Configuration](configuration.md#deterministic-implementer-routing).
Before dispatch use `models.py roles --project <repo-root> --task-dir <absolute-task-directory>`;
taskless diagnostics show only the default and an explicit task-dependent indication.

After approval and successful preflight, `implement.py` publishes one machine-readable
`implementer-routing/1` History event in root `status.md`. It records the band, inputs,
thresholds, configured starting profile, approved-plan digest and rule digest. Every
round's `impl-<n>.json` copies this evidence under `routing`; existing `profile`, `model`
and `fallback_chain` still describe execution. `--profile` must match the pin. Resume
and subsequent rounds reuse it. Plan, rules or selected profile settings that drift
are refused before model contact. Preflight refusal leaves status unchanged.

No max-turns, cost-cap or no-progress failure promotes a model or advances a round.
Existing API/CLI limits and within-round fallbacks apply. Enabling routing on an
already-started unpinned attempt requires an explicit restart/retry. An authorized epic
retry archives prior round evidence and resets round 0; its existing retry History
and archive identify a new pin, while previous History stays intact. Legacy attempts
without active routing retain their previous behavior.

The orchestrator runs `implement.py --prepare-routing` with the ordinary task,
worktree, round and selected profile arguments while status is still round 0. This
validates preflight and publishes the pin without model contact before entering
phase `implementing`. It distinguishes a fresh attempt from an already-started,
unpinned round 1, which is refused rather than silently changing its model.

## Backlog and reviewer trust limits

Configured external backlog and reviewer roles use `scripts/role_run.py` and the same
agent contracts as Claude. No configuration changes the defaults. Backlog metrics live
beside run artifacts as `role-backlog-<invocation-id>.json`; reviewer metrics live beside
task artifacts as `role-reviewer-<invocation-id>.json`. Repairs, failed billable attempts
and fallback usage belong to the same invocation. Run-level backlog USD is separate
from task USD; reviewer USD joins its task. Totals are recomputed by invocation identity
on resume, preserving known subtotals next to null/unknown totals.

For repository gathering, the model returns exactly two JSON string members,
`requirements.md` and `backlog.md`. Both validate before staging. Handled replacement
failures restore the original bytes or absence of both outputs. Abrupt termination
between replacements is outside that guarantee. Ordinary decomposition writes backlog
only and preserves requirements. A blocked backlog creates no tasks.

Review preflight requires a matching round >= 1, status worktree and base, valid plan,
current checks and readable complete touched files. The prompt includes the base diff,
changed-file list, full touched files (base contents for deletions), and declared profile
Review checklists. Oversized, binary, unreadable or incomplete required inputs refuse
before contact. API file and search tools exclude status Log, implementer narratives,
previous reviews and git storage, including through resolved symlinks. API reviewer
command execution is disabled to prevent command-based retrieval of excluded evidence.
The supplied plan and checks remain readable. CLI read-only execution prevents writes
but does not itself hide local files: excluded prompt material is not an OS-level
confidentiality guarantee. A CLI may locate it independently through its own tools.

Independence uses actual current-round metrics, never profile aliases or billing mode.
Provider identity is conservative: API gateways and CLI metadata may be unverifiable.
Strict settings refuse unknown identities; warn mode reports uncertainty or a match.
A syntax validator can reject contradictory approval and malformed evidence; it cannot
establish semantic defect detection. No external reviewer is recommended without
recorded, authorized comparison results using the [evaluation protocol](../../plugins/task-flow/bench/reviewer-evaluation.md).

```mermaid
flowchart TD
  select["Select configured backlog or reviewer"] --> preflight["Resolve complete inputs and read-only boundary"]
  preflight --> identity["Reviewer candidate independence check"]
  identity --> transport["Existing API or verified CLI transport"]
  transport --> validate["Validate returned artifact; one repair"]
  validate --> publish["Host publishes artifact and invocation metrics"]
  validate -->|"invalid"| preserve["Preserve prior artifacts and phase"]
```
