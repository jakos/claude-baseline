# Deterministic gates (scripts/gates.py)

Paths such as `scripts/…`, `skills/…`, `agents/…`, `templates/…` and `bench/…` are inside `plugins/task-flow/`.

`scripts/gates.py` is a checker with no model in it. With `[roles] checker = "script"`
in `models.toml`, the baseline and check phases run this script instead of the
`task-flow:checker` subagent (see
[orchestration](../../plugins/task-flow/skills/workflow/orchestration.md)); with any other value or no config,
the subagent runs exactly as before. It reads the first fenced block under
`## Verification commands` of the task's `plan.md`, runs each line from the worktree (or
the repository root), applies `rules.check_command` with the `checker` role, and writes
`.work/<id>/checks-<n>.md` in the contract's format — the same table, failures and
not-run sections the checker agent writes.

```toml
[roles]
checker = "script"
```

Run by hand (round 0 is the baseline; a human may grant rounds above 3):

```
uv run --quiet --script plugins/task-flow/scripts/gates.py \
    --task-dir .work/<task-id> --worktree <path|none> --round <n> [--timeout-seconds S]
```

Exit 0 means `checks-<n>.md` was written, whatever the results (failures are data), and
stdout is one summary line. Exit 2 is a precondition (missing plan, empty command list,
bad arguments, invalid `models.toml`): the reason goes to stderr, nothing is written.

Each non-blank line of the block that does not start with `#` is one command: one
program plus its arguments, no shell. Whitespace outside quotes separates arguments;
`'...'` and `"..."` group their content verbatim and join with adjacent parts
(`--x="a b"` → `--x=a b`); there are no escapes, so a backslash is literal and Windows
paths (`C:\tools\x.exe`) work unchanged on every platform. Unquoted `| & ; < > ( ) $ * ?
` or backticks are refused (they need a shell), and so is a first word of the form
`NAME=value`. Inside quotes they are ordinary characters, so
`git grep --untracked -nE "a|b" -- path` runs `git` with the argument `a|b`. A refused
line is `ERROR` with a `refused:` reason naming the character and is never started.

Order of decisions per line: `rules.check_command(line, "checker")` first (`ERROR`,
`denied: <reason>`), then the split rule above, then program resolution — a word with a
path separator resolves against the run directory (on Windows also via `PATHEXT`),
otherwise `shutil.which`; not found is `Not run`, not FAIL. On Windows a `.cmd`/`.bat`
program starts through `scripts/launch.py`'s `cmd_command_line`, and a cmd
metacharacter in its arguments is `ERROR`. Then the command runs: exit 0 is `PASS`,
non-zero `FAIL`, a timeout `ERROR` (the process tree is killed), an `OSError` at start
`ERROR`.

The per-command timeout is `--timeout-seconds`, else `[gates] command_timeout_seconds`
from `models.toml` (merged user → project, default 600):

```toml
[gates]
command_timeout_seconds = 600
```

stdout and stderr of each command go to one temporary file (never a pipe, so a
surviving grandchild cannot hang the script); the environment is inherited unchanged.
`FAIL` and timeout `ERROR` get an excerpt in `checks-<n>.md`: the output lines matching
a fixed pattern set (pytest/unittest failure names, assertion and exception lines, ruff
diagnostics, compiler-style and `error:`/`fatal:` lines), at most 30 of them plus a
`[... k more ...]` marker; when nothing matches, the last 30 non-blank lines. A timeout
excerpt starts with `timed out after <s> s; the process tree was killed`.

Limits, stated plainly: gates.py runs only the plan's commands — no profile defaults,
so the planner must list every command it wants checked; there is no shell of any kind
(pipes, redirects, `&&`, variables, globs, `cd`); the matching patterns are heuristics,
and a tool with unusual output falls back to its last 30 lines.

## Generated-file freshness: scripts/sdlc_status.py

`uv run --script plugins/task-flow/scripts/sdlc_status.py check` is one entry of the
shared list above. It validates `docs/task-flow/sdlc-status.toml` — this repository's
AI-native SDLC criteria — and fails when the generated `sdlc:status` block of
`README.md` or the page `docs/task-flow/ai-native-sdlc.md` is stale or missing, when a
`done` criterion lacks existing evidence, or when the source is malformed. It never
writes; `render` regenerates both files, and `/task-flow:task-finish` runs `finish` to
mark the criteria a task declares done and re-render before its commit (see
[AI-native SDLC status](ai-native-sdlc.md)). In a repository without the source file
the command is a no-op for `finish` and the entry simply does not exist.

## The same list in CI: scripts/verify.py

`scripts/verify.py` is this repository's CI entry point (`.github/workflows/ci.yml`, see
[Developing claude-baseline](../development.md#continuous-integration-github-actions))
and runs on a Linux and a Windows hosted runner what the local gates run: the
`## Verification commands` block of `.claude/task-flow.md` — which must equal
`[targets.task-flow].checks` of `.claude/task-flow.release.toml` entry by entry, in
order, or the script refuses to run anything. It also refuses when a discovered
`tests/test_*.py` is missing, duplicated or stale in the lists, so a new test file is
added to both lists by hand. Run it locally from the repository root:

```
uv run --script scripts/verify.py
```

Each check runs from the repository root with its argument list (a Windows `.cmd`/`.bat`
tool goes through `scripts/launch.py`), with a per-check timeout, one visible line per
check, and the run continues after failures. The exit code is nonzero when any check
failed, was denied, could not start, timed out or had no program on `PATH` — that exit
code, not a report, is the CI signal (gates.py above stays the task-round checker that
records results as data):

```
PASS uv run --script tests/test_ci.py
FAIL uvx ruff==0.17.3 check plugins/task-flow/scripts plugins/task-flow/tests plugins/task-flow/bench (exit 1)
ERROR claude plugin validate . (timed out after 600 s; the process tree was killed)
verify.py: 17 passed, 2 failed of 19 checks
```
