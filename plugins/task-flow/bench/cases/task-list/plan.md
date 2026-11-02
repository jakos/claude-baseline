# Plan: Add /task-flow:task-list, a script-backed list of open tasks sorted by what to do next

## Goal
Add the command `/task-flow:task-list [--all]`. With no argument it lists the task-flow
tasks under `.work/` whose phase is not `done`. With `--all` it lists every task. A
stdlib-only script, `plugins/task-flow/scripts/task_list.py`, builds the table from
`.work/*/status.md`, so the output is the same every time. The command skill only runs
that script and shows what it prints. Rows are sorted by the ordering rule below, which
the user confirms when approving this plan. `task-status` does not change.

### Ordering rule (for confirmation at approval)
The script sorts by a three-part key, in this order:

1. **Phase group** (lower rank first):
   | rank | group | phases |
   | 0 | waiting on the user | `planned`, `blocked`, `escalated` |
   | 1 | in progress | `approved`, `baseline`, `implementing`, `checking`, `reviewing`, `review-approved` |
   | 2 | not started | `new` |
   | 3 | unrecognised | a missing `phase:` line, or a value not in the status.md enum |
   | 4 | finished | `done` (listed only with `--all`) |
2. **Last History timestamp, oldest first** (the task that has waited longest comes
   first). This is the ISO timestamp at the start of the last bullet under `## History`.
   It is not the file's last bullet, because `## Log` follows `## History`. A timestamp
   without a zone counts as UTC. A row with no History bullet, or with a timestamp that
   does not parse, sorts after every row in its group that has one.
3. **Task id**, ascending, so the order is fully deterministic.

`round` and `last-verdict` are shown in the table but do not affect the order. This is
deliberate: phase already says who has to act next. Ranking by round as well would let a
round-3 `implementing` task jump ahead of an older round-1 one for no stated reason. If
the user wants a different rule, they change it at approval. Two likely changes:
`review-approved` moved into group 0, because it waits on the user to run `task-finish`;
or a higher round first inside group 1.

## Context
- Plugin root: `plugins/task-flow/`. Commands are skills at `skills/<name>/SKILL.md`,
  with `disable-model-invocation: true`. Every existing command body starts with "Read
  orchestration.md and SKILL.md first". The new one should not: it does no orchestration.
- `skills/task-status/SKILL.md` already lists every task when it gets no argument, and
  the model produces that list. It stays as it is (Constraint).
- `status.md` format: `skills/workflow/orchestration.md → status.md`. Header bullets
  `- phase:`, `- round:`, `- last-verdict:`, then `## History` with bullets
  `- <ISO timestamp> <phase> — <text>`, then `## Log`. The History text contains an em
  dash (U+2014). Live examples: `.work/task-list/status.md`, `.work/acd-followups/`,
  `.work/auto-mode-acd/`.
- Existing script pattern: `scripts/guard.py`. It has a PEP 723 header
  (`requires-python = ">=3.14"`), uses only the stdlib, runs through `uv run --script`,
  and has a module docstring. Its test, `tests/test_guard.py`, uses `unittest`, carries
  its own PEP 723 header, runs the script with `subprocess` and `sys.executable`, and is
  run with `uv run --script plugins/task-flow/tests/test_guard.py`.
- The repo has no pyproject, justfile, Makefile, CI config, ruff config or markdownlint
  config. Verification commands are therefore the two test scripts plus ruff with its
  default settings, called through `uvx`. uv 0.12 is on PATH. The dev-baseline
  PostToolUse hook already runs ruff format on Python files when they are written.
- Versions: `plugins/task-flow/.claude-plugin/plugin.json` and the `task-flow` entry in
  `.claude-plugin/marketplace.json` are both `0.2.1`. The repo reviewer
  (`.claude/agents/baseline-reviewer.md`) expects a version bump whenever behaviour
  changes.
- Windows is the primary dev OS. If stdout is not forced to UTF-8, printing an em dash
  through a pipe can raise `UnicodeEncodeError` (cp1252).

## Profiles
python: plugins/task-flow/scripts/task_list.py, plugins/task-flow/tests/test_task_list.py
docs: plugins/task-flow/skills/task-list/SKILL.md, plugins/task-flow/README.md
config: plugins/task-flow/.claude-plugin/plugin.json, .claude-plugin/marketplace.json

