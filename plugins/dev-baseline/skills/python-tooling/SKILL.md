---
name: python-tooling
description: Set up or fix the Python toolchain in any repo — uv for the interpreter, environment and lockfile, pyproject.toml, ruff for format and lint, prek for git hooks, single-file scripts with inline dependencies. Use when adding Python to a repo (including scripts and helper tooling in a non-Python repo), starting a pyproject, choosing between a script and a project, or when installs, linting or hooks behave differently on two machines.
---

# Python tooling

> **Adapt this file.** It encodes one toolchain, chosen so that every repo — a service, a
> Terraform repo with three helper scripts, a Home Assistant config — is set up the same
> way. Where a project's `CLAUDE.md` disagrees, it wins.

The goal of everything below is one property: **every tool version is decided by a file in
the repo, and every place that runs a tool — your shell, the editor, the git hook, CI, the
formatter hook in this plugin — runs that same version.** Nearly every "passes for me,
fails for you" in Python tooling is two of those places disagreeing.

## Machine, once

```bash
# macOS, Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

uv tool install ruff      # only for the formatter hook in repos without a lockfile
```

Interpreters come from `uv python install`, not from python.org, the Microsoft Store or
the OS package manager. A system Python is for the system. No `pip install` into it,
ever, and no global `pip install --user` either.

## Which Python

**The newest stable CPython minor version**, for new projects and new scripts. Find it
rather than remembering it — the examples below say 3.14 because that was current when
they were written, and a model's training data is always a release or two behind:

```bash
uv python list --only-downloads   # highest version with no a/b/rc suffix
```

Stable means a final release: never an alpha, beta or release candidate, even when it is
the highest number on the list. Use that minor version in `requires-python`, in
`.python-version` and in PEP 723 headers, and pin only the minor (`3.14`, not `3.14.7`),
so patch releases arrive through `uv python upgrade` without touching the repo.

For an existing project, upgrading is a change of its own: bump `.python-version`, run
`uv sync` and the gate, and raise `requires-python` only once that passes. In the first
weeks after a release, a dependency with compiled extensions may have no wheels for it
yet; `uv sync` failing to build one is the signal to stay on the previous minor for now,
not to install a compiler.

## Script or project?

Decide this first; it changes everything after it.

**A single script with its own dependencies** → PEP 723 inline metadata, no pyproject:

```python
# /// script
# requires-python = ">=3.14"
# dependencies = ["httpx>=0.28"]
# ///
```

```bash
uv add --script tools/fetch.py httpx   # writes the block above
uv lock --script tools/fetch.py        # tools/fetch.py.lock, commit it
uv run tools/fetch.py
```

**Two or more scripts sharing code or dependencies, or anything with tests** → a project.
The threshold is low on purpose: the second script that copies a helper from the first is
the moment it became a project.

## The project

Python as tooling in a repo whose product is not a Python package:

```bash
uv init --bare --python 3.14     # pyproject.toml only; no sample code, no build backend
uv python pin 3.14               # .python-version
uv add httpx                     # runtime deps of the tooling
uv add --dev ruff pytest prek    # pinned in uv.lock, not "whatever is installed"
```

```toml
[project]
name = "infra-tools"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["httpx>=0.28"]

[dependency-groups]
dev = ["prek>=0.5", "pytest>=9", "ruff>=0.16"]

[tool.uv]
package = false     # tooling is run, not installed; nothing to build

[tool.ruff]
line-length = 100   # target-version is read from requires-python; do not repeat it

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "UP", "SIM", "RUF"]

[tool.pytest.ini_options]
testpaths = ["tools/tests"]
```

`package = false` matters: without a build backend uv will not try to build and install
the repo into its own environment, which for a repo of scripts only produces confusing
errors about missing packages. A real distributable library or a service is the opposite
case — `uv init --package`, code under `src/`.

Rules that hold either way:

- **Commit `uv.lock` and `.python-version`.** `requires-python` says what the code
  supports; `.python-version` says what this checkout uses. Two questions, two files.
- **`uv run`, never an activated venv.** `uv run` re-syncs against the lockfile every time,
  so a command cannot silently run in a stale or foreign environment. `.venv/` is uv's
  cache, gitignored, deletable at any time.
- **Ruff and pytest are dev dependencies, not global tools.** A global ruff 0.16 and a
  colleague's 0.13 format the same file differently. Pinned in the lockfile, the editor,
  the hook and CI agree. This plugin's formatter hook already prefers the lockfile's ruff
  when `uv.lock` pins one.
