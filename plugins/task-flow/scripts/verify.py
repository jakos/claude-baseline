# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Run this repository's shared task-flow check list as one gate (the CI entry point).

Usage (from the repository root; stdlib only, started by `uv run --script`):

  verify.py [--root DIR] [--config PATH] [--target NAME] [--timeout-seconds S]

CI (.github/workflows/ci.yml) runs exactly this script on a Linux and a Windows
GitHub-hosted runner, so the hosted jobs execute the same commands as the local gates
and `/task-flow:task-release verify`. The list is read from this repository's own
configuration — there is no third copy of it:

1. `[targets.task-flow].checks` of `.claude/task-flow.release.toml`, parsed strictly
   with tomllib through `release.load_config`; each entry is an argument list, so there
   is no shell quoting on any OS.
2. The first fenced block under `## Verification commands` of `.claude/task-flow.md`,
   parsed with the shared `autopilot.verification_commands` and split per line with
   `gates.split_command`.

Before any check runs, the two lists must agree entry by entry, in order, as argument
lists, and every discovered `plugins/task-flow/tests/test_*.py` must be listed exactly
once: a missing test (a new file omitted from both lists), a duplicate entry and a
stale entry (listed but not present) are refused, and so is any entry that would run
this script itself. A refusal prints every problem on stderr and exits 2 without
running anything.

Each check then runs from `--root` (default `.`) with its argument list: the program is
resolved like `gates.py` resolves plan lines (a word with a path separator against the
run directory, on Windows also via PATHEXT, else `shutil.which`; on Windows a
`.cmd`/`.bat` shim starts through `launch.cmd_command_line` and a cmd metacharacter is
refused), `rules.check_command` with the `checker` role is applied first, stdin is
closed, stdout and stderr go to one temporary file and the process tree is killed after
`--timeout-seconds` (default 600). One line per check is printed — `PASS`, `FAIL`,
`ERROR` or `NOTRUN` plus the command, with the failure detail indented — and a failing
check never stops the run. The exit code is 0 only when every check passed; any
failure, missing tool, launch error or timeout exits 1.

Exit codes: 0 all checks passed; 1 at least one check failed, was denied or refused,
could not start, timed out, or its program was not found; 2 configuration or usage
error (missing or malformed release config or gate block, list disagreement, missing,
duplicate or stale test entries, self-reference).

Stdlib only, so it runs on macOS, Linux and Windows.
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import autopilot
import gates
import launch
import release
import rules

GATE_FILE = (".claude", "task-flow.md")
DEFAULT_TARGET = "task-flow"
TESTS_DIR = "plugins/task-flow/tests"
SELF_PATH = "plugins/task-flow/scripts/verify.py"
CHECKER_ROLE = "checker"
DEFAULT_TIMEOUT_SECONDS = 600.0
DETAIL_LINES = 30


def is_test_entry(arg: str) -> bool:
    """True when `arg` is a `plugins/task-flow/tests/test_*.py` path."""
    rest = arg[len(TESTS_DIR) + 1 :] if arg.startswith(TESTS_DIR + "/") else ""
    return arg.endswith(".py") and rest.startswith("test_") and "/" not in rest


def load_checks(root: Path, config_path: Path | None, target: str) -> list[list[str]]:
    """The target's check list from the release config (strict tomllib parsing)."""
    config = release.load_config(root, config_path)
    _name, tgt = release.pick_target(config, target)
    return [list(check) for check in tgt["checks"]]


def gate_argvs(root: Path) -> list[list[str]]:
    """The project gate block of .claude/task-flow.md as argument lists, in order."""
    path = root.joinpath(*GATE_FILE)
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise release.ConfigError(f"{path}: no project gate file") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise release.ConfigError(f"{path}: cannot read: {exc}") from exc
    lines = autopilot.verification_commands(text)
    if not lines:
        raise release.ConfigError(
            f"{path}: no Verification commands block, or it is empty"
        )
    argvs: list[list[str]] = []
    for line in lines:
        try:
            argvs.append(gates.split_command(line))
        except ValueError as exc:
            raise release.ConfigError(
                f"{path}: refused command line {line!r}: {exc}"
            ) from exc
    return argvs


def parity_problems(checks: list[list[str]], gate: list[list[str]]) -> list[str]:
    """The disagreements between the release list and the gate block, in order."""
    problems: list[str] = []
    gate_name = "/".join(GATE_FILE)
    if len(checks) != len(gate):
        problems.append(
            f"the release list has {len(checks)} entries but {gate_name} has {len(gate)}"
        )
    for number, (check, gate_argv) in enumerate(zip(checks, gate), 1):
        if check != gate_argv:
            problems.append(
                f"entry {number} differs: release {check} vs gate {gate_argv}"
            )
    return problems


def listed_tests(checks: list[list[str]]) -> list[str]:
    """The test_*.py arguments of the check list, in order, at most one per check."""
    listed: list[str] = []
    for check in checks:
        for arg in check:
            if is_test_entry(arg):
                listed.append(arg)
                break
    return listed


def discovered_tests(root: Path) -> list[str]:
    """The repo-relative paths of every plugins/task-flow/tests/test_*.py, sorted."""
    folder = root / TESTS_DIR
    if not folder.is_dir():
        return []
    return sorted(f"{TESTS_DIR}/{path.name}" for path in folder.glob("test_*.py"))


