# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Run a plan's Verification commands without a model and write checks-<n>.md.

This is the checker when `[roles] checker = "script"` in models.toml. It reads the first
fenced block under `## Verification commands` of a task's plan.md (the shared parser is
`autopilot.verification_commands`), runs each line from the worktree (or the repository
root for `--worktree none`) and writes `.work/<id>/checks-<n>.md` in the contract's
format. No model is involved; it never touches status.md and writes nothing else.

Command lines (one program per line, no shell):

- Whitespace outside quotes separates arguments. `'...'` and `"..."` group their content
  verbatim and join with adjacent unquoted parts (`--x="a b"` -> `--x=a b`). There are
  no escapes: a backslash is an ordinary character, so Windows paths work unchanged. An
  unclosed quote is refused.
- Outside quotes, any of | & ; < > ( ) $ * ? ` and a backtick is refused, as is a first
  word of the form NAME=value: those need a shell. Inside quotes they are ordinary
  characters, so `git grep -nE "a|b"` is accepted. `split_command` raises ValueError with
  the reason.

Order of decisions per command (first match wins): `rules.check_command(line,
"checker")` -> ERROR `denied: <reason>`; `split_command` -> ERROR `refused: <reason>`;
program not found (relative paths resolve against the run directory, on Windows also
via PATHEXT; otherwise `shutil.which`) -> Not run; on Windows a `.cmd`/`.bat` program
runs through `launch.cmd_command_line` (a cmd metacharacter -> ERROR `refused:`);
otherwise run it. Exit 0 -> PASS, non-zero -> FAIL, timeout -> ERROR (Exit `-`),
OSError at start -> ERROR `cannot start:`.

Running: the environment is inherited unchanged, stdin is DEVNULL, stdout and stderr go
to one temporary file (not a pipe, so a surviving grandchild cannot block the read).
POSIX starts a new session and kills the process group on timeout; Windows runs
taskkill /F /T and then kills the process, waiting at most 10 s more. Output is decoded
as UTF-8 with replacement, CRLF normalized, ANSI escapes stripped.

Timeout per command: `--timeout-seconds`, else `[gates] command_timeout_seconds` from
models.toml, else 600.

Excerpt (FAIL and timeout ERROR): from the cleaned output, in output order and without
duplicates, every line matching a fixed pattern set (unittest/pytest summaries, exception
and assertion lines, ruff diagnostics, compiler-style errors, `error:`/`fatal:` tool
lines); at most 30, plus one `[... k more matching lines not shown]` marker. When nothing
matched: the last 30 non-blank lines with an `[... k earlier lines not shown]` marker
when truncated. A timeout excerpt starts with `timed out after <s> s; the process tree
was killed`.

checks-<n>.md: heading, a context line, the table (rows in plan order; Not-run commands
have no row), `## Failures` with one fenced excerpt or reason per FAIL/ERROR, `## Not
run` with the numbered not-run commands and a fixed profile-defaults line at the end.

CLI: gates.py --task-dir <dir> --worktree <path|none> --round <n> [--timeout-seconds S].
Exit 0 when checks-<n>.md was written (stdout is exactly one summary line), whatever the
results. Exit 2 with the reason on stderr, nothing on stdout and no file written for a
missing task dir or plan.md, no or empty Verification commands block, a --worktree that
is not a directory, a task dir whose parent is not `.work`, a negative round, an invalid
timeout or invalid models.toml.

Stdlib only, started by `uv run --script` or imported as a sibling module by the tests,
so it runs on macOS, Linux and Windows.
"""

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import autopilot
import launch
import models
import rules

CHECKER_ROLE = "checker"
EXCERPT_LIMIT = 30
PROFILE_DEFAULTS_LINE = (
    "- Profile defaults not listed in plan.md's Verification commands: not run; "
    "gates.py runs only the plan's commands."
)

SHELL_CHARS = "|&;<>()$`*?"
ENV_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
EXCERPT_PATTERNS = [
    re.compile(r"^(FAILED|ERROR)\b"),
    re.compile(r"^(FAIL|ERROR): "),
    re.compile(r"^\s*[A-Za-z_][\w.]*(Error|Exception|Exit|Failure)\b:?"),
    re.compile(r"^[A-Z]{1,4}\d{3,4}\b"),
    re.compile(r"^\s*--> \S+:\d+:\d+"),
    re.compile(r"^\S+:\d+:\d+: [A-Z]{1,4}\d{3,4}\b"),
    re.compile(r"^Would reformat: "),
    re.compile(r"^Found \d+ errors?"),
    re.compile(r"^\S+:\d+(:\d+)?: (error|warning|fatal)\b"),
    re.compile(r"^(error|fatal): "),
]


