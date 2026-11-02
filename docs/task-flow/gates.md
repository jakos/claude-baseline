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
