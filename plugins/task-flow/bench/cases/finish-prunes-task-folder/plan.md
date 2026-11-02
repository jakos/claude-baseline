# Plan: task-finish prunes the task folder to its Markdown history

## Goal
At the end of `/task-flow:task-finish`, `.work/<id>/` keeps only its Markdown history and
the worktree. Every top-level `.md` file stays (`task.md`, `plan.md`, `status.md`,
`summary.md`, `checks-*.md`, `review-*.md`, and any other `.md`). Everything else in the
folder (caches, scripts, temporary files, scratch directories and containers) is deleted.
`worktree/` is never touched by the prune; its fate stays with the existing "remove the
worktree?" question.

This follows the user's decision at plan approval (task.md, "Decisions at plan approval",
2026-10-21): "basically all md files describe history of task should survive ... remove
other files like caches, script, temporary files or containers etc". Because check and
review files survive, `summary.md` may keep citing them. Nothing about how `summary.md` is
written changes.

Keep rule, exactly:
- **Kept**: a top-level entry that is a regular file (not a symlink or junction) whose
  name ends in `.md` (case-insensitive), and the top-level entry named exactly `worktree`.
- **Deleted**: every other top-level entry: directories (`base/`, `.ruff_cache/`,
  `probe*/`, …) including any `.md` files inside them, non-`.md` files (`*.py`, `*.txt`,
  `*.json`, …), symlinks and junctions of any name (unlinked, never followed), and a
  directory whose name happens to end in `.md`.

## Context
- Finish procedure: `plugins/task-flow/skills/workflow/orchestration.md` → "Phase: finish"
  (lines ~144–154, steps 1–6; step 6 is "Phase `done`."). The command stub
  `plugins/task-flow/skills/task-finish/SKILL.md` says "Ask separately before each step
  that leaves the machine or deletes anything".
- Contract/layout: `plugins/task-flow/skills/workflow/SKILL.md` → "Layout".
- Script pattern to copy: `plugins/task-flow/scripts/task_list.py` (PEP 723 header,
  `requires-python = ">=3.14"`, stdlib only, `uv run --script`, UTF-8 stdout) and its test
  `plugins/task-flow/tests/test_task_list.py` (unittest, runs the script via subprocess
  against a temp `.work/`).
- Real finished folder: `.work/task-list/` (phase `done`) holds `.ruff_cache/`, `base/`
  (scratch `edge.py`, `fx/`, `mutant/`, `plugins/`), `checks-0..2.md`, `review-1..2.md`,
  `plan.md`, `status.md`, `summary.md`, `task.md`, `worktree/` (kept by the user).
  Under the new rule only `.ruff_cache` and `base` would go.
- The worktree lives inside the task folder (`.work/<id>/worktree`), so a rule that forgot
  it would destroy a live worktree. That is the main hazard.
- Versions: task-flow `0.3.0` in `plugins/task-flow/.claude-plugin/plugin.json` and in the
  task-flow entry of `.claude-plugin/marketplace.json` (dev-baseline is `0.6.1`, untouched).
  The previous feature (task-list) bumped minor.
- No `.claude/task-flow.md` in this repo. No pyproject; Python is checked with `uvx ruff`
  and the scripts' own unittest files.

## Profiles
- python: plugins/task-flow/scripts/prune_task.py, plugins/task-flow/tests/test_prune_task.py
- docs: plugins/task-flow/skills/workflow/orchestration.md, plugins/task-flow/skills/workflow/SKILL.md, plugins/task-flow/skills/task-finish/SKILL.md, plugins/task-flow/README.md, README.md
- config: plugins/task-flow/.claude-plugin/plugin.json, .claude-plugin/marketplace.json

