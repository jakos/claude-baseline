# Notes: case finish-prunes-task-folder

- source task: `.work/finish-prunes-task-folder/` of this repository (phase `done`,
  last verdict `APPROVED`, 3 rounds; the final approved plan is the `plan.md` copied
  here).
- base: `1a82fa158fa596218f14996e7cd538d0428f1e4b` (`1a82fa1`, a merge whose first parent
  is `4daf419`)
- result: `cbe7683e9dcd9a6935299e20e62bdd73e86875b9` (`cbe7683`), the only child of the
  base
- created: 2026-11-02 by `bench.py add-case` from this repository, then edited
  (`commands` selectors, `plan.md` adaptations below).

## Hidden test selection (E1)

Rule, applied to every case alike: a test method is selected exactly when its name
occurs in the case's `plan.md`; a whole file is used only when every test in it is named
in the plan or already exists, unchanged, at the base. The `commands` in `case.toml`
pass `Class.method` selectors to `uv run --script`.

- `plugins/task-flow/tests/test_prune_task.py` at the result has 16 test methods; 11
  are named in the plan and selected. Excluded (not named in the plan; they came from
  review fix lists in later rounds):
  `PruneTaskTest.test_refuses_missing_status`,
  `PruneTaskTest.test_refuses_undecodable_status`,
  `PruneTaskTest.test_refuses_status_without_phase`,
  `PruneTaskTest.test_junction_removed_target_survives`,
  `PruneTaskTest.test_unremovable_entry_is_reported_and_rest_pruned`.
- The dry run itself is covered by the selected `test_dry_run_deletes_nothing`, which
  is why the (b) removal below loses no behaviour.

## Adaptations to the copied plan (E3 only; original lines quoted)

- (b) removed, together with the prose sentence that explains it, because the command
  depends on files outside the exported tree — it points at the real
  `.work/task-list/` of the original worktree, which does not exist in the scratch
  layout. Original command line:
  `uv run --quiet --script plugins/task-flow/scripts/prune_task.py --dry-run ../../task-list`
  Original prose removed: "Command 6 runs from the worktree root
  `.work/finish-prunes-task-folder/worktree`, so `../../task-list` is `.work/task-list/`;
  it is a dry run and must never be run without `--dry-run` against that folder. If the
  task runs with `worktree: none`, use `.work/task-list` instead."
- (c) every `uvx ruff` is pinned to `uvx ruff@0.16.9`, the version with which
  `check-case` passes:
  `uvx ruff check plugins/task-flow/scripts plugins/task-flow/tests`
  → `uvx ruff@0.16.9 check plugins/task-flow/scripts plugins/task-flow/tests`
  `uvx ruff format --check plugins/task-flow/scripts plugins/task-flow/tests`
  → `uvx ruff@0.16.9 format --check plugins/task-flow/scripts plugins/task-flow/tests`
- (d) removed, because gates.py (the harness, newer than this task and not pinnable)
  parses quotes without escapes, so the inner `\"` mangles the pattern and the command
  fails on the result tree only for that reason. Failure line on the result tree:
  `fatal: command line, '\version\: \0\.4\.0\': Trailing backslash`
  Original line:
  `git grep -nE "\"version\": \"0\.4\.0\"" -- plugins/task-flow/.claude-plugin/plugin.json .claude-plugin/marketplace.json`

Risk of the (d) removal: the case no longer checks the version grep; the hidden tests
and the remaining visible commands still separate base from result.
`uvx ruff@0.16.9` must stay downloadable.

## check-case result

`uv run --script plugins/task-flow/bench/bench.py check-case finish-prunes-task-folder`
exits 0 (2026-11-02): all seven checks PASS — load, commits, hidden-files, no-leak,
visible-on-result (10 commands PASS or Not run on the result tree), hidden-on-result,
hidden-on-base.