class Refusal(Exception):
    """A precondition failure: exit 2, reason on stderr, nothing written."""


def split_command(line: str) -> list[str]:
    """Split one plan line into one program and its arguments (no shell, no escapes).

    Raises ValueError whose message names the character or the problem.
    """
    args: list[str] = []
    current = ""
    pending = False  # current has at least one (possibly empty) quoted part
    quote: str | None = None
    for char in line:
        if quote is not None:
            if char == quote:
                quote = None
            else:
                current += char
            continue
        if char in "'\"":
            quote = char
            pending = True
            continue
        if char.isspace():
            if current or pending:
                args.append(current)
                current = ""
                pending = False
            continue
        if char in SHELL_CHARS:
            raise ValueError(
                f"the character {char!r} needs a shell; gates.py runs one program "
                "per line, without a shell"
            )
        current += char
    if quote is not None:
        raise ValueError(
            f"the {quote} quote is not closed; gates.py runs one program per line, "
            "without a shell"
        )
    if current or pending:
        args.append(current)
    if not args:
        raise ValueError("the line is empty; gates.py runs one program per line")
    if ENV_PREFIX.match(args[0]):
        raise ValueError(
            f"the first word {args[0]!r} is an environment assignment; gates.py runs "
            "one program per line, without a shell"
        )
    return args


def find_program(word: str, run_dir: Path, env: dict) -> str | None:
    """Resolve a program word; a word with a path separator resolves against run_dir."""
    if os.path.isabs(word) or "/" in word or "\\" in word:
        base = Path(word) if os.path.isabs(word) else run_dir / word
        candidates = [base]
        if os.name == "nt" and not base.suffix:
            for ext in env.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";"):
                if ext:
                    candidates.append(base.with_name(base.name + ext))
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return None
    return shutil.which(word, path=env.get("PATH"))


def clean_output(text: str) -> str:
    """Normalize CRLF and strip ANSI escapes."""
    return ANSI_ESCAPE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def excerpt(output: str, timed_out: bool, timeout: float) -> list[str]:
    """The failure excerpt lines: matched lines, else the last non-blank ones.

    At most EXCERPT_LIMIT lines plus one marker; a timeout adds a leading line, so the
    result never exceeds EXCERPT_LIMIT + 1 lines.
    """
    matched: list[str] = []
    seen: set[str] = set()
    for line in output.split("\n"):
        if line not in seen and any(p.search(line) for p in EXCERPT_PATTERNS):
            seen.add(line)
            matched.append(line)
    if matched:
        body = matched[:EXCERPT_LIMIT]
        if len(matched) > EXCERPT_LIMIT:
            body.append(
                f"[... {len(matched) - EXCERPT_LIMIT} more matching lines not shown]"
            )
    else:
        non_blank = [line for line in output.split("\n") if line.strip()]
        if len(non_blank) > EXCERPT_LIMIT:
            body = [f"[... {len(non_blank) - EXCERPT_LIMIT} earlier lines not shown]"]
            body += non_blank[-EXCERPT_LIMIT:]
        else:
            body = non_blank
    if timed_out:
        if len(body) >= EXCERPT_LIMIT + 1:
            body = body[:EXCERPT_LIMIT]  # keep room for the leading line, drop marker
        body = [f"timed out after {timeout:g} s; the process tree was killed"] + body
    return body


