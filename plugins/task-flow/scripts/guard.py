# /// script
# requires-python = ">=3.14"
# ///
"""PreToolUse hook: enforce task-flow's agent boundaries deterministically.

Plugin agent frontmatter cannot restrict Write to a directory, and prompts are advice,
not limits. This hook reads `agent_type`, which Claude Code adds to hook input for calls
made inside a subagent, and applies the rules below only to task-flow's own agents.
Calls from the main session and from any other agent pass through untouched.

The rules themselves live in `scripts/rules.py` (`check_command`, `check_write`), shared
with `scripts/implement.py`, so this hook and the external implementer are held to the
same rules by one implementation:

- planner, backlog, checker, reviewer: may write files only under a `.work/` directory.
- every task-flow agent: no push, no PR, no deploy — nothing leaves the machine.
- every task-flow agent: no `git commit`; committing is `/task-finish`'s job, after review.
- every task-flow agent: no snoozing a habit-hooks finding — writing
  `.habit-hooks/snooze.json` or running `habit-snooze --snooze` / `--prune` is denied.
- planner, backlog, reviewer: no git command that changes the working tree, index or branches.

Stdlib only, started by `uv run --script`, so it runs on macOS, Linux and Windows.
"""

import json
import re
import sys

from rules import check_command, check_write

ROLE = re.compile(r"^task-flow[:_](planner|implementer|checker|reviewer|backlog)$")

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}


def deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"task-flow: {reason}",
                }
            }
        )
    )
    sys.exit(0)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except ValueError:
        return
    match = ROLE.match(str(payload.get("agent_type") or ""))
    if not match:
        return
    role = match.group(1)
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}

    if tool in WRITE_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        reason = check_write(path, role)
        if reason is not None:
            deny(reason)

    if tool in SHELL_TOOLS:
        command = str(tool_input.get("command") or "")
        reason = check_command(command, role)
        if reason is not None:
            deny(reason)


if __name__ == "__main__":
    main()
