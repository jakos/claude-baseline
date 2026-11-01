# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Task-flow's denial rules in one place, shared by scripts/guard.py and scripts/implement.py.

`guard.py` (the PreToolUse hook) calls `check_command` / `check_write` with the Claude
subagent's role; `implement.py` (the external implementer) calls them with the
`external-implementer` role. Both callers are therefore held to the same rules by one
implementation. Each function returns None to allow the call, or the denial reason as
plain text; the callers add their own prefix (`task-flow: ` in the hook's
`permissionDecisionReason`, `denied: ` in a tool result). An unknown role raises
ValueError.

Rules per role:

- planner, implementer, checker, reviewer, backlog (the Claude agents): writes are
  confined to a `.work/` directory (the implementer may write anywhere); no push, no
  PR, no deploy — nothing leaves the machine; no `git commit`; planner, reviewer and
  backlog additionally get no git command that changes the tree, index or branches.
- external-implementer: no push, no PR, no deploy and no commit (the same regexes, its
  own reason texts); writes are confined to the worktree root and kept out of `.git`
  and `.work` (its own reason texts). `git add` and read-only commands stay allowed.

Documented limits, stated plainly: commands and paths are matched as raw text, without
parsing quotes, so a mutating git phrase inside quotes is still denied for the
read-only roles — the known false positive, pinned in tests/test_guard.py and
tests/test_rules.py (use the Grep tool for such searches). A shell redirect such as
`echo x > file` is not caught. A program such as `python -c` can still push or commit;
nothing here can see what a program does.

Stdlib only, started by `uv run --script` or imported as a sibling module by guard.py and
implement.py, so it runs on macOS, Linux and Windows.
"""

import re
from pathlib import Path

ROLES = frozenset(
    {"planner", "implementer", "checker", "reviewer", "backlog", "external-implementer"}
)
CLAUDE_ROLES = frozenset({"planner", "implementer", "checker", "reviewer", "backlog"})
READ_ONLY_ROLES = frozenset({"planner", "reviewer", "backlog"})

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
SEGMENTS = re.compile(r"[;&|\n]+")

CLAUDE_LEAVES = (
    "pushing, opening PRs and deploying are not allowed inside task-flow agents. "
    "Nothing leaves the machine until /task-finish asks the user."
)
CLAUDE_COMMITS = (
    "task-flow agents do not commit. Leave changes staged; /task-finish commits "
    "once review has approved them."
)
EXTERNAL_LEAVES = (
    "pushing, opening PRs and deploying are not allowed; "
    "nothing leaves the machine until /task-finish"
)
EXTERNAL_COMMITS = (
    "do not commit; leave changes in the worktree, the script stages them"
)


def _known_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role: {role!r}")


def check_command(command: str, role: str) -> str | None:
    """Return None to allow `command` for `role`, or the denial reason.

    `command` is the raw command text, as the caller passes it. The leaves-machine rule
    is checked per `;`/`&`/`|`/newline segment (a client dry run in the same segment
    exempts it), the commit rule on the whole command, then the read-only git rule.
    """
    _known_role(role)
    segments = SEGMENTS.split(command)
    leaves = any(
        LEAVES_MACHINE.search(s) and not CLIENT_DRY_RUN.search(s) for s in segments
    )
    if role == "external-implementer":
        if leaves:
            return EXTERNAL_LEAVES
        if COMMITS.search(command):
            return EXTERNAL_COMMITS
        return None
    if leaves:
        return CLAUDE_LEAVES
    if COMMITS.search(command):
        return CLAUDE_COMMITS
    if role in READ_ONLY_ROLES and MUTATES_GIT.search(command):
        return f"the {role} is read-only: this git command changes the tree, index or branches."
    return None


def check_write(path: str, role: str, root: Path | str | None = None) -> str | None:
    """Return None to allow writing `path` for `role`, or the denial reason.

    `root` is the external-implementer's worktree and is required for that role
    (ValueError without it); it is ignored for the Claude roles, whose writes are
    confined to `.work/` directories.
    """
    _known_role(role)
    if role == "implementer":
        return None
    if role == "external-implementer":
        if root is None:
            raise ValueError("check_write for external-implementer requires root")
        worktree = Path(root).resolve()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = worktree / candidate
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(worktree)
        except ValueError:
            return f"{path} is outside the worktree"

        def hidden(parts) -> bool:
            return bool({p.rstrip(". ").casefold() for p in parts} & {".git", ".work"})

        raw_parts = () if Path(path).is_absolute() else Path(path).parts
        if hidden(relative.parts) or hidden(raw_parts):
            return f"writing under .git or .work is not allowed ({path})"
        return None
    normalized = "/" + path.replace("\\", "/").lstrip("/")
    if "/.work/" not in normalized:
        return (
            f"the {role} may only write inside .work/<task-id>/; {path!r} is outside it. "
            "Record findings in the task's .work files instead of changing the code."
        )
    return None
