---
name: project-bootstrap
description: Set up a new repository for effective agent-assisted work — write its CLAUDE.md, wire the baseline plugins, choose the verification gates. Use when starting a new project, onboarding an existing repo to Claude Code, or when an existing CLAUDE.md has stopped being useful.
---

# Bootstrapping a repo for agent work

The goal is that a fresh session knows, without being told: what this project is, what
must never be done to it, and how to check whether a change is correct.

## 1. Find the gates first

Before writing any instructions, answer: **what command proves a change is good?**

```bash
just check
```

Behind one recipe, so the name never changes even when the commands do:

```just
check:
    uv run ruff format --check .
    uv run ruff check .
    uv run mypy src/
    uv run pytest -q
```

Two properties matter more than the specific tools. **`uv run` means the gate uses the
locked versions**, so it produces the same verdict on your laptop, on a colleague's, and
in CI — a gate that passes locally and fails in CI is not a gate. **One memorable name**
means the agent, the README, the pre-push hook and the CI job all invoke the same thing,
and adding a step later does not invalidate every place it is written down.

This matters more than any prose. A gate is a machine deciding whether work passed, and
it is what makes unattended `/goal` and `/loop` runs safe rather than a way to accumulate
plausible-looking wrong code. If a project has no gate, adding one is the highest-value
thing to do before anything else.

Weak gates to strengthen: no tests, tests that do not run offline, a linter nobody runs,
a "just look at it" review step.

## 2. Write CLAUDE.md

Keep it under roughly 100 lines. It loads into every session, including every subagent,
so its cost is paid continuously. Long files get skimmed — by people and by models.

The sections that earn their place:

```markdown
# <project>

One paragraph: what this is and who uses it.

## Constraints that are not obvious from the code
The facts a newcomer gets wrong. A limit you cannot design around, a third-party
API's quirks, why the weird thing is weird. This is the most valuable section — it
is what you cannot infer by reading the source.

## Non-negotiables
Numbered, short, absolute. Things that cause real damage: destroying user data,
renaming stable identifiers, writing to production, disabling a safety check.

## Commands
The gates, copy-pasteable.

## Conventions
Only where this project differs from the obvious default. Do not restate PEP 8.

## Layout
A table of directories, one line each, only if the structure is non-obvious.
```

What to leave out: anything a linter enforces, anything the agent can read from the code
in ten seconds, aspirational statements nobody follows, and a description of the tech
stack that `pyproject.toml` already gives.

Write constraints as prohibitions with a reason. "Never change a record's `external_id` —
it is the key every downstream consumer joins on, and rewriting one silently corrupts their
history" is followed. "Be careful with identifiers" is not.

## 3. Wire the tooling

`.claude/settings.json`, committed, so anyone cloning the repo gets the same setup:

```json
{
  "extraKnownMarketplaces": {
    "claude-baseline": {
      "source": { "source": "github", "repo": "jakos/claude-baseline" }
    }
  },
  "enabledPlugins": { "dev-baseline@claude-baseline": true },
  "permissions": {
    "allow": [
      "Bash(just:*)", "Bash(uv run:*)", "Bash(uv sync)",
      "Bash(pytest:*)", "Bash(ruff:*)",
      "Bash(git diff:*)", "Bash(git status)"
    ],
    "ask": ["Bash(git push:*)"],
    "deny": ["Read(./.env)", "Read(./secrets.yaml)"]
  }
}
```

Pre-approving the gate commands matters: an agent that must ask permission to run the
tests will run them less often. `Bash(uv run:*)` is the one that pays for itself, since
every gate command goes through it.

Note that `uv sync` is listed exactly, not as `uv:*`. A blanket `uv:*` would also
pre-approve `uv tool install`, `uv add` and `uv pip install`, which mutate the
environment or the lockfile — those deserve a prompt.

Personal overrides belong in `.claude/settings.local.json`, which is gitignored.

## 4. Decide what is domain knowledge and what is project fact

The distinction that keeps this maintainable over years:

- **Project fact** → `CLAUDE.md` in the repo. "Our staging cluster is Kubernetes."
- **Reusable domain knowledge** → a skill in `claude-baseline` or a domain kit.
  "How to structure a pytest suite with Testcontainers."

When you correct the same mistake in two different repos, the correction belongs in a
skill, not in two `CLAUDE.md` files. That is the compounding step — the reason for having
a baseline repo at all.

## 5. Check it works

Start a session and ask: *what is this project, what must you never do here, and how do
you verify a change?* If the answer is vague, the `CLAUDE.md` is vague. Fix it now,
while it is one file and not a habit.