## Steps
1. **Script.** Create `plugins/task-flow/scripts/task_list.py`, following the style of
   `guard.py`: PEP 723 header with `requires-python = ">=3.14"` and no dependencies, a
   module docstring, stdlib only.
   - CLI (argparse): `--all` and `--root PATH`. `--root` is the directory that contains
     `.work/` and defaults to the current directory. An unknown argument exits 2 with
     argparse's usage message.
   - Read every `<root>/.work/*/status.md` as UTF-8. The task id is the directory name.
     From the header, take `phase`, `round` and `last-verdict` (`?` when missing). Take
     the last `- ` bullet between `## History` and the next `## ` heading. Its leading
     timestamp is the first whitespace-separated token, parsed with
     `datetime.fromisoformat`.
   - Without `--all`, drop rows whose phase is `done`. Sort by the ordering rule in Goal.
   - Print a Markdown table: header `| id | phase | round | last verdict | last History |`,
     a separator row, then one row per task. The last-History cell is the bullet text
     without its leading `- `. A `|` inside it is escaped as `\|`.
   - When rows were hidden, print one line after the table:
     `<n> done task(s) hidden; --all lists them.`
   - When there are no rows (no `.work/`, no status files, or every task done), print
     `No open tasks under .work/.` (with `--all`: `No tasks under .work/.`) and exit 0.
     Also print the hidden-count line when it applies.
   - A status.md that cannot be decoded or parsed does not crash the script. Its row
     shows `?` for the missing fields and sorts in group 3.
   - Before printing, call `sys.stdout.reconfigure(encoding="utf-8")`.
   Profile: python.
2. **Tests.** Create `plugins/task-flow/tests/test_task_list.py` in the style of
   `test_guard.py`: PEP 723 header, `unittest`, the script run through `subprocess`
   with `sys.executable` and `--root` set to a `tempfile.TemporaryDirectory` that holds
   fixture `status.md` files. Required test names, one per behaviour:
   `test_hides_done_by_default`, `test_all_includes_done`,
   `test_reports_hidden_done_count`, `test_orders_by_phase_group`,
   `test_ties_broken_by_oldest_history_first`, `test_ties_broken_by_id_last`,
   `test_last_history_line_ignores_log_section`,
   `test_missing_or_bad_timestamp_sorts_last_in_group`,
   `test_unknown_or_missing_phase_sorts_before_done`, `test_no_work_dir_prints_message`,
   `test_escapes_pipe_in_history`, `test_utf8_em_dash_survives_pipe` (runs with
   `PYTHONIOENCODING` and `PYTHONUTF8` removed from the child's environment, captures
   bytes, and asserts exit 0 and that the decoded output contains `—`),
   `test_unknown_argument_exits_2`. Profile: python.
3. **Command skill.** Create `plugins/task-flow/skills/task-list/SKILL.md` with frontmatter
   `name: task-list`, a `description` that says it lists open task-flow tasks sorted by
   what to do next and that `--all` includes done tasks, `argument-hint: "[--all]"`, and
   `disable-model-invocation: true`. The body tells the main session to run
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/task_list.py" $ARGUMENTS`
   from the repository root and show its output verbatim. It also says: do not read
   `status.md` files, do not re-sort, summarise or add rows, and change nothing. If the
   script exits non-zero, show its stderr. The body does not tell the model to read
   orchestration.md or SKILL.md. Profile: docs.
4. **README.** Edit `plugins/task-flow/README.md`:
   - Add a row to the Commands table for `task-list [--all]`: open tasks, ordered by what
     to do next; `--all` includes done ones. Place it before `task-status`.
   - Add one short paragraph or list under the table that states the ordering rule as
     approved.
   - Next to the guard test command, add the run command for the new test:
     `uv run --script plugins/task-flow/tests/test_task_list.py`.
   - In Layout, change `the 8 commands` to `the 9 commands`, and add `scripts/task_list.py`
     next to `scripts/guard.py`.
   Profile: docs.
5. **Version.** Bump task-flow from `0.2.1` to `0.3.0` in
   `plugins/task-flow/.claude-plugin/plugin.json` and in the `task-flow` entry of
   `.claude-plugin/marketplace.json`. Profile: config.

## Acceptance criteria
AC1. With no argument the script lists only tasks whose phase is not `done`; `--all` also
lists `done` tasks. Verify: `test_hides_done_by_default`, `test_all_includes_done`,
`test_reports_hidden_done_count` pass in `tests/test_task_list.py`.

AC2. Rows follow the ordering rule in Goal: phase group, then oldest last History
timestamp, then id. Unknown or missing phases come after `new` and before `done`.
Missing or bad timestamps come last within their group. Verify:
`test_orders_by_phase_group`, `test_ties_broken_by_oldest_history_first`,
`test_ties_broken_by_id_last`, `test_missing_or_bad_timestamp_sorts_last_in_group`,
`test_unknown_or_missing_phase_sorts_before_done` pass.

AC3. The table has the columns id, phase, round, last verdict and last History. The last
History cell comes from the last bullet of `## History`, not from `## Log`, with `|`
escaped. Verify: `test_last_history_line_ignores_log_section`,
`test_escapes_pipe_in_history` pass.