def test_guard_problems(checks: list[list[str]], root: Path) -> list[str]:
    """Missing, duplicate, stale or self-referential test entries; empty when sound."""
    problems: list[str] = []
    for check in checks:
        if any(arg == SELF_PATH or arg.endswith("/" + SELF_PATH) for arg in check):
            problems.append(f"the check list must not run verify.py itself: {check}")
    listed = listed_tests(checks)
    seen: set[str] = set()
    for name in listed:
        if name in seen:
            problems.append(f"duplicate test entry: {name}")
        seen.add(name)
    discovered = discovered_tests(root)
    for name in discovered:
        if name not in seen:
            problems.append(f"discovered but listed in neither list: {name}")
    for name in listed:
        if name not in discovered:
            problems.append(f"listed but not present on disk (stale): {name}")
    return problems


def popen_arg(
    argv: list[str], run_dir: Path, env: dict, windows: bool | None = None
) -> str | list[str] | None:
    """Resolve argv[0] and build the Popen argument for one check.

    Returns None when the program is not found. On Windows a `.cmd`/`.bat` program
    returns the cmd.exe command line built by `launch.cmd_command_line` (raising
    ValueError on a cmd metacharacter, so no argument can reach an interpreted
    character); otherwise the argument list with the resolved program first.
    `windows` overrides the platform check so tests run on every OS.
    """
    found = gates.find_program(argv[0], run_dir, env)
    if found is None:
        return None
    is_nt = os.name == "nt" if windows is None else windows
    if is_nt and launch.is_cmd_script(found):
        rest = "" if len(argv) == 1 else " " + subprocess.list2cmdline(argv[1:])
        return launch.cmd_command_line(found, rest, launch.cmd_exe(env))
    return [found, *argv[1:]]


def run_one(
    argv: list[str],
    run_dir: Path,
    env: dict,
    timeout: float,
    popen=subprocess.Popen,
    windows: bool | None = None,
) -> dict:
    """Run one check and return its result record (status, exit, detail, duration)."""
    result: dict = {
        "command": " ".join(argv),
        "argv": argv,
        "status": "ERROR",
        "exit": None,
        "duration": 0.0,
        "detail": [],
    }
    reason = rules.check_command(result["command"], CHECKER_ROLE)
    if reason is not None:
        result["detail"] = [f"denied: {reason}"]
        return result
    try:
        popen_argument = popen_arg(argv, run_dir, env, windows)
    except ValueError as exc:
        result["detail"] = [f"refused: {exc}"]
        return result
    if popen_argument is None:
        result["status"] = "NOTRUN"
        result["detail"] = [f"program not found: {argv[0]}"]
        return result
    started = time.monotonic()
    with tempfile.TemporaryFile() as output_file:
        kwargs: dict = {
            "cwd": run_dir,
            "stdin": subprocess.DEVNULL,
            "stdout": output_file,
            "stderr": subprocess.STDOUT,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            proc = popen(popen_argument, **kwargs)
        except OSError as exc:
            result["detail"] = [f"cannot start: {exc}"]
            return result
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            gates.kill_tree(proc, env)
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
        output = gates.clean_output(
            output_file.read().decode("utf-8", errors="replace")
        )
    if timed_out:
        result["detail"] = gates.excerpt(output, True, timeout)
        return result
    result["exit"] = proc.returncode
    if proc.returncode == 0:
        result["status"] = "PASS"
    else:
        result["status"] = "FAIL"
        result["detail"] = gates.excerpt(output, False, timeout)
    return result


def report(result: dict) -> None:
    """Print one visible per-check line, with the detail indented below it."""
    line = f"{result['status']} {result['command']}"
    if result["status"] == "FAIL":
        line += f" (exit {result['exit']})"
    print(line)
    for detail in result["detail"][:DETAIL_LINES]:
        print(f"    {detail}")


def run_all(
    checks: list[list[str]],
    run_dir: Path,
    env: dict,
    timeout: float,
    popen=subprocess.Popen,
    windows: bool | None = None,
) -> list[dict]:
    """Run every check in order and report each; a failure never stops the run."""
    results: list[dict] = []
    for check in checks:
        result = run_one(check, run_dir, env, timeout, popen, windows)
        report(result)
        results.append(result)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        default=".",
        help="repository root to run the checks from (default: .)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="release config path (default: <root>/.claude/task-flow.release.toml)",
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help=f"release target name (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"per-check timeout (default: {DEFAULT_TIMEOUT_SECONDS:g})",
    )
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        print(
            f"verify.py: --timeout-seconds must be > 0, got {args.timeout_seconds:g}",
            file=sys.stderr,
        )
        return 2
    root = Path(args.root)
    if not root.is_dir():
        print(f"verify.py: --root is not a directory: {args.root}", file=sys.stderr)
        return 2
    config_path = Path(args.config) if args.config else None
    try:
        checks = load_checks(root, config_path, args.target)
        gate = gate_argvs(root)
    except release.ConfigError as exc:
        print(f"verify.py: {exc}", file=sys.stderr)
        return 2
    problems = parity_problems(checks, gate) + test_guard_problems(checks, root)
    if problems:
        for problem in problems:
            print(f"verify.py: {problem}", file=sys.stderr)
        print(
            f"verify.py: {len(problems)} configuration problem(s); no check ran",
            file=sys.stderr,
        )
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    results = run_all(checks, root, os.environ, args.timeout_seconds)
    failed = [result for result in results if result["status"] != "PASS"]
    print(
        f"verify.py: {len(results) - len(failed)} passed, {len(failed)} failed "
        f"of {len(results)} checks"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
