# Starting a new repo with dev-baseline

From nothing to a repo where a fresh Claude session knows what the project is, what it
must never do, and how to check whether a change is correct. About fifteen minutes.

## 0. Once per machine

The plugin does nothing on its own — `ruff` is what the hook actually runs:

```bash
uv tool install ruff
```

Then install the plugin at user scope, so it loads in every project:

```
/plugin marketplace add jakos/claude-baseline
/plugin install dev-baseline@claude-baseline
```

Two prompts, not one. Check it landed:

```bash
claude plugin details dev-baseline
```

You want `Skills (5)`, `Agents (1)`, `Hooks (1)`. If `claude plugin list` says **failed to
load**, read the error — that is the only place a broken plugin announces itself.

> On Jakub's machine the marketplace is a *directory* source pointing at the checkout, so
> the working tree is what loads, everywhere. See the README.

## 1. In the new repo: find the gate before writing anything

Open Claude Code in the repo and ask:

> What command proves a change to this repo is correct? If there isn't one, what would it
> take to have one?

This should pull in `project-bootstrap`. The gate matters more than any documentation you
write: it is a machine, not a model, deciding whether work passed, and it is what makes
`/loop` and `/goal` safe rather than a way to accumulate plausible wrong code.

Aim for one command behind one name:

```just
check:
    uv run ruff format --check .
    uv run ruff check .
    uv run mypy src/
    uv run pytest -q
```

A repo with no gate: adding one is the highest-value thing you can do, before anything
below.

## 2. Write CLAUDE.md

Ask:

> Write a CLAUDE.md for this repo.

Keep it under ~100 lines. It loads into every session **and every subagent**, so a
300-line file costs ~3,000 tokens every time, forever, multiplied by every subagent you
spawn.

The section that earns its place is **constraints not obvious from the code** — what a
newcomer gets wrong, what a previous attempt broke on. Everything else the model can read
from the source in ten seconds. Leave out anything a linter enforces.

Write constraints as prohibitions with a reason. *"Never change a record's `external_id` —
it is the key every downstream consumer joins on"* gets followed. *"Be careful with
identifiers"* does not.

## 3. Commit `.claude/settings.json`

```json
{
  "permissions": {
    "allow": ["Bash(just:*)", "Bash(uv run:*)", "Bash(uv sync)", "Bash(git diff:*)", "Bash(git status)"],
    "ask": ["Bash(git push:*)"],
    "deny": ["Read(./.env)"]
  }
}
```

Pre-approving the gate commands is the point: an agent that must ask permission to run the
tests runs them less often. Note `uv sync` listed exactly rather than `uv:*` — a blanket
rule would also pre-approve `uv add` and `uv tool install`, which change state.

Personal overrides go in `.claude/settings.local.json`, gitignored.

**Do not put `extraKnownMarketplaces` here.** It looks project-scoped and is not: it
rewrites one user-wide file, keyed by the name inside `marketplace.json` rather than the
key you give it.

## 4. Then just work

Nothing below needs invoking. The skills fire on their own:

| When you're doing this | What loads |
| --- | --- |
| Adding an endpoint, deciding where logic belongs | `python-service` |
| Writing tests, or a suite is slow or flaky | `pytest-suite` |
| Something broken, intermittent, or a fix didn't hold | `debug-systematically` |
| Committing, slicing work, opening a PR | `commit-and-pr` |

Every Python file written is formatted by `ruff` automatically, and whatever ruff can't fix
comes back as a note. You do nothing.

Before committing, the one thing worth invoking by hand:

```
@code-reviewer
```

It reads the diff and every touched file in full, on Sonnet, in its own context — so the
file contents never enter your session. Worth it mid-task when your context is valuable;
overkill for a two-line diff.

## 5. Check it worked

Start a fresh session and ask:

> What is this project, what must you never do here, and how do you verify a change?

If the answer is vague, the `CLAUDE.md` is vague. Fix it now, while it is one file and not
a habit.

## When something seems wrong

| Symptom | Cause |
| --- | --- |
| Hook does nothing | `ruff` not on `PATH`. It exits silently by design |
| Skill never fires | Its description doesn't match how you phrased the request |
| A pushed change didn't arrive | `version` in `plugin.json` wasn't bumped — updates are gated on it |
| Plugin missing entirely | `claude plugin list` — a load failure shows only there |

Everything in this plugin fails quietly. Nothing errors, output still appears, and it
looks exactly like working. When in doubt, check `claude plugin details dev-baseline`.

## The rule that makes this compound

When you correct the same mistake in two different repos, the correction belongs in a
skill in this repository, not in two `CLAUDE.md` files. A project fact goes in the repo; a
reusable technique goes in a skill. That distinction is the whole reason the baseline
exists.