## Steps
1. **Add `plugins/task-flow/scripts/prune_task.py`** (python). Stdlib only, PEP 723 header
   like `task_list.py`, module docstring stating the keep rule from Goal. CLI:
   `prune_task.py [--dry-run] <task-dir>`.
   - Keep test per top-level entry: name `worktree` (exact, case-sensitive) → keep;
     otherwise keep only if not a symlink/junction (`Path.is_symlink()`,
     `Path.is_junction()`), `Path.is_file()`, and `name.lower().endswith(".md")`.
   - Refuse (message on stderr, **exit 2**, nothing deleted) when: `<task-dir>` is not
     an existing directory; its parent directory's name is not `.work`; `status.md` is
     missing or its `- phase:` header line is not `done`. Read `status.md` as UTF-8; an
     undecodable file is a refusal, not a crash.
   - Otherwise delete every top-level entry that is not kept. Never descend into or modify
     `worktree/`. Symlinks and junctions are unlinked, never followed. Directories via
     `shutil.rmtree(..., onexc=...)` with a handler that clears the read-only bit and
     retries (Windows read-only files, e.g. git objects in scratch clones); a read-only
     top-level file is handled the same way.
   - Stdout: one line per removed entry, sorted by name, `removed: <name>` (with
     `--dry-run`: `would remove: <name>`, and nothing is deleted), then one line
     `kept: <comma-separated kept names present, sorted>`. Exit 0. A second run on an
     already pruned folder removes nothing and exits 0.
   - Force UTF-8 stdout as `task_list.py` does.
2. **Add `plugins/task-flow/tests/test_prune_task.py`** (python). unittest, PEP 723 header,
   runs the script via subprocess against temp `.work/<id>/` fixtures, same style as
   `test_task_list.py`. Test methods (names are part of the acceptance criteria):
   `test_keeps_all_markdown_files`, `test_removes_non_markdown_and_dirs`,
   `test_removes_markdown_inside_scratch_dirs`, `test_never_touches_worktree`,
   `test_refuses_when_not_done`, `test_refuses_outside_work_dir`,
   `test_refuses_missing_dir`, `test_dry_run_deletes_nothing`,
   `test_second_run_is_noop`, `test_removes_read_only_files`,
   `test_symlink_not_followed` (skip with `self.skipTest` when `os.symlink` raises
   `OSError`, e.g. Windows without developer mode).
3. **Update "Phase: finish" in `plugins/task-flow/skills/workflow/orchestration.md`**
   (docs). Steps 1–6 stay as they are. Add a new last step 7 **Prune**: after phase
   `done`, from the repository root run
   `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/prune_task.py" .work/<id>`
   and show its output. Not asked. State the rule in one sentence: every top-level `.md`
   file stays (task, plan, status, summary, checks, reviews), everything else in the folder
   is deleted, and `worktree/` is left to the worktree question in step 5. If the script
   exits non-zero, show stderr and leave the folder as is.
4. **Update `plugins/task-flow/skills/task-finish/SKILL.md`** (docs): the description
   mentions that finish prunes `.work/<id>/` to its Markdown files; the body's "Ask
   separately before each step that leaves the machine or deletes anything" is reworded so
   the prune is the stated exception (asks remain for push, PR and worktree removal).
   Keep `disable-model-invocation: true` and `argument-hint` unchanged.
5. **Update `plugins/task-flow/skills/workflow/SKILL.md` → Layout** (docs): one sentence
   after the layout block saying that after `/task-finish` only the `.md` files and
   `worktree/` (if kept) remain; any other file or directory in the folder is scratch and
   is deleted. No other change to the contract.
6. **Update `plugins/task-flow/README.md`** (docs): the `task-finish` row in the Commands
   table (line ~79) and the flow line (~16) mention the prune; the test-command paragraph
   (~121) adds `uv run --script plugins/task-flow/tests/test_prune_task.py`; the Layout
   line (~178) lists `scripts/prune_task.py`.
   **Update root `README.md`** layout block (lines ~245–246): add
   `scripts/prune_task.py` with a short comment and `tests/test_prune_task.py`.