- **Start ruff with a short `select` list** and add rules when they catch something real.
  `select = ["ALL"]` produces a wall of findings nobody fixes, and then a habit of ignoring
  the linter.
- **Environment variables** go in a gitignored `.env`, with a committed `.env.example`
  listing the names. `uv run --env-file .env tools/x.py` loads it; no `python-dotenv`
  needed for tooling.

## Git hooks with prek

prek is a drop-in, single-binary replacement for pre-commit. It reads `prek.toml` or an
existing `.pre-commit-config.yaml`; keep the YAML if the repo already has one, use TOML
for a new repo.

**Run ruff as a local hook through `uv run`, not from `ruff-pre-commit`.** That repo pins
its own ruff `rev`, separate from `uv.lock`, so the hook and CI drift apart one
`prek update` at a time — the hook reformats, CI's ruff disagrees, and the diff
ping-pongs. One version, decided by the lockfile:

```toml
default_install_hook_types = ["pre-commit", "pre-push"]

[[repos]]
repo = "builtin"
hooks = [
  { id = "trailing-whitespace" },
  { id = "end-of-file-fixer" },
  { id = "check-yaml" },
  { id = "check-added-large-files" },
]

[[repos]]
repo = "local"

[[repos.hooks]]
id = "ruff-check"
name = "ruff check"
language = "system"
entry = "uv run --locked ruff check --fix"
types = ["python"]

[[repos.hooks]]
id = "ruff-format"
name = "ruff format"
language = "system"
entry = "uv run --locked ruff format"
types = ["python"]

[[repos.hooks]]
id = "pytest"
name = "pytest"
language = "system"
entry = "uv run --locked pytest -q"
pass_filenames = false
always_run = true
stages = ["pre-push"]
```

```bash
uv run prek install         # installs both hook types from default_install_hook_types
uv run prek run --all-files # once now: a hook nobody has run on the whole tree will fail
                            # on the first commit that touches an old file
```

prek is a dev dependency, not a global tool, for the same reason ruff is. Lint runs before
format because `--fix` can leave code the formatter would change; the reverse order can
leave a fixed file unformatted.

Split by cost: formatting and lint on commit, tests on push. A pre-commit hook that takes
thirty seconds gets bypassed with `--no-verify`, and then it protects nothing. `--locked`
makes the hook fail when `uv.lock` is out of date with `pyproject.toml`, instead of
quietly re-resolving inside a git hook.

If prek rejects a builtin hook id, it says so on the first run — that is why the
`--all-files` run is not optional.

## The gate

One name for "is this change good", used by people, the agent, the hook and CI alike:

```just
set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

check:
    uv run --locked ruff format --check .
    uv run --locked ruff check .
    uv run --locked pytest -q
```

Without the `windows-shell` line the recipe needs `sh`, which stock Windows does not have.

CI runs `uv sync --locked` then `just check` — or `uv run prek run --all-files` plus the
tests. Never a separate list of commands that drifts from the local one.

## Every OS

The same repo gets cloned on macOS, Linux and Windows. Everything above already works on
all three; these are the places it breaks when nobody checked:

- **Line endings.** `.gitattributes` with `* text=auto eol=lf`, plus `*.bat text eol=crlf`
  and `*.cmd text eol=crlf` (one pattern per line; git patterns have no `{a,b}`). A script or shebang checked out with CRLF fails with "bad
  interpreter" on Linux and macOS.
- **No activation.** `uv run` hides `.venv\Scripts` versus `.venv/bin`. Docs that say
  `source .venv/bin/activate` are wrong on half the machines.
- **No shell in the tooling.** prek's `language = "system"` hooks spawn `uv` directly, so
  they work in PowerShell, cmd and bash alike. Do not wrap an entry in `bash -c`, and
  write helper logic as a Python script, not a shell one.
- **`just` on Windows** runs recipes with `sh` unless told otherwise. The `windows-shell`
  line in the gate above fixes that; recipes that are only `uv run …` lines then behave the
  same everywhere.
- **Paths in code.** `pathlib`, never string concatenation with `/` or `\`; open text
  files with `encoding="utf-8"`. Before Python 3.15, Windows defaults to the locale code
  page, so the same code reads a file differently there unless it says which encoding.

## When something disagrees between two machines

Check in order: `uv --version`; `.python-version` committed?; `uv.lock` committed and
`uv sync --locked` clean?; is the command going through `uv run` or hitting a global
binary (`where ruff` / `which ruff`)? The last one is the usual answer.
