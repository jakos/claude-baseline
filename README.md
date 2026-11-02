# claude-baseline

Personal baseline of Claude Code skills, agents and guardrails. Not project-specific —
this is the layer that applies to everything, so every project inherits the same
conventions and the same review discipline.

Domain-specific kits live in their own repositories and are installed alongside this one:

- `claude-python-service` — structuring and
  testing Python backend services (DDD layering, FastAPI, pytest with Testcontainers).
- `claude-home-assistant` — a Home Assistant
  config as a git repository.

Python is tooling everywhere, so how to *set up* Python lives here; how to build a *service*
in it lives in the kit.

## Install once, globally
Moved to [docs/dev-baseline.md](docs/dev-baseline.md#install-once-globally).

## What's in it
Moved to [docs/dev-baseline.md](docs/dev-baseline.md#whats-in-it).

### Skills
Moved to [docs/dev-baseline.md](docs/dev-baseline.md#skills).

### Agent
Moved to [docs/dev-baseline.md](docs/dev-baseline.md#agent).

### Hooks
Moved to [docs/dev-baseline.md](docs/dev-baseline.md#hooks).

### Template
Moved to [docs/dev-baseline.md](docs/dev-baseline.md#template).

### Evals
Moved to [docs/dev-baseline.md](docs/dev-baseline.md#evals).

## Second plugin: task-flow

`plugins/task-flow/` — a multi-agent workflow for tasks and feature requests: planner →
your approval → implementer → checker → independent reviewer, at most three rounds, every
artifact in `.work/<task-id>/`. Install it only where you want it:

```
/plugin install task-flow@claude-baseline
```

Then run `/task-flow:task-doctor`: it checks this machine, this session and this
repository and prints the fix for anything missing.

It depends on `dev-baseline` and is described in its own
[README](plugins/task-flow/README.md).

Optional and experimental: the implementer role can run on a per-token, non-Claude model
through `plugins/task-flow/scripts/implement.py` — Mistral or any OpenAI-compatible host,
or Anthropic through the native Messages API via `scripts/drivers.py` (so far only the
Mistral API has been exercised) — while Claude still plans, checks and reviews. A
`models.toml` turns it on; see
[External implementer](plugins/task-flow/README.md#external-implementer). This
repository's own `.claude/task-flow.models.toml` is such a project file and holds no
secrets, only the name of the key's environment variable. Releases (version, changelog
entry, commit and tag) are made with `/task-flow:task-release`; see
[Releasing](plugins/task-flow/README.md#releasing).

## These are opinions, not truth
Moved to [docs/dev-baseline.md](docs/dev-baseline.md#these-are-opinions-not-truth).

## Companions worth installing
Moved to [docs/dev-baseline.md](docs/dev-baseline.md#companions-worth-installing).

## Working on this repo
Moved to [docs/development.md](docs/development.md#working-on-this-repo).

## Reviewing this repo itself
Moved to [docs/development.md](docs/development.md#reviewing-this-repo-itself).

## Layout
Moved to [docs/development.md](docs/development.md#layout).

## License

MIT.
