# task-flow configuration

<!-- Project rules for the task-flow planner in THIS repository (claude-baseline).
     Format: plugins/task-flow/templates/task-flow.md. Plain Markdown: agents read it,
     no parser does. -->

## Verification commands

<!-- One gate for every profile: this list replaces the defaults of python, docs,
     config and generic alike. It matches the checks of [targets.task-flow] in
     .claude/task-flow.release.toml. There is no shell glob: a new
     plugins/task-flow/tests/test_*.py file is added here and there by hand. -->

### python, docs, config, generic
```
uv run --script plugins/task-flow/tests/test_autopilot.py
uv run --script plugins/task-flow/tests/test_gates.py
uv run --script plugins/task-flow/tests/test_guard.py
uv run --script plugins/task-flow/tests/test_implement.py
uv run --script plugins/task-flow/tests/test_launch.py
uv run --script plugins/task-flow/tests/test_models.py
uv run --script plugins/task-flow/tests/test_prune_task.py
uv run --script plugins/task-flow/tests/test_release.py
uv run --script plugins/task-flow/tests/test_rules.py
uv run --script plugins/task-flow/tests/test_task_list.py
uvx ruff check plugins/task-flow/scripts plugins/task-flow/tests
uvx ruff format --check plugins/task-flow/scripts plugins/task-flow/tests
claude plugin validate .
```

## Architecture rules

- Feature and fix tasks never change a version number: do not edit `version` in
  `plugins/*/.claude-plugin/plugin.json` or in `.claude-plugin/marketplace.json`, and
  do not write `CHANGELOG.md` entries. Versions and changelogs are set only by
  `/task-flow:task-release`, configured in `.claude/task-flow.release.toml`.
