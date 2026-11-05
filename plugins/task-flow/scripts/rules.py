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
- external-planner (and any later external producer role): the same command denials as
  the external implementer, and additionally `check_argv` holds its run_command to a
  small allowlist of read-only programs — the deny regexes alone do not make an argv
  read-only, because a program such as `python -c` or a shell redirect is not caught by
  them. Within an allowlisted program, `check_argv` also denies the flags that make it
  mutate: `find`'s `-delete`/`-exec*`/`-ok*`/`-fprint*`/`-fls`, `rg`'s `--pre*`
  preprocessor, and for `git` only a read-only subcommand list behind harmless global
  flags, with no path-taking, config-overriding, file-writing (`--output*`) or
  program-running (`-O`, `--ext-diff`, `--textconv`, pager) flag. The caller
  (role_runtime.run_command) additionally resolves every path-looking argv word and
  refuses any that leaves the selected repository roots. `file` and `jq` are not
  allowlisted: `file -C` writes a compiled magic file and `jq --rawfile`/`--slurpfile`
  read arbitrary files. The producer's file tools are read-only: check_write refuses
  every path, because the trusted runner, not the model, publishes the validated
  artifact.
- every role, the Claude agents and the external implementer alike: no snoozing a
  habit-hooks finding — writing a path that ends in `.habit-hooks/snooze.json` and
  running `habit-snooze --snooze` or `habit-snooze --prune` are denied (habit-hooks'
  own guidance: never snooze without the user's explicit approval).

Documented limits, stated plainly: commands and paths are matched as raw text, without
parsing quotes, so a mutating git phrase inside quotes is still denied for the
read-only roles — the known false positive, pinned in tests/test_guard.py and
tests/test_rules.py (use the Grep tool for such searches). A shell redirect such as
`echo x > file` is not caught by check_command, and neither is a redirect or a `cp`
into `.habit-hooks/snooze.json` — only the write-tool path and the command text are
matched. A program such as `python -c` can still push or commit; nothing here can see
what a program does. That is why the external producer roles additionally use
check_argv's program allowlist: without it, the deny regexes alone would not make a
`run_command` call read-only.

Stdlib only, started by `uv run --script` or imported as a sibling module by guard.py and
implement.py, so it runs on macOS, Linux and Windows.
"""

import posixpath
import re
from pathlib import Path

ROLES = frozenset(
    {
        "planner",
        "implementer",
        "checker",
        "reviewer",
        "backlog",
        "external-implementer",
        "external-planner",
    }
)
CLAUDE_ROLES = frozenset({"planner", "implementer", "checker", "reviewer", "backlog"})
READ_ONLY_ROLES = frozenset({"planner", "reviewer", "backlog"})
# The external producer roles: a model that only reads and returns artifact text, which
# the trusted runner validates and publishes. Their run_command argv is held to
# READ_ONLY_PROGRAMS by check_argv, and they may not write any file.
EXTERNAL_PRODUCER_ROLES = frozenset({"external-planner"})
# The read-only command allowlist of an external producer role's run_command. Anything
# else — interpreters (python, node, ...), shells, uv/uvx, env, xargs, make — is denied
# by name, because the deny regexes alone cannot see what a program does.
READ_ONLY_PROGRAMS = frozenset(
    {
        "cat",
        "diff",
        "du",
        "find",
        "git",
        "grep",
        "head",
        "ls",
        "rg",
        "stat",
        "tail",
        "tree",
        "wc",
        "where",
        "which",
    }
)
# `file` and `jq` are deliberately absent: `file -C` writes a compiled magic file and
# `jq --rawfile`/`--slurpfile`/`-f` read or run arbitrary files.
# find actions that delete files, run programs or write output files.
FIND_MUTATING_PREFIXES = ("-delete", "-exec", "-fprint", "-fls", "-ok")
# rg flags that run an external preprocessor program.
RG_PREPROCESSOR_FLAGS = ("--pre", "--pre-glob")
# git flags that take a path and could point the repository view outside the selected
# one; read-only exploration never needs them.
GIT_PATH_FLAGS = ("-C", "--git-dir", "--work-tree", "--exec-path", "--namespace")
# the only git subcommands a producer role may run; everything else (config, remote,
# branch, ...) is refused by name.
GIT_READ_ONLY_SUBCOMMANDS = frozenset(
    {"blame", "diff", "grep", "log", "ls-files", "rev-parse", "show", "status"}
)
# harmless git global flags allowed before the subcommand.
GIT_SAFE_GLOBAL_FLAGS = frozenset({"--no-pager", "--version", "-P"})
# git flags that run an external program (a pager, diff driver or textconv filter).
GIT_EXEC_FLAGS = (
    "-O",
    "--ext-diff",
    "--open-files-in-pager",
    "--paginate",
    "--textconv",
)
SHELL_ARG_CHARS = "&|<>`$"

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
HABIT_SNOOZE = re.compile(
    r"\bhabit-snooze(?:\.exe)?\b[^|;&\n]*\s--(?:snooze|prune)\b", re.IGNORECASE
)

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
SNOOZE_WRITE = (
    "snoozing a habit-hooks finding needs the user's explicit approval; do not write "
    ".habit-hooks/snooze.json — fix the finding, or record it for the user"
)
SNOOZE_COMMAND = (
    "snoozing a habit-hooks finding needs the user's explicit approval; do not run "
    "habit-snooze --snooze or --prune — fix the finding, or record it for the user"
)


def _known_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role: {role!r}")


def _is_snooze_file(path: str) -> bool:
    """True when the normalized path ends with the components .habit-hooks/snooze.json.

    Backslashes count as separators, `..` is normalized away, and each component is
    stripped of trailing dots and spaces and casefolded, so `C:\\repo\\.HABIT-HOOKS\\
    Snooze.json.` and `sub/../.habit-hooks/snooze.json` both match.
    """
    parts = [
        part.rstrip(". ").casefold()
        for part in posixpath.normpath(path.replace("\\", "/")).split("/")
    ]
    return parts[-2:] == [".habit-hooks", "snooze.json"]


def check_command(command: str, role: str) -> str | None:
    """Return None to allow `command` for `role`, or the denial reason.

    `command` is the raw command text, as the caller passes it. The leaves-machine rule
    is checked per `;`/`&`/`|`/newline segment (a client dry run in the same segment
    exempts it), the commit rule on the whole command, then the snooze rule per
    segment, then the read-only git rule.
    """
    _known_role(role)
    segments = SEGMENTS.split(command)
    leaves = any(
        LEAVES_MACHINE.search(s) and not CLIENT_DRY_RUN.search(s) for s in segments
    )
    snoozes = any(HABIT_SNOOZE.search(s) for s in segments)
    if role in ("external-implementer", *EXTERNAL_PRODUCER_ROLES):
        if leaves:
            return EXTERNAL_LEAVES
        if COMMITS.search(command):
            return EXTERNAL_COMMITS
        if snoozes:
            return SNOOZE_COMMAND
        return None
    if leaves:
        return CLAUDE_LEAVES
    if COMMITS.search(command):
        return CLAUDE_COMMITS
    if snoozes:
        return SNOOZE_COMMAND
    if role in READ_ONLY_ROLES and MUTATES_GIT.search(command):
        return f"the {role} is read-only: this git command changes the tree, index or branches."
    return None


def _program_name(word: str) -> str:
    """The bare program name of an argv word: last path component, extension stripped."""
    name = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        name = name.removesuffix(suffix)
    return name


def _is_git_path_flag(argument: str) -> bool:
    """True when a git argument takes a path: exact or `=`-attached long flags, -C forms."""
    return argument.startswith("-C") or any(
        argument == flag or argument.startswith(flag + "=") for flag in GIT_PATH_FLAGS
    )


def _is_git_config_flag(argument: str) -> bool:
    """True for `-c`, the attached `-c<key>=<value>` form and any `--config*` form."""
    return (
        argument == "-c"
        or (argument.startswith("-c") and not argument.startswith("--"))
        or argument.startswith("--config")
    )


def _check_git_argv(argv: list[str], role: str) -> str | None:
    """The git-specific argv rules of a producer role.

    Only harmless global flags may precede the subcommand, the subcommand itself must
    be in the read-only list, and no argument anywhere may take a path outside the
    repository, override configuration, write a file or run an external program.
    """
    index = 1
    while index < len(argv) and argv[index].startswith("-"):
        flag = argv[index]
        if flag in GIT_SAFE_GLOBAL_FLAGS:
            index += 1
            continue
        return (
            f"git {flag} is not an allowed global flag (only "
            f"{sorted(GIT_SAFE_GLOBAL_FLAGS)} may precede the subcommand); a git "
            "global flag could point outside the selected repository or run a program"
        )
    if index < len(argv) and argv[index] not in GIT_READ_ONLY_SUBCOMMANDS:
        return (
            f"git {argv[index]!r} is not a read-only subcommand (allowed: "
            f"{sorted(GIT_READ_ONLY_SUBCOMMANDS)})"
        )
    for argument in argv[1:]:
        if _is_git_path_flag(argument):
            return (
                f"git {argument} takes a path and could point outside the selected "
                f"repository; the {role} may not use it"
            )
        if _is_git_config_flag(argument):
            return (
                f"git {argument} overrides configuration and could run a program "
                f"(e.g. a pager); the {role} may not use it"
            )
        if argument == "--output" or argument.startswith("--output="):
            return f"git {argument} writes a file; the {role} is read-only"
        if argument in GIT_EXEC_FLAGS:
            return (
                f"git {argument} runs an external program (a pager, diff driver or "
                f"textconv filter); the {role} may not use it"
            )
    joined = " ".join(argv)
    if MUTATES_GIT.search(joined) or COMMITS.search(joined):
        return (
            f"the {role} is read-only: this git command changes the tree, index or "
            "branches"
        )
    return None


def check_argv(argv: list[str], role: str) -> str | None:
    """Return None to allow this argv list for `role`, or the denial reason.

    Only the external producer roles (the external planner today) are restricted here;
    every other role returns None and is governed by check_command on the raw text. A
    producer's run_command must be one allowlisted read-only program — no interpreters,
    no shells, no uv/uvx, no env/xargs/make — with no shell syntax in any argument, no
    flag that deletes, writes, executes or preprocesses (find actions, rg --pre, the
    git rules of _check_git_argv), and no mutating git subcommand (the same regexes as
    check_command, applied to the joined argv). Path-looking arguments are contained to
    the selected repository roots by the caller (role_runtime.run_command).
    """
    _known_role(role)
    if role not in EXTERNAL_PRODUCER_ROLES:
        return None
    if not argv or not all(isinstance(arg, str) and arg for arg in argv):
        return "the command must be a non-empty argv list of non-empty strings"
    program = _program_name(argv[0])
    if program not in READ_ONLY_PROGRAMS:
        return (
            f"{argv[0]!r} is not in the read-only allowlist {sorted(READ_ONLY_PROGRAMS)}; "
            f"the {role} may only read the repository"
        )
    joined = " ".join(argv)
    for argument in argv[1:]:
        if any(char in SHELL_ARG_CHARS for char in argument) or "\n" in argument:
            return (
                f"shell syntax in argument {argument!r}: run_command starts one program "
                "with an argument list, without a shell (no pipes, redirects, "
                "substitution or expansion)"
            )
    if program == "find":
        for argument in argv[1:]:
            if argument.startswith(FIND_MUTATING_PREFIXES):
                return (
                    f"find {argument} can delete files, run programs or write output "
                    f"files; the {role} may only read the repository"
                )
    if program == "rg":
        for argument in argv[1:]:
            if argument.split("=", 1)[0] in RG_PREPROCESSOR_FLAGS:
                return (
                    f"rg {argument} runs an external preprocessor program; "
                    f"the {role} may only read the repository"
                )
    if program == "git":
        reason = _check_git_argv(argv, role)
        if reason is not None:
            return reason
    reason = check_command(joined, role)
    if reason is not None:
        return reason
    return None


def check_write(path: str, role: str, root: Path | str | None = None) -> str | None:
    """Return None to allow writing `path` for `role`, or the denial reason.

    `root` is the external-implementer's worktree and is required for that role
    (ValueError without it); it is ignored for the Claude roles, whose writes are
    confined to `.work/` directories. The snooze check runs before every other write
    rule; for the external implementer it runs after the outside-worktree check, on
    both the raw path and the resolved worktree-relative path.
    """
    _known_role(role)
    if role != "external-implementer" and _is_snooze_file(path):
        return SNOOZE_WRITE
    if role in EXTERNAL_PRODUCER_ROLES:
        return (
            f"the {role} may not write files ({path}); the trusted runner publishes "
            "the validated artifact"
        )
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
        if _is_snooze_file(path) or _is_snooze_file(relative.as_posix()):
            return SNOOZE_WRITE

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
