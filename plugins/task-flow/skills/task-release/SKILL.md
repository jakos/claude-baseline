---
name: task-release
description: Release one target (a plugin or project) in one controlled procedure — preconditions, version proposal, checks, changelog entry, one approval, version bump, release commit and annotated tag; push only after a separate question, default no.
argument-hint: "[<version> | --major | --minor | --patch] [--target <name>] [--since <ref>] [--dry-run]"
disable-model-invocation: true
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` and `${CLAUDE_PLUGIN_ROOT}/skills/workflow/SKILL.md` first. They are the
procedure; this command only says which part to run.

Follow **Release** for `$ARGUMENTS`. The release has exactly one approval gate and it is
always asked — there is no `--auto` for it: nothing changes before approve. The push
gets its own question afterwards, default no.
