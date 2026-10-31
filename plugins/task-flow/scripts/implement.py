# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Run ONE task-flow implementer round with an OpenAI-compatible chat model.

The same contract as agents/implementer.md, for a model that is not a Claude subagent
(the Mistral API, any OpenAI-compatible host, a local llama.cpp server). The script
sends the implementer's instructions and the task files to the model, lets it work in the
task's worktree through a small set of tools, then stages the result and logs it.

Usage:
  uv run --script plugins/task-flow/scripts/implement.py \\
      --task-dir .work/<task-id> --worktree <path|none> --round <n> [--profile <name>]
      [--heartbeat <seconds>] [--verbose]

  --task-dir   the task's .work/<task-id> folder; the repository root is the parent of
               the .work folder that contains it
  --worktree   the task's worktree, or `none` for the repository root
  --round      1 for the first round; n > 1 also needs review-<n-1>.md
  --profile    a [profiles.<name>] of models.toml (see scripts/models.py); without it
               [roles] implementer must name a profile
  --heartbeat  seconds between heartbeat lines on stderr while a request or a command
               is in flight (default 30; 0 disables, negative is refused)
  --verbose    also log the model's text content of each response

Progress goes to stderr. stdout gets exactly one line and the exit code says how it went:

  0  ok: <k> files changed (<profile>/<model>, <turns> turns, $<cost>)
  1  failed: <reason>   the model loop ran but did not finish: every profile unavailable,
                        a non-retryable HTTP error, max_turns, max_cost_usd or max_context
  2  refused: <reason>  nothing was sent to any model: bad arguments or configuration, or
                        a precondition is not met (plan not approved, checks-0.md or
                        review-<n-1>.md missing, worktree missing, profile unknown,
                        disabled or without its key)
  3  blocked: <reason>  the model reported that the plan is wrong

Files written: <task-dir>/impl-<round>.json (metrics, after exit 0, 1 or 3; besides the
keys documented in the plugin README it carries model_seconds, retry_wait_seconds,
command_seconds, slowest_request and slowest_command, and every fallback_chain entry a
retries list) and <task-dir>/impl-<round>.progress.jsonl (one compact JSON line per counted
turn, appended as the round runs, so an interrupted round still shows its turns; it is
scratch and is deleted at /task-finish, which keeps only impl-*.json); status.md is
appended to under `## Log` only (`round <n> implementer: <path> (A|M|D)` lines on ok,
`BLOCKED: <reason>` on blocked, nothing on failed). After the round the script stages what
the round produced — tracked changes, files written with `write_file`/`edit_file`, and new
files created by commands — but never pre-existing untracked files or build artifacts such
as `__pycache__`; nothing is ever committed.

Limits, stated plainly: commands the model runs are matched as text only (a script can
still push or commit), they run without a shell (no pipes or redirects), their network
access is not blocked, and only an allowlisted set of environment variables reaches them.
File tools are confined to the worktree and cannot write under .git or .work.
"""

import argparse
import http.client
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Self

import models
from guard import CLIENT_DRY_RUN, COMMITS, LEAVES_MACHINE

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_CAP = 20000
GREP_MAX_MATCHES = 200
LIST_MAX_ENTRIES = 500
READ_DEFAULT_LIMIT = 2000
MAX_RETRY_AFTER = 60
CHILD_ENV_EXACT = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "TERM",
    "CI",
    "VIRTUAL_ENV",
}
CHILD_ENV_PREFIXES = ("LC_", "UV_", "PYTHON")
ARTIFACT_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
ARTIFACT_SUFFIXES = (".pyc", ".pyo")
ARTIFACT_NAMES = {".ds_store", "thumbs.db"}

ADAPTER = """\
## Runner adapter (this overrides the instructions above where they differ)

You run inside a script, not inside Claude Code. The script gives you these tools:
read_file, write_file, edit_file, list_files, grep, run_command and blocked.

- The contents of the workflow contract, plan.md, status.md, checks-0.md and (for round
  n > 1) review-<n-1>.md are included in the first user message. Your file tools cannot
  reach the .work folder and cannot write there, so never try to edit status.md.
- To report that the plan is wrong or a step cannot be done, call the `blocked` tool with
  the reason. The script then writes the `BLOCKED:` line to status.md for you.
- Do not run `git add` or `git commit`. The script stages the round's changes itself —
  tracked edits, files you wrote with `write_file`/`edit_file`, and new files your commands
  created, but never pre-existing untracked files or build artifacts such as `__pycache__`
  — and writes the `round <n> implementer:` Log line itself.
- File paths are relative to the worktree or absolute; both must stay inside the worktree.
- run_command starts ONE program with arguments, without a shell: no pipes, redirects,
  `&&`, `;`, globbing or variable expansion. The working directory is the worktree. Use
  it to run the plan's verification commands.
- When you are done, reply with a short final message and NO tool call. That ends the
  round. List the files you changed in that message.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file. offset is the first line (1-based), "
            "limit the number of lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace the single exact occurrence of `old` with `new`. "
            "Fails when `old` occurs zero times or more than once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files matching a glob pattern (default **/*) under a "
            "directory (default the worktree). .git is skipped.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search files for a Python regular expression. Output lines "
            "are path:line:text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run one program with arguments in the worktree, without a "
            "shell. Returns the exit code and the combined output.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blocked",
            "description": "Report that the plan is wrong or a step cannot be done as "
            "planned. Ends the round.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]


