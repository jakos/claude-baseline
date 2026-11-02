# Notes: case autopilot-plan-and-run-tasks

- source task: `.work/autopilot-plan-and-run-tasks/` of this repository (phase `done`,
  last verdict `APPROVED`, 5 rounds; the final approved plan is the `plan.md` copied
  here).
- base: `0ed235625d41a0d355209016b27187451e7f582e` (`0ed2356`, a merge)
- result: `80737dd54b1eb880b5bece5c72763855cf62cd93` (`80737dd`), the only child of the
  base
- created: 2026-11-02 by `bench.py add-case` from this repository, then edited
  (`commands` selectors, `plan.md` adaptations below).

## Hidden test selection (E1)

Rule, applied to every case alike: a test method is selected exactly when its name
occurs in the case's `plan.md`; a whole file is used only when every test in it is named
in the plan or already exists, unchanged, at the base. The `commands` in `case.toml`
pass `Class.method` selectors to `uv run --script`, one command per test file.

- `plugins/task-flow/tests/test_autopilot.py` at the result has 39 test methods; 26 are
  named in the plan and selected. Excluded (not named in the plan; they came from
  review fix lists in later rounds or post-approval suggestions):
  `EstimateTest.test_estimate_pending_excludes_skipped_dependents`,
  `EstimateTest.test_plan_counts_ignore_headings_in_fenced_blocks`,
  `EstimateTest.test_fence_closes_only_on_matching_fence`,
  `EstimateTest.test_unclosed_fence_does_not_zero_later_sections`,
  `EstimateTest.test_tilde_and_indented_command_blocks_are_counted`,
  `EstimateTest.test_estimate_planning_tokens_unknown_says_at_least`,
  `EstimateTest.test_estimate_accepts_unapproved_tasks`,
  `MalformedTest.test_missing_run_md_exits_2`,
  `MalformedTest.test_undecodable_run_md_exits_2`,
  `MalformedTest.test_task_row_with_wrong_cell_count_exits_2`,
  `InspectTest.test_inspect_rejects_unreadable_claude_md`,
  `InspectTest.test_inspect_refuses_when_git_is_missing`,
  `InspectTest.test_inspect_rejects_subdirectory`.
- `plugins/task-flow/tests/test_guard.py` is used whole (no selectors): at the result it
  is the base file plus the `READ_ONLY_ROLES`/`ALL_ROLES` tuple change and
  `test_backlog_writes_only_under_work` (named in the plan); every other test in it
  already exists, unchanged, at the base.
- Selection is mechanical, not perfect: a selected test may assert details the review
  rounds tightened — here e.g. the subTests of `test_malformed_run_exits_2`.

## Adaptations to the copied plan (E3 only; original lines quoted)

- (c) every `uvx ruff` is pinned to `uvx ruff@0.16.9`, the version with which
  `check-case` passes:
  `uvx ruff check plugins/task-flow/scripts plugins/task-flow/tests`
  → `uvx ruff@0.16.9 check plugins/task-flow/scripts plugins/task-flow/tests`
  `uvx ruff format --check plugins/task-flow/scripts plugins/task-flow/tests`
  → `uvx ruff@0.16.9 format --check plugins/task-flow/scripts plugins/task-flow/tests`
- (d) removed, because gates.py (the harness, newer than this task and not pinnable)
  parses quotes without escapes, so the inner `\"` mangles the pattern and the command
  fails on the result tree only for that reason. Failure line on the result tree:
  `fatal: command line, '\version\: \0\.5\.0\': Trailing backslash`
  Original line:
  `git grep -nE "\"version\": \"0\.5\.0\"" -- plugins/task-flow/.claude-plugin/plugin.json .claude-plugin/marketplace.json`
- (d) removed for the same reason: after the inner `\"` closes the quoted argument, a
  raw `|` sits outside quotes, so gates.py refuses the line. Failure on the result tree:
  `refused: the character '|' needs a shell; gates.py runs one program per line, without a shell`
  Original line:
  `git grep --untracked -nE "autopilot\.py\" inspect|commits: no|initial commit|thin|bootstrap" -- plugins/task-flow/skills/workflow/orchestration.md plugins/task-flow/README.md`

Risk of the (d) removals: the case no longer checks the version grep or the
`autopilot.py inspect` prose grep; the hidden tests and the remaining visible commands
still separate base from result. `uvx ruff@0.16.9` must stay downloadable.

## check-case result

`uv run --script plugins/task-flow/bench/bench.py check-case autopilot-plan-and-run-tasks`
exits 0 (2026-11-02): all seven checks PASS — load, commits, hidden-files (2 files),
no-leak, visible-on-result (12 commands PASS or Not run on the result tree),
hidden-on-result (2 commands), hidden-on-base (both FAIL or ERROR on the base).
