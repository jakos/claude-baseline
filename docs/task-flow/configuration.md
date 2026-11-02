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

The planner copies what applies into `plan.md → Project rules` and `Verification
commands`, so the other agents never read the config themselves.

## Configuration: `models.toml`

Two optional TOML files: `~/.claude/task-flow/models.toml` (user) and
`<repo-root>/.claude/task-flow.models.toml` (project). Profiles merge by name, then key by
key; `[roles]` merges key by key; the project wins. Unknown tables and keys are errors that
name the file and the key. Keys of `[profiles.<name>]`:

| Key | Meaning | Default |
| --- | --- | --- |
| `driver` | `"openai-loop"` or `"anthropic-loop"` (required) | |
| `base_url` | `http(s)://` URL; `/chat/completions` is appended on `openai-loop`, `/messages` on `anthropic-loop` (required) | |
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
