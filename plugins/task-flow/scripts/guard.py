# /// script
# requires-python = ">=3.14"
# ///
"""PreToolUse hook: enforce task-flow's agent boundaries deterministically.

Plugin agent frontmatter cannot restrict Write to a directory, and prompts are advice,
not limits. This hook reads `agent_type`, which Claude Code adds to hook input for calls
made inside a subagent, and applies the rules below only to task-flow's own agents.
Calls from the main session and from any other agent pass through untouched.

- planner, checker, reviewer: may write files only under a `.work/` directory.
- every task-flow agent: no push, no PR, no deploy — nothing leaves the machine.
- every task-flow agent: no `git commit`; committing is `/task-finish`'s job, after review.
- planner, reviewer: no git command that changes the working tree, index or branches.

Stdlib only, started by `uv run --script`, so it runs on macOS, Linux and Windows.
"""

import json
import re
import sys

ROLE = re.compile(r"^task-flow[:_](planner|implementer|checker|reviewer)$")

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

LEAVES_MACHINE = re.compile(
    r"\bgit\b[^|;&\n]*\spush\b"
    r"|\bgh\s+(?:pr\s+(?:create|merge|edit|ready)|release\s+create|repo\s+create)\b"
    r"|\b(?:kubectl|oc)\s+(?:apply|create|delete|replace|patch|scale|rollout|set|annotate|label)\b"
    r"|\bhelm\s+(?:install|upgrade|uninstall|rollback|delete)\b"
    r"|\bdocker\s+push\b|\bpodman\s+push\b"
    r"|\bterraform\s+(?:apply|destroy)\b"
)
# profile-config's own offline check; it renders locally and contacts no cluster.
CLIENT_DRY_RUN = re.compile(r"\b(?:kubectl|oc)\s+apply\b[^|;&\n]*--dry-run=client\b")
COMMITS = re.compile(r"\bgit\b[^|;&\n]*\scommit\b")
MUTATES_GIT = re.compile(
    r"\bgit\b[^|;&\n]*\s(?:add|checkout|switch|reset|restore|merge|rebase|stash|clean|rm|mv"
    r"|cherry-pick|revert|branch\s+-[dDmM]|worktree\s+(?:add|remove))\b"
)


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

    if tool in WRITE_TOOLS and role != "implementer":
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        normalized = "/" + path.replace("\\", "/").lstrip("/")
        if "/.work/" not in normalized:
            deny(
                f"the {role} may only write inside .work/<task-id>/; {path!r} is outside it. "
                "Record findings in the task's .work files instead of changing the code."
            )

    if tool in SHELL_TOOLS:
        command = str(tool_input.get("command") or "")
        segments = re.split(r"[;&|\n]+", command)
        if any(
            LEAVES_MACHINE.search(s) and not CLIENT_DRY_RUN.search(s) for s in segments
        ):
            deny(
                "pushing, opening PRs and deploying are not allowed inside task-flow agents. "
                "Nothing leaves the machine until /task-finish asks the user."
            )
        if COMMITS.search(command):
            deny(
                "task-flow agents do not commit. Leave changes staged; /task-finish commits "
                "once review has approved them."
            )
        if role in {"planner", "reviewer"} and MUTATES_GIT.search(command):
            deny(
                f"the {role} is read-only: this git command changes the tree, index or branches."
            )


if __name__ == "__main__":
    main()
