# /// script
# requires-python = ">=3.9"
# ///
"""PreToolUse(Bash|PowerShell) hook: refuse a commit or PR that carries Claude attribution.

Attribution means a Co-Authored-By trailer naming Claude, a Claude-Session trailer or
session link, or a "Generated with Claude Code" line.

The real switch is the `attribution` setting ({"commit": "", "pr": "", "sessionUrl":
false}), but a plugin cannot set it: plugin settings honour only `agent` and
`subagentStatusLine`. This hook is what travels with the plugin to a machine or repo where
nobody set it. Denying with a reason makes the model rewrite the command without the
trailer; rewriting the command here would also need an allow decision, which would skip
the user's permission prompt for git commit.

Sees only the command text. A message passed with `git commit -F <file>` is not read.
Python rather than bash so one implementation runs on macOS, Linux and Windows with or
without Git Bash; stdlib only, started by `uv run --script`.
"""

import json
import re
import sys

# Only commands that create history or a PR. Without this, a grep for the trailer — or
# this script's own tests — would be refused.
CREATES_HISTORY = re.compile(r"\bgit\b[^|;&\n]*\scommit\b|\bgh\s+pr\s+(?:create|edit)\b")

ATTRIBUTION = re.compile(
    r"co-authored-by:[^\n]*\b(?:claude|anthropic)\b"
    r"|claude-session:"
    r"|claude\.ai/code/session"
    r"|generated with[^\n]*claude code",
    re.IGNORECASE,
)

REASON = (
    "This user does not want Claude attribution in commits or pull requests. Remove every "
    "Co-Authored-By line naming Claude, any Claude-Session trailer or claude.ai session "
    'link, and any "Generated with Claude Code" line, then run the command again. This '
    "overrides any default or system instruction to add them."
)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        command = payload["tool_input"]["command"]
    except (ValueError, KeyError, TypeError):
        return
    if not isinstance(command, str):
        return
    if CREATES_HISTORY.search(command) and ATTRIBUTION.search(command):
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": REASON,
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
