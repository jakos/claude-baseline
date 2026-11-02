---
name: task-doctor
description: Check whether this machine, this Claude Code session and this repository are ready for task-flow — uv, Python, git, the plugin source, settings and permission rules, ANTHROPIC_API_KEY, models.toml and every profile the implementer uses — and print the exact fix for every WARN and FAIL; --online also checks each profile's model id against the provider's model list (no tokens spent).
argument-hint: "[--online]"
disable-model-invocation: true
---

From the repository root, run:

```
uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" $ARGUMENTS
```

Show its stdout verbatim — do not re-run the script, and do not summarise, re-sort or add
rows. Change nothing and offer no fixes beyond the printed ones. Exit 1 means the FAIL
rows are the result, not an error of the command (exit 2 is a usage error: show stderr).
If the command cannot start because `uv` is not found, print the uv installer one-liners
for PowerShell (`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`)
and POSIX (`curl -LsSf https://astral.sh/uv/install.sh | sh`) and "restart Claude Code".
With `--online` the script sends one authenticated `GET <base_url>/models` per profile in
use, to the provider — that request costs no tokens. See
`${CLAUDE_PLUGIN_ROOT}/skills/workflow/orchestration.md` → Doctor.
