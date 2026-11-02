# Setting up task-flow

Paths such as `scripts/…`, `skills/…`, `agents/…`, `templates/…` and `bench/…` are inside `plugins/task-flow/`.

## Install

From the `claude-baseline` marketplace, in a repository where you want the workflow:

```
/plugin install task-flow@claude-baseline
```

It depends on `dev-baseline`, which is installed with it if missing. It needs
[uv](https://docs.astral.sh/uv/) on `PATH` for its guard hook, like `dev-baseline`.

The workspace ignores itself: `/task-flow:task-new` writes `.work/.gitignore` containing
`*`. To make that explicit for the team as well, add this to the repo's `.gitignore`:

```gitignore
# task-flow workspaces
.work/
```

Then run `/task-flow:task-doctor`: it checks this machine, this session and this
repository (uv, git, permissions, `models.toml`, API keys) and prints the fix for every
WARN and FAIL — see [Doctor](#doctor).

## Permissions

The workflow runs under your normal permission settings; nothing in it bypasses them.
These rules in the project's `.claude/settings.json` let a run go from approval to verdict
without a prompt per command, and were enough for the validation runs:

```json
{
  "permissions": {
    "allow": [
      "Bash(uv run:*)", "Bash(git -C:*)", "Bash(git status:*)", "Bash(git diff:*)",
      "Bash(git log:*)", "Bash(git show:*)", "Bash(git grep:*)", "Bash(git ls-files:*)",
      "Bash(git add:*)", "Bash(git worktree add:*)", "Bash(git switch:*)",
      "Bash(git rev-parse:*)"
    ],
    "ask": ["Bash(git push:*)"]
  }
}
```

Add the project's own tools as needed (`Bash(yamllint:*)`, `Bash(just check)`). A command
an agent cannot run is reported as `Not run` or as a blocker, never silently skipped.

## Doctor

`/task-flow:task-doctor` runs `scripts/doctor.py`, a stdlib-only, read-only check of this
machine, this Claude Code session and this repository: `uv` on `PATH`, the Python the
scripts need, `git` with worktree support, the repository (work tree, a commit, a clean
tree), which task-flow the session loaded, `.claude/task-flow.md`, the settings files, the
recommended permission rules, `ANTHROPIC_API_KEY`, `models.toml`, and every profile the
`[roles] implementer` uses (enabled, key, `base_url`, limits, driver). Each check is one
row — PASS, WARN or FAIL — and every WARN and FAIL has its exact fix under `## Fixes`. The
exit code is 0 when no check is FAIL, 1 otherwise. `--online` adds one authenticated
`GET <base_url>/models` per profile in use — the provider's model list, which costs no
tokens — and says whether the configured `model` is among the listed ids. There is no
`--fix`: the doctor changes nothing, and it never prints a secret (key values are
redacted).
