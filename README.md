# claude-baseline

Personal baseline of Claude Code skills, agents and guardrails. Not project-specific —
this is the layer that applies to everything, so every project inherits the same
conventions and the same review discipline.

Domain-specific kits live in their own repositories and are installed alongside this one.

## Install once, globally

Because this applies to all your work, install it at **user scope** so it loads in every
project without per-repo configuration:

```
/plugin marketplace add jakos/claude-baseline --scope user
```

```
/plugin install dev-baseline@claude-baseline
```

Two separate prompts — they do not work as one. After this, the skills are available in
every session on this machine, including repos that have no `.claude/` directory at all.

Project-scoped install (committed to a repo so teammates get it) instead uses
`--scope project`, which writes to that repo's `.claude/settings.json`.

## What's in it

### Skills

| Skill | Loads when |
| --- | --- |
| `python-service` | Structuring a Python backend — DDD layering, FastAPI at the edge, distributed-system boundaries |
| `pytest-suite` | Writing or fixing tests — unit tests with fakes, integration tests with Testcontainers, flakiness |
| `project-bootstrap` | Starting a repo or onboarding one: choosing gates, writing a useful CLAUDE.md, wiring plugins |
| `debug-systematically` | Something is broken, intermittent, or a first fix did not hold |
| `commit-and-pr` | Committing, slicing work, writing a PR body worth reading |

### Agent

`@code-reviewer` — read-only review of a Python diff. Correctness under unconsidered
inputs, error handling, resource leaks, blocking calls in async code, test coverage of
the change. Reports findings; never edits.

It is a separate agent on purpose: the model that wrote the code is the worst reviewer of
it, because it will confirm the assumptions it just made.

### Hook

`PostToolUse` on every Python file written: `ruff format`, `ruff check --fix`, then
surface whatever could not be fixed automatically. Prefers
[habit-hooks](https://github.com/habit-hooks/habit-hooks) when it is installed and the
project has a `.habit-hooks/config.toml`, because it returns coaching text rather than
rule codes. Silent no-op when neither tool is present.

Findings go back as JSON in `hookSpecificOutput.additionalContext`, which is the only
channel a `PostToolUse` hook has to the model — plain stdout on exit 0 goes to the debug
log and is never read, and exit 2 is not honoured for this event because the tool has
already run. A hook that prints its findings has no effect at all; this is easy to get
wrong and invisible when you do.

Requires `ruff` on `PATH` to do anything (`uv tool install ruff`), and `jq` is used when
present. Without them the hook exits silently rather than complaining on every edit.

### Template

`templates/CLAUDE.md.template` — the skeleton `project-bootstrap` fills in.

### Evals

`plugins/dev-baseline/evals/` — one case per skill, each asserting that the skill fired
*and* that the answer was the one it exists to produce, plus two negative cases that must
fire nothing. Descriptions are the whole interface of a skill, and they cost context in
every session whether or not they trigger; this is how you find out whether yours do.

```bash
cd plugins/dev-baseline
claude plugin eval .                    # with-plugin and no-plugin arms, with a delta
claude plugin eval . --ablation none    # with-arm only, half the cost, for iterating
```

`Δ` is the number to read — the with-plugin score minus the no-plugin score. A skill whose
cases score well in both arms is not earning its context. See `evals/README.md`.

## These are opinions, not truth

Every skill here encodes a way of working. Where a project disagrees, its own `CLAUDE.md`
wins. Where **you** disagree, edit the skill — it lives in your repo precisely so it can
be wrong and then corrected.

The rule that makes this compound: when you correct the same mistake in two different
projects, the correction belongs in a skill here, not in two `CLAUDE.md` files. Push the
change, then `/plugin marketplace update claude-baseline`.

## Companions worth installing

These sit at different layers and compose rather than compete:

- **[ponytail](https://github.com/DietrichGebert/ponytail)** — stops the agent
  over-building; makes it reach for the stdlib or an existing dependency before inventing
  an abstraction. Its published benchmarks are self-reported; the mechanism is sound.
  ```
  /plugin marketplace add DietrichGebert/ponytail
  /plugin install ponytail@ponytail
  ```
- **[habit-hooks](https://github.com/habit-hooks/habit-hooks)** — linter findings turned
  into actionable coaching. The formatter hook above uses it automatically when present.
  ```bash
  uv tool install habit-hooks
  ```
- **[addyosmani/agent-skills](https://github.com/addyosmani/agent-skills)** — 25 process
  skills across the SDLC. Useful for `/spec` and `/plan`, which this baseline does not
  cover. Note that all 25 descriptions occupy context in every session, and its `/review`
  overlaps with `@code-reviewer` here.
- **[loop-engineering](https://github.com/cocodedk/loop-engineering)** — methodology for
  autonomous verify-loops. Read it; skip the shell scripts, since Claude Code's `/loop`
  and `/goal` already implement the harness. Its real lesson is the precondition: a loop
  is only safe where a machine, not a model, decides whether the work passed.

## Layout

```
.claude-plugin/marketplace.json      catalog
plugins/dev-baseline/
    .claude-plugin/plugin.json       manifest
    skills/<name>/SKILL.md           five skills
    agents/code-reviewer.md          read-only reviewer
    hooks/hooks.json                 python formatter
    scripts/format_python.sh
    templates/CLAUDE.md.template
    evals/<case>/                    behavioural tests: does the skill fire
```

Validate before pushing:

```bash
claude plugin validate .            # manifests: schema and syntax
cd plugins/dev-baseline && claude plugin eval .   # behaviour: do the skills fire
```

## License

MIT.