7. **Bump task-flow `0.3.0` → `0.4.0`** (config) in
   `plugins/task-flow/.claude-plugin/plugin.json` and the task-flow entry of
   `.claude-plugin/marketplace.json`. dev-baseline's version unchanged.

## Acceptance criteria
AC1. Fixture with `task.md`, `plan.md`, `status.md` (phase `done`), `summary.md`,
`checks-0.md`, `checks-1.md`, `review-1.md`, `notes.MD`: after the script all eight files
remain with unchanged contents.
Verify: `test_keeps_all_markdown_files` passes in
`uv run --script plugins/task-flow/tests/test_prune_task.py`.

AC2. Fixture additionally holding `base/edge.py`, `base/fx/`, `.ruff_cache/x`,
`scratch.py`, `out.txt`, `tmp.json`, and a directory named `dir.md/`: after the script all
of these are gone, only the `.md` files remain, and stdout has exactly one
`removed: <name>` line per deleted entry, sorted, followed by a `kept:` line.
Verify: `test_removes_non_markdown_and_dirs` passes.

AC3. A scratch directory containing `.md` files (`base/notes.md`, `base/sub/readme.md`) is
removed as a whole.
Verify: `test_removes_markdown_inside_scratch_dirs` passes.

AC4. A `worktree/` directory with nested files (including a `.git` file and non-`.md`
files) is untouched: same entries and file contents after the run, and it appears in the
`kept:` line.
Verify: `test_never_touches_worktree` passes.

AC5. The script refuses with exit code 2, a stderr message and no deletion when phase is
not `done`, when the parent directory is not named `.work`, and when the directory does
not exist.
Verify: `test_refuses_when_not_done`, `test_refuses_outside_work_dir`,
`test_refuses_missing_dir` pass.

AC6. `--dry-run` deletes nothing and prints `would remove: <name>` for each entry that a
real run would remove; a second real run on a pruned folder removes nothing and exits 0.
Verify: `test_dry_run_deletes_nothing`, `test_second_run_is_noop` pass.

AC7. Read-only files inside a scratch directory and a read-only top-level non-`.md` file
are removed; a symlink (any name, including one ending in `.md`) pointing outside the task
folder is removed without deleting or changing its target (the test may skip where
symlinks cannot be created; a skip is not a failure).
Verify: `test_removes_read_only_files`, `test_symlink_not_followed` pass or skip.

AC8. On the real finished folder `.work/task-list/`, a dry run lists exactly `.ruff_cache`
and `base` as `would remove:`; its `kept:` line names all nine `.md` files
(`checks-0.md`, `checks-1.md`, `checks-2.md`, `plan.md`, `review-1.md`, `review-2.md`,
`status.md`, `summary.md`, `task.md`) and `worktree`; nothing is deleted.
Verify: verification command 6 output; manual: `.work/task-list/` listing unchanged
afterwards.

AC9. `orchestration.md` → Phase: finish keeps steps 1–6 and ends with a Prune step that
comes after phase `done`, runs `prune_task.py`, says it is not asked, keeps the `.md`
files and leaves `worktree/` to the worktree question.
Verify: verification command 7 (`git grep`) finds `prune_task.py` in orchestration.md;
manual: read Phase: finish for the order and wording above.

AC10. `task-finish/SKILL.md` mentions the prune and no longer says to ask before every
deletion, while still asking before push, PR and worktree removal;
`disable-model-invocation: true` and `argument-hint` unchanged.
Verify: verification command 8 (`git grep`); manual: read the file.

AC11. `workflow/SKILL.md` Layout states that after `/task-finish` only `.md` files and
`worktree/` remain; plugin README names `prune_task.py` and `test_prune_task.py`; root
README layout names both.
Verify: verification commands 9 and 10 (`git grep`).

AC12. task-flow version is `0.4.0` in both manifests; manifests still validate.
Verify: verification command 11 (`git grep`) and command 12 (`claude plugin validate .`).

AC13. Existing tests and lint still pass: guard and task-list tests, ruff check and format
over `plugins/task-flow/scripts` and `plugins/task-flow/tests`.
Verify: verification commands 2–5.

