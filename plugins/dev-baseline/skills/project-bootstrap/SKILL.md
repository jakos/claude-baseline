---
name: project-bootstrap
description: Set up a new repository for effective agent-assisted work — write its CLAUDE.md, wire the baseline plugins, choose the verification gates. Use when starting a new project from an empty directory, onboarding an existing repo to Claude Code, or when an existing CLAUDE.md has stopped being useful.
---

# Bootstrapping a repo for agent work

The goal is that a fresh session knows, without being told: what this project is, what
must never be done to it, and how to check whether a change is correct.

## 0. Which situation is this?

- **There is code** — onboarding an existing repo, or a CLAUDE.md that stopped helping.
  Start at step 1: the answers are in the repository, so read it.
- **There is nothing yet** — an empty directory, or only a README. Start at *Fresh
  project* below. There is no code to read the constraints out of, so they have to come
  from the person, and the gate has to be built rather than found. Then continue at step 3.

## Fresh project

### Ask before building anything

Scaffolding first and asking later produces a generic repo and a CLAUDE.md made of
guesses that every later session will treat as fact. Ask these in **one message**, then
stop and wait. Only questions whose answers change what gets built:

| # | Question | What it decides |
| --- | --- | --- |
| 1 | What is it, and who uses it? | CLAUDE.md's first paragraph |
| 2 | What does it talk to — APIs, databases, files, devices, other services? | Dependencies, test setup, the constraints section |
| 3 | What must never happen? Data lost, a production write, a secret leaked, money moved? | Non-negotiables |
| 4 | Is it a service, a CLI or library, or a set of scripts? | Package or `package = false`; whether the `python-service` kit applies |
| 5 | Is anything already decided — language version, libraries, where it runs? | What not to choose for them |

**Every question accepts "I don't know — research it."** Say so in the message, and when
an `AskUserQuestion` tool is available, make it an explicit option on each question. People
starting something new often do not know the rate limit of the API they are about to use,
or which of three libraries fits; making them guess is worse than looking it up.

What "research it" means depends on whose question it is:

- **Facts about the world** (2, 5, and the technical half of 4): look them up — the
  external system's docs for limits, auth, quotas and known quirks; candidate libraries'
  maintenance and fit. Come back with a **recommendation, its reason and its sources**,
  and get a yes before it is built or written down. Defaults that `python-tooling` already
  settles — uv, ruff, prek, the newest stable Python — need no research, only a mention.
- **Things only the person knows** (1, 3): no search answers these. Propose a reading from
  what they have said so far, and if they still do not know, write it down marked
  **Assumed:** so a later session knows it was never confirmed.

A researched answer goes into CLAUDE.md as a decision with its reason — "httpx over
requests: async needed for the NAS API, 2026-10" — because the reason is what stops a
later session re-opening it.

### Build the gate before the code

The gate comes first here too; it just has to be built instead of found:

1. `git init`, `.gitignore`, `.gitattributes`.
2. The Python setup from `python-tooling` — project or scripts, pyproject, lockfile, ruff,
   pytest and prek as dev dependencies, the `just check` recipe. Follow that skill rather
   than re-deriving it here.
3. **One trivial test** that imports the package, or runs the script with `--help`. It is
   there so the gate is proven to run end to end, not to test anything; the first real
   test replaces it.
4. `just check` passes, and `prek run --all-files` passes, **before** the first line of
   real code. A gate that has never once passed is not a gate yet.

### Then CLAUDE.md, from the answers

Use the sections in step 2. Fill them from the interview and the research, not from
imagination: a constraints section with one real line beats five plausible ones. It grows
the first time the same correction has to be made twice.

Continue at step 3 for settings, then commit once the gate passes — the bootstrap is the
first commit, so every later commit starts from a green gate.

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
  "attribution": { "commit": "", "pr": "", "sessionUrl": false },
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

Domain kits go in the same `enabledPlugins` map — a Python service also enables
`python-service@claude-python-service`. Only enable a kit where its domain applies; every
enabled skill's description costs context in every session.

Leave `extraKnownMarketplaces` out of project settings. It looks project-scoped and is
not: it rewrites the user-wide marketplace list, under the name the marketplace declares.
Each machine adds the marketplace once, at user scope.

`attribution` takes empty strings, not `false`: Claude Code 2.1.291 rejects `false` and
then ignores the whole settings file.

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
