# /// script
# requires-python = ">=3.9"
# ///
"""PostToolUse(Edit|Write) hook: format the Python file just written, then surface what a
linter could not fix automatically.

Findings go back as JSON in hookSpecificOutput.additionalContext. That is the only channel
a PostToolUse hook has to Claude: plain stdout on exit 0 goes to the debug log and is never
seen, and exit 2 is not honoured for this event because the tool has already run.

Prefers habit-hooks (https://github.com/habit-hooks/habit-hooks) when installed and the
project opted in with .habit-hooks/config.toml, because it returns actionable coaching
rather than rule codes. Falls back to plain ruff. Silent no-op when the file is not Python
or no ruff is available.

Python rather than bash so one implementation runs on macOS, Linux and Windows with or
without Git Bash, with no jq and no sed dialect differences; stdlib only, started by
`uv run --script`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT = 25  # per tool run; the hook itself has 60 s


def run(cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def emit(text: str) -> None:
    print(
        json.dumps(
            {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}
        )
    )
    sys.exit(0)


def resolve_ruff(project: Path) -> list[str] | None:
    # A project that pins ruff in its lockfile must be formatted by THAT ruff: formatting
    # rules change between releases, so a global 0.16 reformatting a project pinned to 0.6
    # produces diff churn in files the author did not touch.
    lock = project / "uv.lock"
    uv = shutil.which("uv")
    if uv and lock.is_file():
        try:
            pinned = 'name = "ruff"' in lock.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pinned = False
        if pinned:
            return [uv, "run", "--quiet", "--project", str(project), "ruff"]
    ruff = shutil.which("ruff")
    return [ruff] if ruff else None


def main() -> None:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        file_path = payload["tool_input"]["file_path"]
    except (ValueError, KeyError, TypeError):
        return
    if not isinstance(file_path, str) or not file_path.endswith(".py"):
        return
    target = Path(file_path)
    if not target.is_file():
        return

    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ruff = resolve_ruff(project)
    if ruff is None:
        return

    # Fix first, then format, so the formatter sees the final shape.
    run([*ruff, "check", "--fix", str(target)])
    run([*ruff, "format", str(target)])

    sensors, mapper = shutil.which("habit-sensors"), shutil.which("habit-mapper")
    if sensors and mapper and (project / ".habit-hooks" / "config.toml").is_file():
        found = run([sensors, "--files", str(target)])
        if found and found.stdout:
            coached = run([mapper], stdin=found.stdout)
            if coached and coached.stdout.strip():
                emit(f"habit-hooks findings in {target} — fix these before moving on:\n{coached.stdout}")

    remaining = run([*ruff, "check", str(target)])
    if remaining and remaining.returncode != 0:
        emit(f"ruff findings in {target}:\n{remaining.stdout}{remaining.stderr}")


if __name__ == "__main__":
    main()