AC4. The script exits 0 and prints the no-tasks message when `.work/` is missing or
empty. It writes UTF-8 to a pipe on Windows without `PYTHONIOENCODING`. It exits 2 on an
unknown argument. Verify: `test_no_work_dir_prints_message`,
`test_utf8_em_dash_survives_pipe`, `test_unknown_argument_exits_2` pass.

AC5. The script uses only the stdlib, runs through `uv run --script`, and passes ruff
lint and format checks with default settings. Verify: commands 1, 3, 4 and 5 in
Verification commands exit 0, and
`git grep -nE "^(import|from) " -- plugins/task-flow/scripts/task_list.py` shows only
stdlib modules (manual: read the import list).

AC6. `skills/task-list/SKILL.md` exists with `name: task-list`,
`disable-model-invocation: true` and `argument-hint: "[--all]"`, and its body runs
`scripts/task_list.py` through `uv run --script` with `$ARGUMENTS`. Verify: command 6
prints all four lines; manual: the body tells the model to show the output verbatim and
not to read status.md files.

AC7. `task-status` is unchanged. Verify: command 7 prints nothing.

AC8. The README documents the command, the ordering rule and the new test command, and
says 9 commands. Verify: command 8 prints a match for each of `task-list`,
`test_task_list.py` and `9 commands`; manual: the ordering rule written in the README
matches the approved rule in plan.md Goal.

AC9. task-flow is version `0.3.0` in both manifests. Verify: command 9 prints two lines
that contain `0.3.0`.

## Verification commands
```
uv run --script plugins/task-flow/tests/test_task_list.py
uv run --script plugins/task-flow/tests/test_guard.py
uvx ruff@0.16.9 check plugins/task-flow/scripts plugins/task-flow/tests
uvx ruff@0.16.9 format --check plugins/task-flow/scripts plugins/task-flow/tests
uv run --quiet --script plugins/task-flow/scripts/task_list.py --all --root .
git diff --exit-code HEAD -- plugins/task-flow/skills/task-status/SKILL.md
git grep -nE "task-list|test_task_list\.py|9 commands" -- plugins/task-flow/README.md
```
Commands 6–9 (the `git grep` and `git diff` lines) inspect working-tree files, so they
also see unstaged changes. On the untouched base, commands 1, 6, 8 and 9 are expected to
fail, and ruff may report the missing paths. That is the round-0 baseline, not a blocker.

## Project rules
No `.claude/task-flow.md` in this repo. Rules that apply, from `task.md` and the repo:
- `task-status` keeps its current behaviour, including listing every task when called
  with no argument. Do not edit `skills/task-status/SKILL.md`.
- Do not change `disable-model-invocation` on any skill. The new skill sets it to `true`.
- Commit messages and PR text carry no attribution lines (`Co-Authored-By`,
  `Claude-Session` or similar). A dev-baseline hook enforces this.
- Scripts run on macOS, Linux and Windows without Git Bash: stdlib only, started by
  `uv run --script`, UTF-8 read and written explicitly, no bash, `jq` or `sed`, and no
  assumption that ruff is installed at runtime.
- Line endings LF (`.gitattributes`: `* text=auto eol=lf`).

## Out of scope
- Any change to `skills/task-status/SKILL.md`, or to `orchestration.md → Resolving the
  task` (it still refers to task-status's listing).
- Making any command model-invocable.
- Changing the `status.md` format, the agents, `guard.py`, `hooks/hooks.json`, or any
  other command.
- Edits to the root `README.md` or `GETTING-STARTED.md`. The root README does not list
  task-flow commands.
- Extra columns or "next command" hints beyond the five columns above; filters other
  than `--all`; JSON output.
- dev-baseline, and anything under `plugins/dev-baseline/`.

## Risks / open questions
BLOCKED: no
- **The ordering rule needs the user's confirmation** at the approval gate (task.md goal 4).
  The plan uses the rule in Goal. Choices to confirm: `review-approved` in group 1 rather
  than group 0; round and last-verdict not used for sorting; unknown phases between `new`
  and `done`. If the user changes the rule, record it in `task.md` and re-plan, so that
  AC2 and its tests match.
- `$ARGUMENTS` goes to the script unquoted. Arguments other than `--all` exit 2 with
  usage, and the skill shows that output, so an unexpected argument cannot fail silently.
- The skill runs the script with the main session's cwd as the root. That is the
  repository root in normal use. Inside a worktree there is no `.work/`, so the script
  prints the no-tasks message. That is correct behaviour, and command 5 relies on it when
  the checks run from a worktree.
- `${CLAUDE_PLUGIN_ROOT}` in a skill body is assumed to be substituted, as every existing
  task-flow command already assumes. Not verified against the docs here.
