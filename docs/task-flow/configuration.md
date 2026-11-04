# Configuration

Paths such as `scripts/…`, `skills/…`, `agents/…`, `templates/…` and `bench/…` are inside `plugins/task-flow/`.

## Project configuration

Optional `.claude/task-flow.md` in the target repository — copy
[`templates/task-flow.md`](../../plugins/task-flow/templates/task-flow.md). It declares extra profiles and
path→profile mappings, exact verification commands per profile (replacing the
defaults), architecture rules, base branch, branch naming, whether to use worktrees, and
what `task-finish` may do without asking. It is Markdown because only agents read it.

Optional `.claude/task-flow.release.toml` configures `/task-flow:task-release` — the
release targets, their version files, tag patterns, changelogs, scopes and checks (see
Releasing, and [`templates/release.toml`](../../plugins/task-flow/templates/release.toml) for a commented
example). It is TOML because a script parses it strictly, unlike `task-flow.md`.

Optional `.claude/task-flow.records.toml` opts a repository into the finish-time task
record — the deterministic `docs/tasks/<id>.md` and `Task-*` commit trailers that
`scripts/task_record.py` renders at `/task-flow:task-finish` (see
[Task records](#task-records-recordstoml), and
[`templates/records.toml`](../../plugins/task-flow/templates/records.toml) for a commented example). It is
TOML because a script parses it strictly too; the template ships `enabled = false`, so
copying it changes nothing until you opt in.

The planner copies what applies into `plan.md → Project rules` and `Verification
commands`, so the other agents never read the config themselves.

## Task records (`records.toml`)

`.claude/task-flow.records.toml` in the target repository — read from the selected
worktree, or the repository root when the task has none — opts into the finish-time
task record:

```toml
[records]
enabled = true
```

`scripts/task_record.py` parses the file strictly with tomllib: `[records]` is the
only table and `enabled` its only key, a boolean; an unknown key or a wrong type is an
error (exit 2). A missing file or `enabled = false` keeps the previous finish
behaviour — no record, no trailers, no new required artifacts, no extra approval gate.
Roll back by setting `enabled = false` or deleting the file.

When enabled, `/task-flow:task-finish` runs, after `summary.md` and before the commit
(both from the repository root, in the background):

- `uv run --script plugins/task-flow/scripts/task_record.py render --repo <repo-root> --task-dir <abs .work/<id>> --worktree <abs worktree | none>`
  writes one bounded, schema-versioned (`task-record/1`), UTF-8/LF
  `docs/tasks/<id>.md` in the worktree: task id, goal and omitted scope (the
  `## Goal` / `## Out of scope` sections of `summary.md`), every plan AC text with its
  final result, per-round verdict and actual implementer (`profile`/`model` of
  `impl-<n>.json`; `unknown` when absent — never guessed), approval source, final
  verdict and risk availability (`unavailable` until a merged risk schema exists).
  Regeneration is byte-identical; a conflicting existing record, an unsafe destination
  or a malformed artifact is refused (exit 2) with no partial output, and finish stops
  before the commit.
- the same command with `trailers` prints the sanitized trailer block that is appended
  once to the commit message: `Task-Id`, `Task-Rounds`, repeated
  `Task-Implementer: <profile>/<model>` for the distinct executed pairs, and
  `Task-Plan-Approved: user|auto`. `Task-Risk` is not emitted while no risk schema is
  merged.

All exported prose is scrubbed of file paths (Windows, POSIX, UNC, home), credential
URLs, common token/private-key/secret-assignment patterns and attribution/session
markers, with visible truncation at fixed length limits and no timestamps or absolute
paths; the helper fails closed on unsafe residual input and its diagnostics never echo
the values. It never copies prompts, raw logs, settings, endpoints or environment
values, and it never commits, pushes, opens PRs, contacts models or prunes — the
orchestrator stages the generated record with the task's other changes and commits it
together.

## Configuration: `models.toml`

Two optional TOML files: `~/.claude/task-flow/models.toml` (user) and
`<repo-root>/.claude/task-flow.models.toml` (project). Profiles merge by name, then key by
key; `[roles]` merges key by key; the project wins. Unknown tables and keys are errors that
name the file and the key. Keys of `[profiles.<name>]`:

| Key | Meaning | Default |
| --- | --- | --- |
| `driver` | `"openai-loop"`, `"anthropic-loop"` or `"cli"` (required) | |
| `base_url` | `http(s)://` URL; `/chat/completions` is appended on `openai-loop`, `/messages` on `anthropic-loop` (required on the API drivers, refused on `cli`) | |
| `model` | model id (required) | |
| `api_key_env` | NAME of the environment variable holding the key, never the key; required unless `billing = "local"` | none |
| `billing` | `subscription`, `api` or `local` (required) | |
| `max_turns` | completion requests allowed on one profile | 60 |
| `max_cost_usd` | cap over the whole run, all profiles together | none |
| `max_context` | fail when a response reports more prompt tokens | none |
| `price_input_per_mtok`, `price_cached_input_per_mtok`, `price_output_per_mtok` | USD per 1M tokens, for the cost figure | 0 |
| `price_cache_write_per_mtok` | USD per 1M prompt-cache-write tokens, `anthropic-loop` (cache writes cost 1.25x the input price for the 5-minute TTL) | `price_input_per_mtok` |
| `fallback` | other profiles to try, in order, when this one stays unavailable | `[]` |
| `enabled` | `false` disables the profile | `true` |
| `retries`, `retry_base_seconds` | retries per request on 408, 429, 5xx, timeouts and connection errors; exponential backoff, `Retry-After` honoured up to 60 s | 3, 2 |
| `request_timeout_seconds`, `command_timeout_seconds` | per request, per `run_command` call | 300, 600 |
| `temperature` | sent as `temperature`; NOT allowed on `anthropic-loop` (the pinned API version rejects it on every model) | provider default |
| `max_output_tokens` | sent as `max_tokens` on `openai-loop` (under the `max_tokens_param` key) and as `max_tokens` on `anthropic-loop` | provider default; 16000 on `anthropic-loop` |
| `max_tokens_param` | the body key `max_output_tokens` is sent under on `openai-loop`: `"max_tokens"` or `"max_completion_tokens"`; only `"max_tokens"` on `anthropic-loop` | `"max_tokens"` |
| `extra_body` | table merged into the request body, e.g. reasoning effort; reserved keys per driver — `openai-loop`: `model`, `messages`, `tools`, `tool_choice`; `anthropic-loop`: those plus `system`, `max_tokens`, `stream`, `temperature`, `top_p`, `top_k` | none |
| `post_edit` | list of commands run after each successful `write_file`/`edit_file` call, with every literal `{path}` replaced by the written file; the exit code and output (capped to 40 lines) are appended to that tool call's result | `[]` |
| `prompt_extras` | list of files read before the round whose contents are appended to the system prompt before the runner adapter; `~` is expanded, a relative path resolves against the repository root; a missing or unreadable file refuses the round (exit 2) | `[]` |

Keys that only `driver = "cli"` accepts (refused on the API drivers), all validated after
the user/project merge with file/key provenance in the error; a `cli` profile also refuses
the API-loop keys `base_url`, `max_turns`, `max_cost_usd`, `max_context`, `retries`,
`retry_base_seconds`, `request_timeout_seconds`, `command_timeout_seconds`, `temperature`,
`max_output_tokens`, `max_tokens_param`, `extra_body` and `post_edit` (the CLI owns its
internal loop; `model` stays required and is metadata only):

| Key | Meaning | Default |
| --- | --- | --- |
| `command` | the CLI program and its arguments, a non-empty list of non-empty strings (required on `cli`); only `{worktree}`, `{prompt_file}` and `{last_message_file}` are expanded, each argument on its own, no shell | |
| `timeout_seconds` | the whole CLI run's deadline; on timeout the process tree is killed | 600 |
| `prompt_via` | how the prompt reaches the CLI: `"stdin"`, `"file"` (requires `{prompt_file}` in `command`), or `"arg"` (appended as one final argument; refused for a Windows `.cmd`/`.bat` shim) | `"stdin"` |
| `usage_format` | `"codex-jsonl"` parses stdout as one JSON event per line and sums `turn.completed` usage; `"none"` uses only the final plain stdout message, leaving usage and cost unknown (null) | `"none"` |
| `pass_env` | extra environment-variable NAMES whose values are passed to the CLI on top of the runner allowlist | `[]` |
| `limit_patterns` | regular expressions; a match against the bounded stderr or final output makes this attempt unavailable and tries the next `fallback` | `[]` |

`billing = "api"` on `driver = "cli"` requires `api_key_env` (passed through `pass_env`);
`billing = "subscription"` or `"local"` refuses one. See
[Agent CLI driver](external-implementer.md#agent-cli-driver-cli-codex) for Codex examples.

`[roles]` keys are `planner`, `implementer`, `checker`, `reviewer`, `backlog`. Values:
`claude`, `claude:opus`, `claude:sonnet`, `claude:haiku` for any role, a profile name for
`implementer` only, `script` for `checker` only (see
[Deterministic gates](gates.md#deterministic-gates-scriptsgatespy)). `implement.py` reads only
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

## Choosing Claude models per role

`[roles]` also picks the Claude model of every Claude-run role. Values: `claude`,
`claude:opus`, `claude:sonnet` or `claude:haiku` for any role; a profile name for
`implementer` only; `script` for `checker` only. A `claude:<model>` value is passed to the
subagent call as its `model` argument and takes precedence over the agent file's frontmatter;
bare `claude` passes nothing, so the frontmatter default applies; with no `models.toml` at
all, nothing changes. A misspelled role or value is refused with exit 2, never silently
ignored. `checker = "script"` runs `scripts/gates.py` instead of the checker subagent —
see [Deterministic gates](gates.md#deterministic-gates-scriptsgatespy). Caveat: with
`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` on or
`CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` set, Claude Code ignores the per-call model.

| Role | Frontmatter default |
| --- | --- |
| `planner` | opus |
| `implementer` | sonnet |
| `checker` | haiku |
| `reviewer` | opus |
| `backlog` | opus |

[templates/models.toml](../../plugins/task-flow/templates/models.toml) is a commented example with every key, each
role at its frontmatter default, so copying it changes no model. Example — the reviewer on
Sonnet to cut cost, the same reason dev-baseline's `code-reviewer` runs on Sonnet:

```toml
[roles]
reviewer = "claude:sonnet"
```