class Refusal(Exception):
    """A precondition failed; nothing was sent to any model."""


class Fatal(Exception):
    """A non-retryable model error; the fallback chain is not consulted."""


class Unavailable(Exception):
    """The profile stayed unavailable after its retries; try the next fallback."""


class GitError(Exception):
    pass


class ToolError(Exception):
    """A tool call that cannot be done; returned to the model as the result."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refusal(message)


_LOG_LOCK = threading.Lock()


def log(message: str) -> None:
    with _LOG_LOCK:
        print(f"[implement] {message}", file=sys.stderr, flush=True)


def one_line(text: str, limit: int = 300) -> str:
    return " ".join(str(text).split())[:limit]


def fmt_tokens(count: int) -> str:
    """Token count as an integer, or as `12.3k` when >= 1000."""
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


class Heartbeat:
    """While the block runs, log `<label> ... <elapsed> s` every `interval` seconds.

    The thread is a daemon and is joined with a timeout on exit, so a bug here can
    never hang or change the exit of the round. `interval <= 0` disables it.
    """

    def __init__(self, interval: float, label: str):
        self.interval = interval
        self.label = label
        self.started = time.monotonic()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def __enter__(self) -> Self:
        if self.interval > 0:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=max(self.interval, 1.0) + 1.0)
        return False

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
            elapsed = int(time.monotonic() - self.started)
            log(f"{self.label} ... {elapsed} s")


# ---------------------------------------------------------------------------- output


def truncate(text: str, cap: int = OUTPUT_CAP) -> str:
    if len(text) <= cap:
        return text
    half = cap // 2
    omitted = len(text) - 2 * half
    return f"{text[:half]}\n...[{omitted} characters truncated]...\n{text[-half:]}"


# ---------------------------------------------------------------------------- git


def git(
    worktree: Path,
    *args: str,
    env: dict | None = None,
    input: str | None = None,
) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(worktree), *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            env={**os.environ, **env} if env else None,
            input=input,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {args[0]}: {exc}") from exc
    if done.returncode != 0:
        raise GitError(f"git {args[0]} failed: {one_line(done.stderr)}")
    return done.stdout


def is_artifact(path: str) -> bool:
    """True for a well-known build artifact (git path, /-separated): any component
    naming an artifact directory, or a file with an artifact suffix or name."""
    parts = path.split("/")
    if any(part.casefold() in ARTIFACT_DIRS for part in parts):
        return True
    name = parts[-1].casefold()
    return name in ARTIFACT_NAMES or name.endswith(ARTIFACT_SUFFIXES)


def untracked_files(worktree: Path) -> list[str]:
    """Untracked, non-ignored paths of the worktree, /-separated and unquoted."""
    out = git(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    return [path for path in out.split("\0") if path]


def snapshot(worktree: Path) -> tuple[str, set[str]]:
    """Return the before tree and the untracked paths present before the round.

    The real index is not modified: the tree that also contains the unstaged
    modifications of tracked files is built in a temporary index outside the
    worktree (GIT_INDEX_FILE). Creates no commit and no ref.
    """
    base = git(worktree, "write-tree").strip()
    untracked_before = set(untracked_files(worktree))
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git(worktree, "read-tree", base, env=env)
        git(worktree, "add", "-u", env=env)
        before = git(worktree, "write-tree", env=env).strip()
    return before, untracked_before


def stage_round(worktree: Path, untracked_before: set[str], touched: set[str]) -> str:
    """Stage what the round produced and return the after tree id.

    Stages tracked modifications and deletions, plus untracked files that are
    new this round or were written with write_file/edit_file, unless they are
    build artifacts; a nested repository (listed as `dir/`) is never staged.
    Pre-existing untracked files and untracked artifacts are left alone.
    Creates no commit and no ref.
    """
    git(worktree, "add", "-u")
    stage = [
        path
        for path in untracked_files(worktree)
        if not path.endswith("/")
        and not is_artifact(path)
        and (path not in untracked_before or path in touched)
    ]
    if stage:
        git(
            worktree,
            "--literal-pathspecs",
            "add",
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
            input="\0".join(stage) + "\0",
        )
    return git(worktree, "write-tree").strip()


def changed_files(worktree: Path, before: str, after: str) -> list[dict]:
    out = git(worktree, "diff", "--name-status", "--no-renames", "-z", before, after)
    tokens = out.split("\0")
    files = []
    for i in range(0, len(tokens) - 1, 2):
        files.append({"path": tokens[i + 1], "status": tokens[i][:1]})
    return files


# ---------------------------------------------------------------------------- preconditions


class Context:
    def __init__(self):
        self.task_dir: Path
        self.repo_root: Path
        self.worktree: Path
        self.round: int
        self.profile_name: str
        self.profiles: dict
        self.files: dict
        self.heartbeat: float
        self.verbose: bool
        self.progress_path: Path


def parse_args(argv):
    parser = Parser(
        description="Run one task-flow implementer round on a non-Claude model"
    )
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--round", required=True, type=int)
    parser.add_argument("--profile")
    parser.add_argument(
        "--heartbeat",
        type=float,
        default=30.0,
        help="seconds between heartbeat lines while a request or command runs "
        "(default 30; 0 disables)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="also log the model's text content of each response",
    )
    args = parser.parse_args(argv)
    if args.heartbeat < 0:
        raise Refusal(f"--heartbeat must be 0 or more seconds, got {args.heartbeat:g}")
    return args


def approved(status_text: str) -> bool:
    for line in status_text.splitlines():
        match = re.match(r"-\s*plan-approved:\s*(.*)", line)
        if match:
            return match.group(1).strip().lower().startswith("yes")
    return False


def prepare(args) -> Context:
    ctx = Context()
    ctx.task_dir = Path(args.task_dir).resolve()
    if not ctx.task_dir.is_dir():
        raise Refusal(f"task dir not found: {ctx.task_dir}")
    if ctx.task_dir.parent.name != ".work":
        raise Refusal(f"task dir must be inside a .work folder: {ctx.task_dir}")
    ctx.repo_root = ctx.task_dir.parent.parent
    ctx.round = args.round
    if ctx.round < 1:
        raise Refusal("round must be 1 or higher")

    needed = ["plan.md", "status.md", "checks-0.md" if ctx.round == 1 else None]
    if ctx.round > 1:
        needed.append(f"review-{ctx.round - 1}.md")
    for name in filter(None, needed):
        if not (ctx.task_dir / name).is_file():
            raise Refusal(f"{name} is missing in {ctx.task_dir}")
    ctx.files = {}
    names = ["plan.md", "status.md", "checks-0.md"]
    if ctx.round > 1:
        names.append(f"review-{ctx.round - 1}.md")
    for name in names:
        path = ctx.task_dir / name
        if path.is_file():
            ctx.files[path] = path.read_text(encoding="utf-8")
    if not approved(ctx.files[ctx.task_dir / "status.md"]):
        raise Refusal("status.md does not say plan-approved: yes")

    if args.worktree == "none":
        ctx.worktree = ctx.repo_root
    else:
        ctx.worktree = Path(args.worktree).resolve()
    if not ctx.worktree.is_dir():
        raise Refusal(f"worktree not found: {ctx.worktree}")
    try:
        inside = git(ctx.worktree, "rev-parse", "--is-inside-work-tree").strip()
    except GitError as exc:
        raise Refusal(f"worktree is not a git work tree: {exc}") from exc
    if inside != "true":
        raise Refusal(f"worktree is not a git work tree: {ctx.worktree}")

    try:
        ctx.profiles, roles = models.load(ctx.repo_root)
    except models.ConfigError as exc:
        raise Refusal(f"invalid configuration: {exc}") from exc
    name = args.profile or roles.get("implementer")
    if not name:
        raise Refusal("no --profile given and [roles] implementer is not set")
    if name not in ctx.profiles:
        raise Refusal(f"no profile named {name!r} in models.toml")
    profile = ctx.profiles[name]
    if not profile["enabled"]:
        raise Refusal(f"profile {name!r} is disabled (enabled = false)")
    if profile["api_key_env"] and not models.api_key(profile):
        raise Refusal(
            f"profile {name!r}: environment variable {profile['api_key_env']} is not set"
        )
    ctx.profile_name = name
    ctx.heartbeat = args.heartbeat
    ctx.verbose = args.verbose
    ctx.progress_path = ctx.task_dir / f"impl-{ctx.round}.progress.jsonl"
    return ctx


# ---------------------------------------------------------------------------- prompt


def implementer_body() -> str:
    text = (PLUGIN_ROOT / "agents" / "implementer.md").read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[text.find("\n", end + 1) + 1 :]
    return text.strip().replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN_ROOT.as_posix())


def plan_profiles(plan_text: str) -> list[str]:
    names = []
    inside = False
    for line in plan_text.splitlines():
        if line.startswith("## "):
            inside = line.strip().lower() == "## profiles"
            continue
        if inside:
            match = re.match(r"\s*(?:[-*]\s*)?`?([A-Za-z][\w-]*)`?\s*:", line)
            if match and match.group(1) not in names:
                names.append(match.group(1))
    return names


def conventions(profile: str) -> str | None:
    path = PLUGIN_ROOT / "skills" / f"profile-{profile}" / "SKILL.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^## Conventions\s*$", text, re.MULTILINE)
    if not match:
        return None
    rest = text[match.start() :]
    nxt = re.search(r"^## (?!Conventions)", rest, re.MULTILINE)
    return (rest[: nxt.start()] if nxt else rest).strip()


def build_system(plan_text: str) -> str:
    parts = [implementer_body()]
    for name in plan_profiles(plan_text):
        section = conventions(name)
        if section is None:
            log(
                f"plan.md names profile {name!r}, which has no Conventions here; ignored"
            )
            continue
        parts.append(f"# Profile `{name}`\n\n{section}")
    parts.append(ADAPTER.strip())
    return "\n\n".join(parts)


def build_user(ctx: Context) -> str:
    skill = PLUGIN_ROOT / "skills" / "workflow" / "SKILL.md"
    sections = [
        f"Task directory: {ctx.task_dir}",
        f"Repository root: {ctx.repo_root}",
        f"Worktree: {ctx.worktree}",
        f"Round: {ctx.round}",
        "The files below are included for you; your file tools only reach the worktree.",
    ]
    ordered = [(skill, skill.read_text(encoding="utf-8"))] + list(ctx.files.items())
    for path, text in ordered:
        sections.append(f'<file path="{path}">\n{text}\n</file>')
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------- tools


def child_env() -> dict:
    env = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if upper in CHILD_ENV_EXACT or upper.startswith(CHILD_ENV_PREFIXES):
            env[name] = value
    return env


class Tools:
    def __init__(
        self,
        worktree: Path,
        command_timeout: float,
        touched: set[str],
        heartbeat: float = 0.0,
        on_command=None,
    ):
        self.root = worktree.resolve()
        self.command_timeout = command_timeout
        self.touched = touched
        self.heartbeat = heartbeat
        self.on_command = on_command
        self.turn = 0
        self.last_command: float | None = None

    # path handling
    def resolve(self, raw, write=False) -> Path:
        if not isinstance(raw, str) or not raw:
            raise ToolError("error: path must be a non-empty string")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(self.root)
        except ValueError:
            raise ToolError(f"denied: {raw} is outside the worktree") from None

        def hidden(parts):
            return {p.rstrip(". ").casefold() for p in parts} & {".git", ".work"}

        raw_parts = () if Path(raw).is_absolute() else Path(raw).parts
        if write and (hidden(relative.parts) or hidden(raw_parts)):
            raise ToolError(
                f"denied: writing under .git or .work is not allowed ({raw})"
            )
        return resolved

    def inside(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root)
        except ValueError:
            return False
        return True

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    @staticmethod
    def text_arg(args: dict, name: str) -> str:
        value = args.get(name)
        if not isinstance(value, str):
            raise ToolError(f"error: {name} must be a string")
        return value

    # tools
    def read_file(self, args):
        path = self.resolve(args.get("path"))
        offset = args.get("offset", 1)
        limit = args.get("limit", READ_DEFAULT_LIMIT)
        if (
            not isinstance(offset, int)
            or not isinstance(limit, int)
            or offset < 1
            or limit < 1
        ):
            raise ToolError("error: offset and limit must be positive integers")
        try:
            with open(path, encoding="utf-8", newline="") as handle:
                lines = handle.read().splitlines(keepends=True)
        except FileNotFoundError:
            raise ToolError(f"error: {args['path']} does not exist") from None
        except IsADirectoryError:
            raise ToolError(f"error: {args['path']} is a directory") from None
        except UnicodeDecodeError:
            raise ToolError(f"error: {args['path']} is not UTF-8 text") from None
        return "".join(lines[offset - 1 : offset - 1 + limit])

    def write_file(self, args):
        path = self.resolve(args.get("path"), write=True)
        content = self.text_arg(args, "content")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        self.touched.add(self.rel(path))
        return f"wrote {len(content)} characters to {self.rel(path)}"

    def edit_file(self, args):
        path = self.resolve(args.get("path"), write=True)
        old = self.text_arg(args, "old")
        new = self.text_arg(args, "new")
        if not old:
            raise ToolError("error: old must not be empty")
        try:
            with open(path, encoding="utf-8", newline="") as handle:
                text = handle.read()
        except FileNotFoundError:
            raise ToolError(f"error: {args['path']} does not exist") from None
        except UnicodeDecodeError:
            raise ToolError(f"error: {args['path']} is not UTF-8 text") from None
        count = text.count(old)
        if count != 1:
            raise ToolError(
                f"error: old text occurs {count} times in {args['path']}; "
                "it must occur exactly once (add context to make it unique)"
            )
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text.replace(old, new, 1))
        self.touched.add(self.rel(path))
        return f"edited {self.rel(path)}"

    def list_files(self, args):
        base = self.resolve(args.get("path", "."))
        pattern = args.get("pattern") or "**/*"
        if not isinstance(pattern, str):
            raise ToolError("error: pattern must be a string")
        try:
            found = []
            for item in base.glob(pattern):
                if not item.is_file() or not self.inside(item):
                    continue
                rel = self.rel(item)
                if ".git" in rel.split("/"):
                    continue
                found.append(rel)
        except (NotImplementedError, ValueError, OSError) as exc:
            raise ToolError(f"error: bad pattern: {exc}") from None
        found.sort()
        extra = len(found) - LIST_MAX_ENTRIES
        shown = found[:LIST_MAX_ENTRIES]
        if extra > 0:
            shown.append(f"...[{extra} more]")
        return "\n".join(shown) or "(no files)"

    def grep(self, args):
        try:
            regex = re.compile(self.text_arg(args, "pattern"))
        except re.error as exc:
            raise ToolError(f"error: bad regular expression: {exc}") from None
        base = self.resolve(args.get("path", "."))
        files = (
            [base]
            if base.is_file()
            else sorted(p for p in base.rglob("*") if p.is_file())
        )
        matches = []
        for item in files:
            if not self.inside(item):
                continue
            rel = self.rel(item)
            if ".git" in rel.split("/"):
                continue
            try:
                text = item.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{rel}:{number}:{line}")
                    if len(matches) >= GREP_MAX_MATCHES:
                        matches.append(f"...[stopped at {GREP_MAX_MATCHES} matches]")
                        return "\n".join(matches)
        return "\n".join(matches) or "(no matches)"

    def find_program(self, token: str, env: dict) -> str | None:
        """Resolve a program token; relative paths are relative to the worktree."""
        if os.path.isabs(token) or "/" in token or "\\" in token:
            base = Path(token) if os.path.isabs(token) else self.root / token
            candidates = [base]
            if os.name == "nt" and not base.suffix:
                exts = env.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
                candidates += [base.with_name(base.name + e) for e in exts if e]
            for candidate in candidates:
                if candidate.is_file():
                    return str(candidate)
            return None
        return shutil.which(token, path=env.get("PATH"))

    def run_command(self, args):
        command = self.text_arg(args, "command").strip()
        if not command:
            raise ToolError("error: command is empty")
        segments = re.split(r"[;&|\n]+", command)
        if any(
            LEAVES_MACHINE.search(s) and not CLIENT_DRY_RUN.search(s) for s in segments
        ):
            raise ToolError(
                "denied: pushing, opening PRs and deploying are not allowed; "
                "nothing leaves the machine until /task-finish"
            )
        if COMMITS.search(command):
            raise ToolError(
                "denied: do not commit; leave changes in the worktree, the script stages them"
            )
        env = child_env()
        try:
            if os.name == "nt":
                stripped = command
                if stripped.startswith('"') and '"' in stripped[1:]:
                    end = stripped.index('"', 1)
                    first, rest = stripped[1:end], stripped[end + 1 :]
                else:
                    parts = stripped.split(None, 1)
                    first = parts[0]
                    rest = stripped[len(first) :]
                found = self.find_program(first, env)
                if not found:
                    raise ToolError(f"error: program not found: {first}")
                if found.lower().endswith((".cmd", ".bat")):
                    raise ToolError(
                        f"error: {first} is a .cmd/.bat script, which cannot be started "
                        "without a shell"
                    )
                argv = subprocess.list2cmdline([found]) + rest
            else:
                argv = shlex.split(command)
                found = self.find_program(argv[0], env)
                if not found:
                    raise ToolError(f"error: program not found: {argv[0]}")
                argv[0] = found
        except ValueError as exc:
            raise ToolError(f"error: cannot parse command: {exc}") from None
        try:
            proc = subprocess.Popen(
                argv,
                cwd=self.root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            raise ToolError(f"error: cannot start command: {exc}") from None
        timed_out = False
        started = time.monotonic()
        with Heartbeat(self.heartbeat, f"running {one_line(command, 60)}"):
            try:
                output, _ = proc.communicate(timeout=self.command_timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                try:
                    output, _ = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    output = b""
        self.last_command = time.monotonic() - started
        if self.on_command is not None:
            self.on_command(self.turn, command, self.last_command, timed_out)
        text = output.decode("utf-8", errors="replace")
        head = (
            f"timed out after {self.command_timeout:g} s and was killed"
            if timed_out
            else f"exit code: {proc.returncode}"
        )
        return f"{head}\n{text}"

    def execute(self, name: str, args) -> tuple[str, str | None]:
        """Run a tool; returns (result text, blocked reason or None)."""
        if not isinstance(args, dict):
            return "error: arguments must be a JSON object", None
        if name == "blocked":
            reason = args.get("reason")
            return "recorded", (
                reason if isinstance(reason, str) and reason else "unspecified"
            )
        handler = {
            "read_file": self.read_file,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "list_files": self.list_files,
            "grep": self.grep,
            "run_command": self.run_command,
        }.get(name)
        if handler is None:
            return f"error: unknown tool {name!r}", None
        try:
            return truncate(handler(args)), None
        except ToolError as exc:
            return truncate(str(exc)), None
        except Exception as exc:  # noqa: BLE001 - a tool bug must not end the round
            return truncate(f"error: {type(exc).__name__}: {exc}"), None


# ---------------------------------------------------------------------------- model loop


def retry_after(headers) -> float | None:
    value = headers.get("Retry-After") if headers else None
    try:
        return min(float(value), MAX_RETRY_AFTER) if value is not None else None
    except ValueError:
        return None


def is_timeout(exc: BaseException) -> bool:
    """True for a TimeoutError, directly or as the reason of a URLError."""
    if isinstance(exc, TimeoutError):
        return True
    reason = getattr(exc, "reason", None)
    return isinstance(reason, TimeoutError)


def post(
    profile: dict, key: str | None, body: dict, attempts: list | None = None
) -> dict:
    """POST one chat completion; retry retryable failures per the profile.

    When `attempts` is a list, every attempt is recorded in it as
    {"seconds", "outcome", "wait"}: "outcome" is "ok" or the retry reason ("HTTP <code>",
    "timeout", or the `<ExceptionName>: <message>` text), "wait" the backoff sleep that
    followed the attempt (0.0 for the last one). The list is filled also when Unavailable
    or Fatal is raised.
    """
    url = profile["base_url"].rstrip("/") + "/chat/completions"
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    retries = profile["retries"]
    last = "no attempt made"
    for attempt in range(retries + 1):
        delay = None
        started = time.monotonic()
        try:
            request = urllib.request.Request(
                url, data=data, headers=headers, method="POST"
            )
            with urllib.request.urlopen(
                request, timeout=profile["request_timeout_seconds"]
            ) as r:
                payload = json.loads(r.read().decode("utf-8"))
            if attempts is not None:
                attempts.append(
                    {
                        "seconds": time.monotonic() - started,
                        "outcome": "ok",
                        "wait": 0.0,
                    }
                )
            return payload
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", errors="replace")
            code = exc.code
            if code in (408, 429) or code >= 500:
                last = f"HTTP {code}"
                outcome = last
                delay = retry_after(exc.headers)
            else:
                if attempts is not None:
                    attempts.append(
                        {
                            "seconds": time.monotonic() - started,
                            "outcome": f"HTTP {code}",
                            "wait": 0.0,
                        }
                    )
                raise Fatal(f"HTTP {code}: {one_line(detail)}") from None
            exc.close()
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            ConnectionError,
            OSError,
            ValueError,
        ) as exc:
            last = f"{type(exc).__name__}: {one_line(exc, 120)}"
            outcome = "timeout" if is_timeout(exc) else last
        if attempts is not None:
            attempts.append(
                {
                    "seconds": time.monotonic() - started,
                    "outcome": outcome,
                    "wait": 0.0,
                }
            )
        if attempt < retries:
            wait = (
                delay
                if delay is not None
                else profile["retry_base_seconds"] * 2**attempt
            )
            log(f"{last}; retrying in {wait:g} s ({attempt + 1}/{retries})")
            if attempts is not None:
                attempts[-1]["wait"] = wait
            time.sleep(wait)
    raise Unavailable(f"{last} after {retries + 1} attempts")


def as_int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class Session:
    def __init__(self, ctx: Context, started: float):
        self.ctx = ctx
        self.round_started = started
        self.start = ctx.profiles[ctx.profile_name]
        self.system = build_system(ctx.files[ctx.task_dir / "plan.md"])
        self.user = build_user(ctx)
        self.usage: dict[str, dict] = {}
        self.chain: list[dict] = []
        self.turns = 0
        self.active = ctx.profile_name
        self.touched: set[str] = set()
        self.model_seconds = 0.0
        self.retry_wait_seconds = 0.0
        self.command_seconds = 0.0
        self.slowest_request: dict | None = None
        self.slowest_command: dict | None = None
        self.retries: dict[str, list] = {}

    def cost(self) -> float:
        total = 0.0
        for name, tokens in self.usage.items():
            p = self.ctx.profiles[name]
            total += (
                (tokens["input"] - tokens["cached"]) * p["price_input_per_mtok"]
                + tokens["cached"] * p["price_cached_input_per_mtok"]
                + tokens["output"] * p["price_output_per_mtok"]
            ) / 1e6
        return total

    def tokens(self) -> dict:
        total = {"input": 0, "cached": 0, "output": 0}
        for tokens in self.usage.values():
            for field in total:
                total[field] += tokens[field]
        return total

    def settings(self) -> dict:
        p = self.ctx.profiles[self.active]
        return {
            "temperature": p["temperature"],
            "max_output_tokens": p["max_output_tokens"],
            "extra_body": p["extra_body"],
        }

    def body(self, profile: dict, messages: list) -> dict:
        body = {
            "model": profile["model"],
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
        }
        if profile["temperature"] is not None:
            body["temperature"] = profile["temperature"]
        if profile["max_output_tokens"] is not None:
            body["max_tokens"] = profile["max_output_tokens"]
        body.update(profile["extra_body"] or {})
        return body

    def account(self, name: str, usage) -> dict:
        """Add one response's usage to the profile's totals; return this call's tokens."""
        usage = usage if isinstance(usage, dict) else {}
        entry = self.usage.setdefault(name, {"input": 0, "cached": 0, "output": 0})
        prompt = as_int(usage.get("prompt_tokens"))
        details = usage.get("prompt_tokens_details")
        cached = (
            as_int(details.get("cached_tokens")) if isinstance(details, dict) else 0
        )
        turn = {
            "input": prompt,
            "cached": min(cached, prompt),
            "output": as_int(usage.get("completion_tokens")),
        }
        entry["input"] += turn["input"]
        entry["cached"] += turn["cached"]
        entry["output"] += turn["output"]
        return turn

    def note_attempts(self, name: str, attempts: list, turn: int) -> None:
        """Accumulate one post() call's attempts: model time, waits, slowest request,
        and one retry record per failed attempt that was followed by another one."""
        for index, attempt in enumerate(attempts):
            seconds = attempt["seconds"]
            self.model_seconds += seconds
            self.retry_wait_seconds += attempt.get("wait", 0.0)
            if (
                self.slowest_request is None
                or seconds > self.slowest_request["seconds"]
            ):
                self.slowest_request = {
                    "turn": turn,
                    "profile": name,
                    "seconds": round(seconds, 3),
                    "outcome": attempt["outcome"],
                }
            if attempt["outcome"] != "ok" and index + 1 < len(attempts):
                self.retries.setdefault(name, []).append(
                    {
                        "turn": turn,
                        "reason": attempt["outcome"],
                        "attempt_seconds": round(seconds, 3),
                        "wait_seconds": round(attempt.get("wait", 0.0), 3),
                    }
                )

    def record_command(
        self, turn: int, command: str, seconds: float, timed_out: bool
    ) -> None:
        self.command_seconds += seconds
        if self.slowest_command is None or seconds > self.slowest_command["seconds"]:
            self.slowest_command = {
                "turn": turn,
                "command": command[:200],
                "seconds": round(seconds, 3),
                "timed_out": timed_out,
            }

    def write_progress(self, profile: str, record: dict) -> None:
        """Append one turn's progress line; a write error only logs a warning."""
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        try:
            with open(
                self.ctx.progress_path, "a", encoding="utf-8", newline=""
            ) as handle:
                handle.write(line + "\n")
                handle.flush()
        except OSError as exc:
            log(
                f"warning: cannot write {self.ctx.progress_path.name}: "
                f"{one_line(exc, 120)}"
            )

    def run(self) -> tuple[str, str]:
        candidates = [self.ctx.profile_name, *self.start["fallback"]]
        interrupted = None
        for index, name in enumerate(candidates):
            profile = self.ctx.profiles[name]
            if index > 0:
                skip = None
                if not profile["enabled"]:
                    skip = "disabled"
                elif profile["api_key_env"] and not models.api_key(profile):
                    skip = f"{profile['api_key_env']} is not set"
                if skip:
                    self.chain.append(
                        {
                            "profile": name,
                            "outcome": "skipped",
                            "reason": skip,
                            "retries": [],
                        }
                    )
                    log(f"fallback profile {name} skipped: {skip}")
                    continue
            self.active = name
            status, reason = self.run_profile(name, profile, interrupted)
            self.chain.append(
                {
                    "profile": name,
                    "outcome": status,
                    "reason": reason,
                    "retries": self.retries.get(name, []),
                }
            )
            if status != "unavailable":
                return status, reason
            interrupted = (name, reason)
            log(f"profile {name} unavailable ({reason}); trying the next fallback")
        reasons = "; ".join(
            f"{c['profile']}: {c['reason']}" for c in self.chain if c["outcome"] != "ok"
        )
        return "failed", f"all profiles unavailable ({reasons})"

    def run_profile(self, name: str, profile: dict, interrupted) -> tuple[str, str]:
        tools = Tools(
            self.ctx.worktree,
            profile["command_timeout_seconds"],
            self.touched,
            heartbeat=self.ctx.heartbeat,
            on_command=self.record_command,
        )
        user = self.user
        if interrupted:
            user += (
                f"\n\nNote: a previous attempt on profile {interrupted[0]} was interrupted "
                f"({interrupted[1]}). The worktree may already hold partial changes; "
                "inspect it before continuing and do not redo finished work."
            )
        messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": user},
        ]
        key = models.api_key(profile)
        cap = self.start["max_cost_usd"]
        turns = 0
        while True:
            if turns >= profile["max_turns"]:
                return (
                    "failed",
                    f"max_turns ({profile['max_turns']}) exceeded on {name}",
                )
            attempts: list = []
            request_started = time.monotonic()
            request_seconds = 0.0
            turn_tools: list[str] = []
            turn_tokens = {"input": 0, "cached": 0, "output": 0}
            command_before = self.command_seconds
            retries_before = len(self.retries.get(name, []))
            counted = False
            try:
                try:
                    with Heartbeat(self.ctx.heartbeat, f"waiting for {name}"):
                        payload = post(
                            profile, key, self.body(profile, messages), attempts
                        )
                except Unavailable as exc:
                    self.note_attempts(name, attempts, self.turns + 1)
                    return "unavailable", str(exc)
                except Fatal as exc:
                    self.note_attempts(name, attempts, self.turns + 1)
                    return "failed", f"{name}: {exc}"
                request_seconds = time.monotonic() - request_started
                self.note_attempts(name, attempts, self.turns + 1)
                counted = True
                turns += 1
                self.turns += 1
                tools.turn = self.turns
                turn_tokens = self.account(
                    name, payload.get("usage") if isinstance(payload, dict) else None
                )
                if cap is not None and self.cost() > cap:
                    return (
                        "failed",
                        f"max_cost_usd ({cap:g}) exceeded: ${self.cost():.4f}",
                    )
                if (
                    profile["max_context"] is not None
                    and turn_tokens["input"] > profile["max_context"]
                ):
                    return (
                        "failed",
                        (
                            f"max_context ({profile['max_context']}) exceeded: "
                            f"{turn_tokens['input']} tokens"
                        ),
                    )
                try:
                    message = payload["choices"][0]["message"]
                    calls = message.get("tool_calls") or []
                    content = message.get("content")
                except (KeyError, IndexError, TypeError, AttributeError):
                    return "failed", f"{name}: malformed response from the model"
                if self.ctx.verbose and isinstance(content, str) and content.strip():
                    log(f"turn {self.turns} text: {one_line(content, 500)}")
                prefix = (
                    f"turn {self.turns} ({request_seconds:.1f} s, "
                    f"{fmt_tokens(turn_tokens['input'])} in / "
                    f"{fmt_tokens(turn_tokens['output'])} out, "
                    f"${self.cost():.4f} total)"
                )
                if not calls:
                    log(f"{prefix}: final message")
                    return "ok", "finished"
                assistant = {"role": "assistant", "content": content, "tool_calls": []}
                results = []
                for call in calls:
                    try:
                        call_id = str(call["id"])
                        function = call["function"]
                        tool_name = function["name"]
                        raw = function.get("arguments") or "{}"
                    except (KeyError, TypeError):
                        return "failed", f"{name}: malformed tool call from the model"
                    turn_tools.append(tool_name)
                    assistant["tool_calls"].append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": raw
                                if isinstance(raw, str)
                                else json.dumps(raw),
                            },
                        }
                    )
                    try:
                        arguments = json.loads(raw) if isinstance(raw, str) else raw
                    except ValueError:
                        result, blocked = "error: arguments are not valid JSON", None
                    else:
                        tools.last_command = None
                        result, blocked = tools.execute(tool_name, arguments)
                    if tool_name == "run_command" and tools.last_command is not None:
                        log(
                            f"{prefix}: run_command [{tools.last_command:.1f} s] -> "
                            f"{one_line(result, 80)}"
                        )
                    else:
                        log(f"{prefix}: {tool_name} -> {one_line(result, 80)}")
                    if blocked is not None:
                        return "blocked", blocked
                    results.append(
                        {"role": "tool", "tool_call_id": call_id, "content": result}
                    )
                messages.append(assistant)
                messages.extend(results)
            finally:
                if counted:
                    self.write_progress(
                        name,
                        {
                            "turn": self.turns,
                            "profile": name,
                            "tools": turn_tools,
                            "request_seconds": round(request_seconds, 3),
                            "retries": len(self.retries.get(name, [])) - retries_before,
                            "command_seconds": round(
                                self.command_seconds - command_before, 3
                            ),
                            "tokens": turn_tokens,
                            "cost_usd": round(self.cost(), 6),
                            "elapsed_seconds": round(
                                time.monotonic() - self.round_started, 3
                            ),
                        },
                    )


