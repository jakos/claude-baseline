# Notes: case task-list

- source task: `.work/task-list/` of this repository (phase `done`, last verdict
  `APPROVED`, 2 rounds; the final approved plan is the `plan.md` copied here).
- base: `4daf4197b4dc24b4c78a501ab4269be89a6d7c4e` (`4daf419`)
- result: `7216e6c09975f164e51686f59eb83a5114beafd5` (`7216e6c`), the only child of the base
- created: 2026-11-02 by `bench.py add-case` from this repository, then edited
  (`commands` selectors, `plan.md` adaptations below).

## Hidden test selection (E1)

Rule, applied to every case alike: a test method is selected exactly when its name
occurs in the case's `plan.md`; a whole file is used only when every test in it is named
in the plan or already exists, unchanged, at the base. The `commands` in `case.toml`
pass `Class.method` selectors to `uv run --script`, one command per test file.

- `plugins/task-flow/tests/test_task_list.py` at the result has 14 test methods; 13 are
  named in the plan and selected. Excluded (not named in the plan):
  `TaskListTest.test_undecodable_status_is_listed_as_unrecognised` (added by a review
  suggestion after the plan).
- Selection is mechanical, not perfect: a selected test may assert details the review
  rounds tightened. Here the 13 selected tests all derive from the plan.

## Adaptations to the copied plan (E3 only; original lines quoted)

- (a) `main` as the base ref becomes `HEAD` in one Verification command, because the
  scratch base commit is `HEAD`:
  `git diff --exit-code main -- plugins/task-flow/skills/task-status/SKILL.md`
  → `git diff --exit-code HEAD -- plugins/task-flow/skills/task-status/SKILL.md`
- (c) every `uvx ruff` is pinned to `uvx ruff@0.16.9`, the version with which
  `check-case` passes (ruff 0.16.9 is what `uvx ruff --version` reported at planning):
  `uvx ruff check plugins/task-flow/scripts plugins/task-flow/tests`
  → `uvx ruff@0.16.9 check plugins/task-flow/scripts plugins/task-flow/tests`
  `uvx ruff format --check plugins/task-flow/scripts plugins/task-flow/tests`
  → `uvx ruff@0.16.9 format --check plugins/task-flow/scripts plugins/task-flow/tests`
- (d) removed, because gates.py (the harness, newer than this task and not pinnable)
  parses quotes without escapes, so the inner `\"` mangles the pattern and the command
  fails on the result tree only for that reason. Failure line on the result tree:
  `fatal: command line, '^(name: task-list|disable-model-invocation: true|argument-hint: \\[--all\\]\\)|task_list\\.py': Unmatched ( or \(`
  Original line:
  `git grep -nE "^(name: task-list|disable-model-invocation: true|argument-hint: \"\[--all\]\")|task_list\.py" -- plugins/task-flow/skills/task-list/SKILL.md`
- (d) removed for the same reason. Failure line on the result tree:
  `fatal: command line, '\version\: \0\.3\.0\': Trailing backslash`
  Original line:
  `git grep -nE "\"version\": \"0\.3\.0\"" -- plugins/task-flow/.claude-plugin/plugin.json .claude-plugin/marketplace.json`

Risk of the (d) removals: the case no longer checks the skill frontmatter grep or the
version grep; the hidden tests and the remaining visible commands still separate base
from result. `uvx ruff@0.16.9` must stay downloadable.

## check-case result

`uv run --script plugins/task-flow/bench/bench.py check-case task-list` exits 0
(2026-11-02): all seven checks PASS — load, commits, hidden-files, no-leak,
visible-on-result (7 commands PASS or Not run on the result tree), hidden-on-result,
hidden-on-base.
