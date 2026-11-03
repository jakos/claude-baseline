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
