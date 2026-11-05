# task-flow

A multi-agent workflow for tasks and feature requests: the planner writes a plan you
approve, an implementer works in a git worktree, a checker runs the gates, and an
independent reviewer judges the result — at most three rounds, every artifact in
`.work/<task-id>/`. Its purpose is achieving an AI-native SDLC — agents work under
human gates, never unattended; the generated stage status is in
[AI-native SDLC status](../../docs/task-flow/ai-native-sdlc.md).

## Quick reference

```
/task-flow:task-run "<request>"
/task-flow:task-status
/task-flow:task-finish
/task-flow:task-autopilot
/task-flow:task-doctor
```

## Documentation

- [Overview](../../docs/task-flow/overview.md)
- [AI-native SDLC status](../../docs/task-flow/ai-native-sdlc.md)
- [Setting up task-flow](../../docs/task-flow/setup.md)
- [Commands](../../docs/task-flow/commands.md)
- [Autopilot](../../docs/task-flow/autopilot.md)
- [Configuration](../../docs/task-flow/configuration.md)
- [External implementer](../../docs/task-flow/external-implementer.md)
- [Deterministic gates](../../docs/task-flow/gates.md)
- [What enforces what](../../docs/task-flow/enforcement.md)
- [Profiles](../../docs/task-flow/profiles.md)
- [Releasing](../../docs/task-flow/releasing.md)
- [Benchmark](../../docs/task-flow/benchmark.md)

With `driver = "cli"` the implementer role runs one agent-CLI process instead of an HTTP
API — Codex on a ChatGPT subscription (saved login) or on an invocation-scoped API key;
see [External implementer](../../docs/task-flow/external-implementer.md):

```toml
[profiles.codex]                 # subscription: saved login (codex login), no key
# .claude/task-flow.models.toml
driver = "cli"
model = "gpt-5.2-codex"           # metadata only; -m below picks the model
billing = "subscription"
command = ["codex", "exec", "--sandbox", "workspace-write", "--json", "--ephemeral", "-m", "gpt-5.2-codex", "-"]
usage_format = "codex-jsonl"

[profiles.codex-api]             # or an invocation-scoped key instead of the login
driver = "cli"
model = "gpt-5.2-codex"
billing = "api"
api_key_env = "CODEX_API_KEY"
pass_env = ["CODEX_API_KEY"]
command = ["codex", "exec", "--sandbox", "workspace-write", "--json", "--ephemeral", "-m", "gpt-5.2-codex", "-o", "{last_message_file}", "-"]
usage_format = "codex-jsonl"

[roles]
implementer = "codex"
```

The planner stage can run on an external profile the same way — `[roles] planner = "codex"` or a one-run `--planner codex` — see [External planner](../../docs/task-flow/external-implementer.md).