def code_span(text: str) -> str:
    """A backtick code span whose delimiter is longer than any backtick run in text."""
    runs = [len(m.group(0)) for m in re.finditer(r"`+", text)]
    delimiter = "`" * (max(runs, default=0) + 1)
    body = text
    if body.startswith("`") or body.endswith("`"):
        body = f" {body} "
    return delimiter + body + delimiter


def fence_block(lines: list[str]) -> str:
    """A fenced block whose fence is longer than any backtick run in the body."""
    body = "\n".join(lines)
    runs = [len(m.group(0)) for m in re.finditer(r"`+", body)]
    delimiter = "`" * max([3] + [run + 1 for run in runs])
    return f"{delimiter}\n{body}\n{delimiter}"


def render(round_n: int, run_dir: Path, timeout: float, results: list[dict]) -> str:
    """The checks-<n>.md content (D6): heading, context line, table, failures, not run."""
    lines = [
        f"# Checks: round {round_n}",
        "",
        (
            f"Run by scripts/gates.py from {code_span(str(run_dir))}, plan order, "
            f"no model. Timeout {timeout:g} s per command."
        ),
        "",
        "| # | Command | Result | Exit | Duration |",
        "| --- | --- | --- | --- | --- |",
    ]
    for result in results:
        if result["status"] == "NOTRUN":
            continue
        exit_cell = "-" if result["status"] == "ERROR" else str(result["exit"])
        duration = result["duration"]
        duration_cell = "<1s" if duration < 1 else f"{duration:.0f}s"
        command_cell = code_span(result["command"].replace("|", "\\|"))
        lines.append(
            f"| {result['number']} | {command_cell} | {result['status']} | "
            f"{exit_cell} | {duration_cell} |"
        )
    lines.append("")
    lines.append("## Failures")
    failures = [r for r in results if r["status"] in ("FAIL", "ERROR")]
    if failures:
        for result in failures:
            lines.append(f"### {result['number']}. {code_span(result['command'])}")
            lines.append(fence_block(result["detail"]))
            lines.append("")
    else:
        lines.append("none")
        lines.append("")
    lines.append("## Not run")
    for result in results:
        if result["status"] == "NOTRUN":
            lines.append(
                f"- {result['number']}. {code_span(result['command'])} — "
                f"{result['not_run_reason']}"
            )
    lines.append(PROFILE_DEFAULTS_LINE)
    return "\n".join(lines) + "\n"


