# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Run ONE agent-CLI process for scripts/implement.py (driver "cli" in models.toml).

This module is the reusable CLI execution boundary: it resolves the configured
`command`, expands only the validated placeholders in individual arguments, runs the
program from the selected worktree with a deadline, and returns a structured result.
It owns no workflow artifact — implement.py writes impl-<n>.json, the progress file and
status.md, and a later role delegation can reuse this module for its own processes.

Placeholder and transport rules (validated in scripts/models.py, enforced again here):

- only {worktree}, {prompt_file} and {last_message_file} are expanded, each argument on
  its own; there is no shell and no other interpolation;
- prompt_via "stdin" writes the UTF-8 prompt to the child's stdin; "file" writes it to a
  temporary file outside the worktree whose path replaces {prompt_file} (required for
  that transport); "arg" appends the entire prompt as one final argument;
- the temporary files live in a system temporary directory and are removed on every
  exit path.

Execution: the program is resolved like scripts/gates.py resolves plan lines (a word
with a path separator resolves against the worktree, on Windows also via PATHEXT,
otherwise shutil.which with the child's PATH). On Windows a resolved .cmd/.bat program
runs through scripts/launch.py's guarded cmd.exe line, and a cmd metacharacter is
refused rather than escaped; because prompt_via "arg" would put the whole prompt on that
command line, it is refused for a shim program (use stdin or file). Otherwise an argv
list is passed with shell=False. stdout and stderr are drained by reader threads, so a
full pipe can never defeat the deadline; the excerpts kept are bounded. The deadline is
a monotonic wall clock; on timeout the process tree is killed and reaped (the process
group on POSIX, taskkill /F /T plus kill on Windows, as in scripts/gates.py), the
streams are closed and the scratch files removed.

Output parsing (usage_format): "codex-jsonl" parses stdout as one JSON event per line,
incrementally in the reader thread; every turn.completed event's usage is summed once
(input, cached input, output, and optional reasoning output counted separately, never
double-counted as output), agent_message item.completed events give the final message,
and a turn.failed/error event marks the run as failed no matter the exit code. Unknown
events are tolerated; a recognized usage that is not numeric makes the usage unknown
(null) instead of fabricating zero. "none" uses the final plain stdout message and
leaves usage and cost unknown.

Secrets: secret_values()/redact() replace the values of key-looking environment
variables (and of extra names such as a profile's api_key_env) in any text this module
returns, so diagnostics, event records and the recorded command never persist a key.

Stdlib only, started by `uv run --script` or imported as a sibling module, so it runs on
macOS, Linux and Windows without Git Bash.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import launch

OUTPUT_CAP = 20000  # characters of stdout/stderr kept per stream
LINE_CAP = 1_000_000  # one stdout line is at most this many bytes before it is dropped
EVENT_CAP = 200  # structured events reported through on_event
READ_CHUNK = 65536
SECRET_TOKENS = {"KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL"}
MIN_REDACT_LENGTH = 4
REDACTED = "***"
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
ALLOWED_PLACEHOLDERS = {"worktree", "prompt_file", "last_message_file"}
USAGE_INT_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
FAILURE_EVENT_TYPES = {"turn.failed", "error", "item.failed"}


class CliRefused(Exception):
    """The command cannot be built or started; the attempt is unavailable."""


@dataclass
class CliRun:
    """One finished CLI process: bounded excerpts, structured result, redacted argv."""

    command: list[str]  # the expanded argv as run (before any Windows shim wrapping)
    shim: bool  # True when a .cmd/.bat program ran through the cmd.exe line
    exit_code: int | None
    timed_out: bool
    stdout: str  # bounded excerpt
    stderr: str  # bounded excerpt
    final_message: str | None
    usage: dict | None  # {"input","cached","reasoning","output"} or None when unknown
    usage_malformed: bool
    failure_event: bool
    events: list[dict] = field(default_factory=list)  # bounded structured events
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------- redaction


def secret_values(env: dict, extra_names=()) -> list[str]:
    """The values that must never be printed: key-looking variables and extra names."""
    names = {name for name in extra_names if name}
    for var, value in env.items():
        if not value:
            continue
        if any(token in SECRET_TOKENS for token in var.upper().split("_")):
            names.add(var)
    return sorted({env[n] for n in names if env.get(n)}, key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    for value in secrets:
        if len(value) >= MIN_REDACT_LENGTH:
            text = text.replace(value, REDACTED)
    return text


# ---------------------------------------------------------------------------- resolution


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


def expand_arguments(command: list[str], values: dict) -> list[str]:
    """Expand the allowed placeholders in each argument; refuse anything else."""
    expanded = []
    for argument in command:
        if not isinstance(argument, str) or not argument:
            raise CliRefused(
                f"command entries must be non-empty strings, got {argument!r}"
            )

        def replace(match, argument=argument):
            name = match.group(1)
            if name not in ALLOWED_PLACEHOLDERS:
                raise CliRefused(
                    f"unknown placeholder {{{name}}} in {argument!r}; allowed: "
                    f"{sorted(ALLOWED_PLACEHOLDERS)}"
                )
            return values[name]

        expanded.append(PLACEHOLDER.sub(replace, argument))
    return expanded


def build_popen(
    argv: list[str],
    prompt_via: str,
    prompt: str,
    env: dict,
    run_dir: Path,
    windows: bool | None = None,
):
    """Build the Popen argument for the expanded argv.

    Returns (popen_arg, shim). Raises CliRefused when the program is missing or the
    combination cannot be run safely (a shim program with prompt_via "arg", or a cmd
    metacharacter in the shim line). `windows` overrides the platform check so tests
    run on every OS.
    """
    found = find_program(argv[0], run_dir, env)
    if found is None:
        raise CliRefused(f"program not found: {argv[0]}")
    is_nt = os.name == "nt" if windows is None else windows
    if is_nt and launch.is_cmd_script(found):
        if prompt_via == "arg":
            raise CliRefused(
                'prompt_via = "arg" appends the whole prompt as a command-line '
                "argument, but a .cmd/.bat program runs through cmd.exe, which "
                'refuses metacharacters; use prompt_via "stdin" or "file"'
            )
        rest = "" if len(argv) == 1 else " " + subprocess.list2cmdline(argv[1:])
        try:
            line = launch.cmd_command_line(found, rest, launch.cmd_exe(env))
        except ValueError as exc:
            raise CliRefused(str(exc)) from None
        return line, True
    if prompt_via == "arg":
        return [found, *argv[1:], prompt], False
    return [found, *argv[1:]], False


# ---------------------------------------------------------------------------- output parsing


def _int(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def usage_from_event(event: dict):
    """The usage of one turn.completed event, or None; raises ValueError when the
    recognized usage is present but not numeric (never fabricate zero)."""
    if event.get("type") != "turn.completed":
        return None
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    result = {}
    malformed = False
    for source, target in (
        ("input_tokens", "input"),
        ("cached_input_tokens", "cached"),
        ("output_tokens", "output"),
        ("reasoning_output_tokens", "reasoning"),
    ):
        if source not in usage:
            continue
        value = _int(usage[source])
        if value is None:
            malformed = True
        else:
            result[target] = value
    if malformed:
        raise ValueError(f"turn.completed usage is not numeric: {usage!r}")
    return result or None


def _redact_strings(value, secrets: list[str]):
    """Recursively redact every string inside a parsed event field."""
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {key: _redact_strings(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_strings(item, secrets) for item in value]
    return value


def event_summary(
    event: dict, elapsed: float, secrets: list[str] | None = None
) -> dict:
    """One bounded progress record for a parsed stdout event.

    Every string field is redacted with `secrets` before the record can reach
    `on_event` or `CliRun.events`, so an agent_message quoting a key never
    persists in the progress file or metrics.
    """
    secrets = secrets or []
    record = {
        "event": redact(str(event.get("type", "unknown"))[:80], secrets),
        "elapsed_seconds": round(elapsed, 3),
    }
    if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
        record["usage"] = _redact_strings(
            {
                key: event["usage"][key]
                for key in USAGE_INT_FIELDS
                if key in event["usage"]
            },
            secrets,
        )
    item = event.get("item")
    if isinstance(item, dict) and item.get("type") == "agent_message":
        text = item.get("text")
        if isinstance(text, str):
            record["message"] = redact(text[:500], secrets)
    if event.get("type") in FAILURE_EVENT_TYPES:
        record["failed"] = True
    return record


class _Bounded:
    """A bounded text buffer keeping the head and the tail of what was written."""

    def __init__(self, cap: int):
        self.cap = cap
        self.head = ""
        self.tail = ""
        self.total = 0
        self.truncated = 0

    def append(self, text: str) -> None:
        self.total += len(text)
        if len(self.head) < self.cap // 2:
            room = self.cap // 2 - len(self.head)
            self.head += text[:room]
            text = text[room:]
        if text:
            keep = self.cap - len(self.head)
            if keep > 0:
                self.tail = (self.tail + text)[-keep:]
            self.truncated += len(text) - max(keep, 0)

    def value(self) -> str:
        if self.truncated:
            return f"{self.head}\n...[{self.truncated} characters truncated]...\n{self.tail}"
        return self.head + self.tail


class _LineReader:
    """Drain one stream on a thread: bounded excerpt plus incremental JSONL parsing."""

    def __init__(self, stream, cap: int, parse_jsonl: bool, handler):
        self.stream = stream
        self.buffer = _Bounded(cap)
        self.parse_jsonl = parse_jsonl
        self.handler = handler  # callable(line_text) for each complete stdout line
        self.line = ""
        self.dropped_lines = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def join(self, timeout: float) -> None:
        self.thread.join(timeout=timeout)

    def _run(self) -> None:
        try:
            while True:
                chunk = self.stream.read(READ_CHUNK)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                self.buffer.append(text)
                if self.parse_jsonl:
                    self._feed(text)
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.stream.close()
            except OSError:
                pass

    def _feed(self, text: str) -> None:
        self.line += text
        while "\n" in self.line:
            line, self.line = self.line.split("\n", 1)
            self._handle_line(line)
        if len(self.line) > LINE_CAP:
            self.dropped_lines += 1
            self.line = ""

    def _handle_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped.startswith("{"):
            return
        try:
            event = json.loads(stripped)
        except ValueError:
            return
        if isinstance(event, dict):
            self.handler(event)


# ---------------------------------------------------------------------------- process tree


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


# ---------------------------------------------------------------------------- the run


def run_cli(
    *,
    command: list[str],
    prompt: str,
    prompt_via: str,
    usage_format: str,
    timeout_seconds: float,
    worktree: Path,
    env: dict,
    on_event=None,
    secrets: list[str] | None = None,
) -> CliRun:
    """Run one CLI process and return its structured result.

    `on_event(record)` is called from the reader thread for each parsed stdout event
    (at most EVENT_CAP), so the caller can append bounded progress records. Raises
    CliRefused when the command cannot be built or the program is missing.
    """
    secrets = secrets or []
    worktree = Path(worktree)
    with tempfile.TemporaryDirectory(prefix="task-flow-cli-") as tmp:
        prompt_file = Path(tmp) / "prompt.md"
        last_message_file = Path(tmp) / "last-message.txt"
        prompt_file.write_text(prompt, encoding="utf-8", newline="")
        last_message_file.write_text("", encoding="utf-8", newline="")
        values = {
            "worktree": str(worktree),
            "prompt_file": str(prompt_file),
            "last_message_file": str(last_message_file),
        }
        argv = expand_arguments(list(command), values)
        popen_arg, shim = build_popen(argv, prompt_via, prompt, env, worktree)
        if prompt_via == "stdin":
            stdin = subprocess.PIPE
        else:
            stdin = subprocess.DEVNULL
        kwargs = {
            "cwd": worktree,
            "env": env,
            "stdin": stdin,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        started = time.monotonic()
        try:
            proc = subprocess.Popen(popen_arg, **kwargs)
        except OSError as exc:
            raise CliRefused(f"cannot start {argv[0]}: {exc}") from exc

        events: list[dict] = []
        usage_total: dict | None = None
        usage_malformed = False
        failure_event = False
        final_from_events: list[str] = []

        def handle_event(event: dict) -> None:
            nonlocal usage_total, usage_malformed, failure_event
            elapsed = time.monotonic() - started
            if len(events) < EVENT_CAP:
                events.append(event_summary(event, elapsed, secrets))
            try:
                usage = usage_from_event(event)
            except ValueError:
                usage_malformed = True
                usage = None
            if usage is not None:
                if usage_total is None:
                    usage_total = {"input": 0, "cached": 0, "reasoning": 0, "output": 0}
                for key, value in usage.items():
                    usage_total[key] = usage_total.get(key, 0) + value
            if event.get("type") in FAILURE_EVENT_TYPES:
                failure_event = True
            item = event.get("item")
            if (
                event.get("type") == "item.completed"
                and isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
            ):
                final_from_events.append(item["text"])
            if on_event is not None and len(events) <= EVENT_CAP:
                try:
                    on_event(event_summary(event, elapsed, secrets))
                except Exception:  # noqa: BLE001, S110 - a callback bug must not kill the run
                    pass

        parse_jsonl = usage_format == "codex-jsonl"
        stdout_reader = _LineReader(proc.stdout, OUTPUT_CAP, parse_jsonl, handle_event)
        stderr_reader = _LineReader(proc.stderr, OUTPUT_CAP, False, None)
        stdout_reader.start()
        stderr_reader.start()
        if prompt_via == "stdin":
            # a writer thread, so a prompt larger than the pipe buffer cannot block
            # the deadline loop and a child that never reads stdin cannot hang us
            def write_prompt() -> None:
                try:
                    proc.stdin.write(prompt.encode("utf-8"))
                    proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass

            threading.Thread(target=write_prompt, daemon=True).start()
        deadline = started + timeout_seconds
        timed_out = False
        try:
            while True:
                try:
                    proc.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        timed_out = True
                        kill_tree(proc, env)
                        break
            if timed_out:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    kill_tree(proc, env)
                    proc.wait()
            else:
                proc.wait()
        finally:
            if proc.poll() is None:
                kill_tree(proc, env)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.wait()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            stdout_reader.join(timeout=5)
            stderr_reader.join(timeout=5)
        duration = time.monotonic() - started

        stdout_text = redact(stdout_reader.buffer.value(), secrets)
        stderr_text = redact(stderr_reader.buffer.value(), secrets)
        if parse_jsonl:
            final_message = final_from_events[-1] if final_from_events else None
            if (
                final_message is None
                and last_message_file.read_text(
                    encoding="utf-8", errors="replace"
                ).strip()
            ):
                final_message = last_message_file.read_text(
                    encoding="utf-8", errors="replace"
                ).strip()
            usage = None if usage_malformed else usage_total
        else:
            final_message = stdout_text.strip() or None
            usage = None
        if final_message is not None:
            final_message = redact(final_message, secrets)[:OUTPUT_CAP]
        return CliRun(
            command=[redact(argument, secrets) for argument in argv],
            shim=shim,
            exit_code=None if timed_out else proc.returncode,
            timed_out=timed_out,
            stdout=stdout_text,
            stderr=stderr_text,
            final_message=final_message,
            usage=usage,
            usage_malformed=usage_malformed,
            failure_event=failure_event,
            events=events,
            duration_seconds=duration,
        )