## Verification commands
```
uv run --script plugins/task-flow/tests/test_prune_task.py
uv run --script plugins/task-flow/tests/test_task_list.py
uv run --script plugins/task-flow/tests/test_guard.py
uvx ruff@0.16.9 check plugins/task-flow/scripts plugins/task-flow/tests
uvx ruff@0.16.9 format --check plugins/task-flow/scripts plugins/task-flow/tests
git grep -n "prune_task\.py" -- plugins/task-flow/skills/workflow/orchestration.md
git grep -nE "prune|disable-model-invocation: true|argument-hint" -- plugins/task-flow/skills/task-finish/SKILL.md
git grep -nE "task-finish" -- plugins/task-flow/skills/workflow/SKILL.md
git grep -nE "prune_task\.py|test_prune_task\.py" -- plugins/task-flow/README.md README.md
claude plugin validate .
```
The `git grep` lines inspect working-tree files, so they see
unstaged changes too. On the untouched base, commands 1, 6, 7, 10 and 11 are expected to
fail (new files absent); that is the round-0 baseline, not a blocker. Command 12 is
`Not run` if the `claude` CLI is not on PATH.

## Project rules
No `.claude/task-flow.md` in this repo. Rules that apply, from `task.md` and the repo:
- Every `.md` file in `.work/<id>/` survives the finish (user decision, 2026-10-21).
- Never delete or modify `.work/<id>/worktree/`; worktree removal stays a separate,
  asked step (`git worktree remove`). The user kept the `task-list` worktree on purpose.
- Do not delete anything under any real `.work/<id>/` while implementing or checking;
  tests use temp directories only; the only command touching a real task folder is the
  `--dry-run` in verification command 6.
- Commit messages and PR text carry no attribution lines (`Co-Authored-By`,
  `Claude-Session` or similar). A dev-baseline hook enforces this.
- Scripts run on macOS, Linux and Windows without Git Bash: stdlib only, started by
  `uv run --script`, `requires-python = ">=3.14"`, UTF-8 read and written explicitly, no
  bash, `jq` or `sed`, no assumption that ruff is installed at runtime.
- Do not change `disable-model-invocation` on any skill.
- Line endings LF (`.gitattributes`: `* text=auto eol=lf`).

## Out of scope
- Pruning the already finished folders `.work/task-list/`, `.work/acd-followups/`,
  `.work/auto-mode-acd/`, or any real task folder. Users can run the script by hand later.
- Any change to how `summary.md` is written (it may keep citing `checks-*.md` and
  `review-*.md`), to finish steps 1–6, to the worktree question, to push/PR asks, or to
  `.claude/task-flow.md` `## Finish` keys (no new `prune:` config key;
  `templates/task-flow.md` unchanged).
- A `## References` section or any other new content in `summary.md`.
- Pruning on escalation, blocking or cancellation; only `/task-finish` prunes.
- Recursing into kept entries or filtering inside scratch directories; the rule is
  top-level only.
- Changes to `task_list.py`, `task-status`, `guard.py`, hooks, agents, or other phases.
- Archiving deleted files elsewhere.

## Risks / open questions
- The prune runs without a question, which reverses the stub's current "ask before
  deleting anything" for this one step. Justified by task.md ("when task-finish runs,
  delete ...") and the user's approval-gate decision naming what to remove; it touches
  only non-`.md` scratch. Raise at the approval gate if the user prefers an ask.
- Main hazard is the worktree inside the task folder; mitigated by naming `worktree`
  explicitly in the keep rule, never following links, and AC4.
- A `.md` symlink or a directory named `*.md` is deleted, not kept: only regular `.md`
  files count as history. Covered by AC2 and AC7.
- Prune requires `phase: done`, so a finish that stops before setting `done` (failed
  commit) leaves the folder intact — intended. `summary.md` is no longer required by the
  script, since any `.md` set is kept as is.