def kill_tree(proc: subprocess.Popen, env: dict) -> None:
    """Kill a timed-out process and its tree (the process group on POSIX)."""
    if os.name == "nt":
        root = env.get("SYSTEMROOT") or env.get("SystemRoot") or r"C:\Windows"
        taskkill = os.path.join(root, "System32", "taskkill.exe")
        subprocess.run(
            [taskkill, "/F", "/T", "/PID", str(proc.pid)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        try:
            proc.kill()
        except OSError:
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass


def run_line(number: int, line: str, run_dir: Path, env: dict, timeout: float) -> dict:
    """Decide and run one plan command line; the result record for render()."""
    result = {
        "number": number,
        "command": line,
        "status": "ERROR",
        "exit": None,
        "duration": 0.0,
        "detail": [],
        "not_run_reason": None,
    }
    reason = rules.check_command(line, CHECKER_ROLE)
    if reason is not None:
        result["detail"] = [f"denied: {reason}"]
        return result
    try:
        args = split_command(line)
    except ValueError as exc:
        result["detail"] = [f"refused: {exc}"]
        return result
    found = find_program(args[0], run_dir, env)
    if found is None:
        result["status"] = "NOTRUN"
        result["not_run_reason"] = f"program not found: {args[0]}"
        return result
    if os.name == "nt" and launch.is_cmd_script(found):
        rest = "" if len(args) == 1 else " " + subprocess.list2cmdline(args[1:])
        try:
            popen_arg = launch.cmd_command_line(found, rest, launch.cmd_exe(env))
        except ValueError as exc:
            result["detail"] = [f"refused: {exc}"]
            return result
    else:
        popen_arg = [found, *args[1:]]
    started = time.monotonic()
    with tempfile.TemporaryFile() as output_file:
        kwargs = {
            "cwd": run_dir,
            "stdin": subprocess.DEVNULL,
            "stdout": output_file,
            "stderr": subprocess.STDOUT,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(popen_arg, **kwargs)
        except OSError as exc:
            result["detail"] = [f"cannot start: {exc}"]
            return result
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(proc, env)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
                proc.wait()
        result["duration"] = time.monotonic() - started
        output_file.seek(0)
        output = clean_output(output_file.read().decode("utf-8", errors="replace"))
    if timed_out:
        result["detail"] = excerpt(output, True, timeout)
        return result
    result["exit"] = proc.returncode
    if proc.returncode == 0:
        result["status"] = "PASS"
    else:
        result["status"] = "FAIL"
        result["detail"] = excerpt(output, False, timeout)
    return result


def run(args: argparse.Namespace) -> int:
    if args.round < 0:
        raise Refusal(f"--round must be >= 0, got {args.round}")
    if args.timeout_seconds is not None and args.timeout_seconds <= 0:
        raise Refusal(f"--timeout-seconds must be > 0, got {args.timeout_seconds:g}")
    task_dir = Path(args.task_dir).resolve()
    if not task_dir.is_dir():
        raise Refusal(f"task dir not found: {args.task_dir}")
    if task_dir.parent.name != ".work":
        raise Refusal(f"the task dir's parent must be .work, got {task_dir.parent}")
    plan_path = task_dir / "plan.md"
    if not plan_path.is_file():
        raise Refusal(f"no plan.md in {task_dir}")
    if args.worktree == "none":
        run_dir = task_dir.parent.parent
    else:
        run_dir = Path(args.worktree).resolve()
        if not run_dir.is_dir():
            raise Refusal(f"--worktree is not a directory: {args.worktree}")
    repo_root = task_dir.parent.parent
    try:
        if args.timeout_seconds is not None:
            timeout = args.timeout_seconds
        else:
            timeout = models.gates(repo_root)["command_timeout_seconds"]
    except models.ConfigError as exc:
        raise Refusal(str(exc)) from exc
    try:
        plan = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Refusal(f"cannot read {plan_path}: {exc}") from exc
    commands = autopilot.verification_commands(plan)
    if not commands:
        raise Refusal(
            f"plan.md has no Verification commands block, or it is empty: {plan_path}"
        )
    env = os.environ
    results = [
        run_line(number, line, run_dir, env, timeout)
        for number, line in enumerate(commands, 1)
    ]
    out_path = task_dir / f"checks-{args.round}.md"
    out_path.write_text(
        render(args.round, run_dir, timeout, results), encoding="utf-8", newline="\n"
    )
    counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "NOTRUN": 0}
    for result in results:
        counts[result["status"]] += 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(
        f"checks-{args.round}: {counts['PASS']} PASS, {counts['FAIL']} FAIL, "
        f"{counts['ERROR']} ERROR, {counts['NOTRUN']} not run -> {out_path}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task-dir", required=True, help="the .work/<id>/ directory")
    parser.add_argument(
        "--worktree", required=True, help="the worktree path, or none for the repo root"
    )
    parser.add_argument("--round", required=True, type=int, help="round number, >= 0")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="per-command timeout; else [gates] command_timeout_seconds, else 600",
    )
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Refusal as exc:
        print(f"gates.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
