# task-flow configuration

<!-- Project rules for the task-flow planner in THIS repository (claude-baseline).
     Format: plugins/task-flow/templates/task-flow.md. Plain Markdown: agents read it,
     no parser does. -->

## Verification commands

<!-- One gate for every profile: this list replaces the defaults of python, docs,
     config and generic alike. It matches the checks of [targets.task-flow] in
     .claude/task-flow.release.toml entry by entry, in order, and scripts/verify.py
     refuses to run when the two lists disagree or when a discovered
     plugins/task-flow/tests/test_*.py is missing, duplicated or stale here and there —
     CI (.github/workflows/ci.yml, see docs/development.md) runs that script, so this
     list and the release list are one shared list, not two. There is no shell glob: a
     new plugins/task-flow/tests/test_*.py file is added here and there by hand. The
     Ruff commands pin the exact version (0.17.3) whose settings the committed ruff.toml
     records, so a clean runner matches a local run. -->

### python, docs, config, generic
```
uv run --script plugins/task-flow/tests/test_autopilot.py
uv run --script plugins/task-flow/tests/test_bench.py
uv run --script plugins/task-flow/tests/test_bench_publish.py
uv run --script plugins/task-flow/tests/test_docs.py
uv run --script plugins/task-flow/tests/test_doctor.py
uv run --script plugins/task-flow/tests/test_drivers.py
uv run --script plugins/task-flow/tests/test_gates.py
uv run --script plugins/task-flow/tests/test_guard.py
uv run --script plugins/task-flow/tests/test_implement.py
uv run --script plugins/task-flow/tests/test_launch.py
uv run --script plugins/task-flow/tests/test_models.py
uv run --script plugins/task-flow/tests/test_prune_task.py
uv run --script plugins/task-flow/tests/test_release.py
uv run --script plugins/task-flow/tests/test_rules.py
uv run --script plugins/task-flow/tests/test_task_list.py
uv run --script plugins/task-flow/tests/test_ci.py
uv run --script plugins/task-flow/tests/test_sdlc_status.py
uv run --script plugins/task-flow/scripts/sdlc_status.py check
uv run --script plugins/task-flow/tests/test_epics.py
uv run --script plugins/task-flow/tests/test_task_record.py
uv run --script plugins/task-flow/tests/test_cli_driver.py
uvx ruff==0.17.3 check plugins/task-flow/scripts plugins/task-flow/tests plugins/task-flow/bench
uvx ruff==0.17.3 format --check plugins/task-flow/scripts plugins/task-flow/tests plugins/task-flow/bench
claude plugin validate .
```

## Architecture rules

- Feature and fix tasks never change a version number: do not edit `version` in
  `plugins/*/.claude-plugin/plugin.json` or in `.claude-plugin/marketplace.json`, and
  do not write `CHANGELOG.md` entries. Versions and changelogs are set only by
  `/task-flow:task-release`, configured in `.claude/task-flow.release.toml`.