# ---------------------------------------------------------------------------- results


def append_log(task_dir: Path, lines: list[str]) -> None:
    path = task_dir / "status.md"
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    prefix = ""
    if text and not text.endswith("\n"):
        prefix = "\n"
    if not re.search(r"^## Log\s*$", text, re.MULTILINE):
        prefix += "## Log\n"
    with open(path, "a", encoding="utf-8", newline="") as handle:
        handle.write(prefix + "".join(f"{line}\n" for line in lines))


def write_metrics(
    ctx: Context, session: Session, status, reason, files, started
) -> None:
    profile = ctx.profiles[session.active]
    data = {
        "profile": session.active,
        "model": profile["model"],
        "turns": session.turns,
        "tokens": session.tokens(),
        "cost_usd": round(session.cost(), 6),
        "duration_seconds": round(time.monotonic() - started, 3),
        "status": status,
        "reason": reason,
        "fallback_chain": session.chain,
        "settings": session.settings(),
        "files_changed": files,
        "model_seconds": round(session.model_seconds, 3),
        "retry_wait_seconds": round(session.retry_wait_seconds, 3),
        "command_seconds": round(session.command_seconds, 3),
        "slowest_request": session.slowest_request,
        "slowest_command": session.slowest_command,
    }
    path = ctx.task_dir / f"impl-{ctx.round}.json"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    try:
        ctx = prepare(parse_args(argv))
        before, untracked_before = snapshot(ctx.worktree)
    except Refusal as exc:
        print(f"refused: {one_line(exc)}")
        return 2
    except GitError as exc:
        print(f"refused: {one_line(exc)}")
        return 2

    started = time.monotonic()
    try:
        with open(ctx.progress_path, "w", encoding="utf-8", newline="") as handle:
            handle.write("")
    except OSError as exc:
        log(f"warning: cannot create {ctx.progress_path.name}: {one_line(exc, 120)}")
    session = Session(ctx, started)
    log(f"round {ctx.round}, profile {ctx.profile_name}, worktree {ctx.worktree}")
    try:
        status, reason = session.run()
    except Exception as exc:  # noqa: BLE001 - report, do not lose the metrics
        status, reason = "failed", f"unexpected error: {type(exc).__name__}: {exc}"

    files: list[dict] = []
    try:
        after = stage_round(ctx.worktree, untracked_before, session.touched)
        files = changed_files(ctx.worktree, before, after)
    except GitError as exc:
        if status == "ok":
            status, reason = "failed", f"could not stage the result: {exc}"

    if status == "ok":
        lines = [
            f"round {ctx.round} implementer: {f['path']} ({f['status']})" for f in files
        ]
        append_log(
            ctx.task_dir, lines or [f"round {ctx.round} implementer: no files changed"]
        )
    elif status == "blocked":
        append_log(ctx.task_dir, [f"BLOCKED: {one_line(reason, 2000)}"])
    write_metrics(ctx, session, status, reason, files, started)

    profile = ctx.profiles[session.active]
    if status == "ok":
        print(
            f"ok: {len(files)} files changed ({session.active}/{profile['model']}, "
            f"{session.turns} turns, ${session.cost():.4f})"
        )
        return 0
    print(f"{status}: {one_line(reason)}")
    return 3 if status == "blocked" else 1


if __name__ == "__main__":
    sys.exit(main())
