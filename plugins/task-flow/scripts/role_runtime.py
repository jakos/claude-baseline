# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""The role-neutral runtime of task-flow's external producer roles (the planner today).

scripts/implement.py owns the implementer's API Session, tool dispatch, prompt assembly
and accounting; this module is the reusable core a second role (the planner, later the
backlog and reviewer of external-review-backlog) runs on without copying it: request
retries, the fallback chain, usage/cost accounting with nullable (never fabricated)
values, environment redaction and deadlines, plus the read-only tool surface a producer
role gets (bounded read/list/search and a small argv-based read-only command allowlist,
checked with scripts/rules.py under the real role). The CLI transport is
scripts/cli_driver.py, reused, never copied.

Differences from the implementer, deliberate: a producer role has no write_file or
edit_file — a role adapter returns artifact text instead of obeying the agent's Write
instruction, and the trusted runner (scripts/role_run.py) publishes the validated
artifact — and its run_command takes an argv list held to rules.READ_ONLY_PROGRAMS
(read-only subcommands and flags only), so no interpreter, shell syntax or redirect
can run. Resolved paths and symlinks are contained to the selected repository (the
worktree) and the explicitly supplied roots (plugin root, task directory) — for the
file tools and for every path-looking run_command argument alike.

Usage accounting keeps the implementer's semantics: a driver "cli" profile with billing
"subscription" has no measurable cost (null, not zero); a profile without reported
usage has unknown usage (null); known subtotals stay additive next to a null total.
Metrics are the bounded, schema-versioned role-<role>-<invocation-id>.json records
written next to the task artifacts; impl-<n>.json belongs to implement.py alone and is
never touched here.

Stdlib only (plus cli_driver, drivers, models, rules from this directory), started by
`uv run --script` or imported as a sibling module, so it runs on macOS, Linux and
Windows without Git Bash.
"""

import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import cli_driver
import drivers
import models
import rules

OUTPUT_CAP = 20000
GREP_MAX_MATCHES = 200
LIST_MAX_ENTRIES = 500
READ_DEFAULT_LIMIT = 2000
MAX_RETRY_AFTER = 60
METRICS_SCHEMA = "role-metrics/1"
METRICS_CAP = 100_000
# The environment allowlist a role's child processes get (same as the implementer's).
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
    "CODEX_HOME",
}
CHILD_ENV_PREFIXES = ("LC_", "UV_", "PYTHON")


class Refusal(Exception):
    """A precondition failed; nothing was sent to any model."""


class Unavailable(Exception):
    """The profile stayed unavailable after its retries; try the next fallback."""


class Fatal(Exception):
    """A non-retryable model error; the fallback chain is not consulted."""


class ToolError(Exception):
    """A tool call that cannot be done; returned to the model as the result."""


def log(message: str) -> None:
    print(f"[role] {message}", file=sys.stderr, flush=True)


def one_line(text, limit: int = 300) -> str:
    return " ".join(str(text).split())[:limit]


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def truncate(text: str, cap: int = OUTPUT_CAP) -> str:
    if len(text) <= cap:
        return text
    half = cap // 2
    omitted = len(text) - 2 * half
    return f"{text[:half]}\n...[{omitted} characters truncated]...\n{text[-half:]}"


def child_env() -> dict:
    """The allowlisted child environment; os.environ is never modified."""
    env = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if upper in CHILD_ENV_EXACT or upper.startswith(CHILD_ENV_PREFIXES):
            env[name] = value
    return env


# ---------------------------------------------------------------------------- HTTP loop


def retry_after(headers) -> float | None:
    value = headers.get("Retry-After") if headers else None
    try:
        return min(float(value), MAX_RETRY_AFTER) if value is not None else None
    except ValueError:
        return None


def is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    reason = getattr(exc, "reason", None)
    return isinstance(reason, TimeoutError)


def post(
    profile: dict,
    key: str | None,
    body: dict,
    attempts: list | None = None,
    *,
    urlopen=None,
    sleep=time.sleep,
) -> dict:
    """POST one request to the profile's driver endpoint; retry retryable failures.

    When `attempts` is a list, every attempt is recorded in it as
    {"seconds", "outcome", "wait"} (the same shape implement.py records). `urlopen` and
    `sleep` are injectable so tests need no network and no real waiting.
    """
    urlopen = urlopen or urllib.request.urlopen
    driver = drivers.get(profile["driver"])
    url = driver.url(profile)
    data = json.dumps(body).encode("utf-8")
    headers = driver.headers(key)
    retries = profile["retries"]
    last = "no attempt made"
    for attempt in range(retries + 1):
        delay = None
        started = time.monotonic()
        try:
            request = urllib.request.Request(
                url, data=data, headers=headers, method="POST"
            )
            with urlopen(request, timeout=profile["request_timeout_seconds"]) as r:
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
                {"seconds": time.monotonic() - started, "outcome": outcome, "wait": 0.0}
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
            sleep(wait)
    raise Unavailable(f"{last} after {retries + 1} attempts")


# ---------------------------------------------------------------------------- accounting


class UsageLedger:
    """Per-profile token usage and USD cost; unknown stays None, never zero."""

    def __init__(self, profiles: dict):
        self.profiles = profiles
        self.usage: dict[str, dict | None] = {}

    def add_api(self, name: str, payload) -> dict:
        profile = self.profiles[name]
        usage = drivers.get(profile["driver"]).usage(payload)
        entry = self.usage.setdefault(
            name,
            {"input": 0, "cached": 0, "cache_write": 0, "output": 0, "reasoning": 0},
        )
        entry["input"] += usage["input"]
        entry["cached"] += usage["cached"]
        entry["cache_write"] += usage["cache_write"]
        entry["output"] += usage["output"]
        return {
            "input": usage["input"],
            "cached": usage["cached"],
            "output": usage["output"],
        }

    def add_cli(self, name: str, usage: dict | None, malformed: bool) -> None:
        if usage is None or malformed:
            self.usage[name] = None  # unknown usage stays null, never zero
            return
        entry = self.usage.setdefault(
            name,
            {"input": 0, "cached": 0, "cache_write": 0, "output": 0, "reasoning": 0},
        )
        for key in ("input", "cached", "reasoning", "output"):
            entry[key] += usage.get(key, 0)

    def profile_cost(self, name: str, tokens: dict | None) -> float | None:
        """One profile's USD cost, or None when unknown (never fabricated)."""
        if tokens is None:
            return None
        profile = self.profiles[name]
        write_price = profile["price_cache_write_per_mtok"]
        if write_price is None:
            write_price = profile["price_input_per_mtok"]
        if profile["driver"] == "cli":
            if profile["billing"] == "subscription":
                return None
            prices = (
                profile["price_input_per_mtok"],
                profile["price_cached_input_per_mtok"],
                profile["price_output_per_mtok"],
                write_price,
            )
            if not any(prices):
                return None
        return (
            (tokens["input"] - tokens["cached"] - tokens["cache_write"])
            * profile["price_input_per_mtok"]
            + tokens["cached"] * profile["price_cached_input_per_mtok"]
            + tokens["cache_write"] * write_price
            + tokens["output"] * profile["price_output_per_mtok"]
        ) / 1e6

    def cost(self) -> float | None:
        """The total USD cost, or None when any used profile's cost is unknown."""
        total = 0.0
        for name, tokens in self.usage.items():
            value = self.profile_cost(name, tokens)
            if value is None:
                return None
            total += value
        return total

    def cost_known(self) -> float:
        """The subtotal of the known costs (additive metadata next to a null total)."""
        total = 0.0
        for name, tokens in self.usage.items():
            value = self.profile_cost(name, tokens)
            if value is not None:
                total += value
        return total

    def tokens(self) -> dict | None:
        """Summed token counts, or None when any used profile's usage is unknown."""
        if any(entry is None for entry in self.usage.values()):
            return None
        total = {"input": 0, "cached": 0, "output": 0}
        for tokens in self.usage.values():
            for field_name in total:
                total[field_name] += tokens[field_name]
        return total

    def cost_text(self) -> str:
        cost = self.cost()
        return f"${cost:.4f}" if cost is not None else "$unknown"


# ---------------------------------------------------------------------------- producer tools

PRODUCER_TOOLS = [
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
            "description": "Run ONE allowlisted read-only program with its arguments "
            "(an argv list, no shell, no pipes, redirects or expansion) in the "
            "worktree. Returns the exit code and the combined output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "the program and its arguments, e.g. "
                        '["git", "log", "--oneline", "-5"]',
                    }
                },
                "required": ["argv"],
            },
        },
    },
]


class ProducerTools:
    """The read-only tool surface of an external producer role.

    Paths resolve inside `roots` (the selected repository plus the explicitly supplied
    plugin/artifact roots) and symlinks are contained too: a resolved path must stay
    under one of them. There is no write_file/edit_file: the runner publishes the
    validated artifact. run_command takes an argv list checked by rules.check_argv
    under the real role — with every path-looking argument resolved and contained to
    the same roots — and runs without a shell in the worktree with the child
    environment and a deadline.
    """

    def __init__(self, worktree: Path, roots, role: str, command_timeout: float):
        self.worktree = Path(worktree).resolve()
        self.roots = [Path(root).resolve() for root in roots]
        self.role = role
        self.command_timeout = command_timeout

    def _inside_roots(self, resolved: Path) -> bool:
        return any(_is_relative_to(resolved, root) for root in self.roots)

    def _looks_like_path(self, arg: str) -> bool:
        """True when an argv word is a path: absolute, separator/`..`-bearing, or an
        existing file relative to the worktree (the subprocess working directory)."""
        if arg.startswith("-"):
            return False
        candidate = Path(arg)
        if candidate.is_absolute():
            return True
        if "/" in arg or "\\" in arg or ".." in candidate.parts:
            return True
        return (self.worktree / candidate).exists()

    def check_argv_paths(self, argv: list[str]) -> None:
        """Refuse any path-looking argv word that resolves outside the roots.

        Resolved symlinks are contained the same way the file tools contain them: a
        link inside a root that points outside resolves outside and is refused.
        """
        for arg in argv[1:]:
            if not self._looks_like_path(arg):
                continue
            candidate = Path(arg)
            if not candidate.is_absolute():
                candidate = self.worktree / candidate
            resolved = candidate.resolve()
            if not self._inside_roots(resolved):
                raise ToolError(
                    f"denied: {arg} resolves outside the selected repository roots"
                )

    def resolve(self, raw) -> Path:
        if not isinstance(raw, str) or not raw:
            raise ToolError("error: path must be a non-empty string")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.worktree / candidate
        resolved = candidate.resolve()
        if not self._inside_roots(resolved):
            raise ToolError(f"denied: {raw} is outside the selected repository roots")
        return resolved

    def rel(self, path: Path) -> str:
        for root in self.roots:
            if _is_relative_to(path, root):
                return path.relative_to(root).as_posix()
        return path.as_posix()

    @staticmethod
    def text_arg(args: dict, name: str) -> str:
        value = args.get(name)
        if not isinstance(value, str):
            raise ToolError(f"error: {name} must be a string")
        return value

    def read_file(self, args):
        path = self.resolve(args.get("path"))
        offset = args.get("offset", 1)
        limit = args.get("limit", READ_DEFAULT_LIMIT)
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or not isinstance(limit, int)
            or isinstance(limit, bool)
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

    def list_files(self, args):
        base = self.resolve(args.get("path", "."))
        pattern = args.get("pattern") or "**/*"
        if not isinstance(pattern, str):
            raise ToolError("error: pattern must be a string")
        try:
            found = []
            for item in base.glob(pattern):
                if not item.is_file() or not self._inside_roots(item.resolve()):
                    continue
                rel = self.rel(item.resolve())
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
            resolved = item.resolve()
            if not self._inside_roots(resolved):
                continue
            rel = self.rel(resolved)
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

    def run_command(self, args):
        argv = args.get("argv")
        if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
            raise ToolError("error: argv must be a list of strings")
        reason = rules.check_argv(argv, self.role)
        if reason is not None:
            raise ToolError(f"denied: {reason}")
        self.check_argv_paths(argv)
        env = child_env()
        found = cli_driver.find_program(argv[0], self.worktree, env)
        if found is None:
            raise ToolError(f"error: program not found: {argv[0]}")
        popen_argv = [found, *argv[1:]]
        try:
            proc = subprocess.Popen(
                popen_argv,
                cwd=self.worktree,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            raise ToolError(f"error: cannot start command: {exc}") from None
        timed_out = False
        started = time.monotonic()
        try:
            output, _ = proc.communicate(timeout=self.command_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            cli_driver.kill_tree(proc, env)
            try:
                output, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                output = b""
        duration = time.monotonic() - started
        text = output.decode("utf-8", errors="replace")
        head = (
            f"timed out after {self.command_timeout:g} s and was killed"
            if timed_out
            else f"exit code: {proc.returncode}"
        )
        log(
            f"run_command ({duration:.1f} s): {' '.join(argv)[:80]} -> {one_line(head, 60)}"
        )
        return f"{head}\n{text}"

    def execute(self, name: str, args) -> str:
        """Run one tool; returns the result text (a denial is feedback, not an error)."""
        if not isinstance(args, dict):
            return "error: arguments must be a JSON object"
        handler = {
            "read_file": self.read_file,
            "list_files": self.list_files,
            "grep": self.grep,
            "run_command": self.run_command,
        }.get(name)
        if handler is None:
            return (
                f"error: unknown tool {name!r} (this role has no write or edit tools)"
            )
        try:
            return truncate(handler(args))
        except ToolError as exc:
            return truncate(str(exc))
        except Exception as exc:  # noqa: BLE001 - a tool bug must not end the run
            return truncate(f"error: {type(exc).__name__}: {exc}")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------- the API session


@dataclass
class SessionResult:
    """One finished external-role session: status, artifact text and accounting."""

    status: str  # "ok" | "blocked" | "failed" | "unavailable"
    reason: str
    text: str | None = None
    chain: list[dict] = field(default_factory=list)
    repairs: int = 0
    turns: int = 0
    ledger: "UsageLedger | None" = None


class RoleSession:
    """One role-neutral API loop over a profile chain, with a validation repair hook.

    `validator(text)` is called with the model's final message and returns
    (accepted: bool, blocked: bool, reasons: list[str]). When it refuses and
    `max_repairs` (default 1) allows, the bounded reasons are appended to the
    conversation as one repair request and the loop continues; provider retries and
    fallback stay bounded independently and never silently select Claude.
    """

    def __init__(
        self,
        profiles: dict,
        profile_name: str,
        system: str,
        user: str,
        tools: ProducerTools,
        validator,
        *,
        max_repairs: int = 1,
        max_turns: int | None = None,
        heartbeat: float = 0.0,
        urlopen=None,
        sleep=time.sleep,
    ):
        self.profiles = profiles
        self.start = profiles[profile_name]
        self.profile_name = profile_name
        self.system = system
        self.user = user
        self.tools = tools
        self.validator = validator
        self.max_repairs = max_repairs
        self.max_turns = max_turns
        self.heartbeat = heartbeat
        self.urlopen = urlopen
        self.sleep = sleep
        self.ledger = UsageLedger(profiles)
        self.chain: list[dict] = []
        self.repairs = 0
        self.turns = 0
        self.retries: dict[str, list] = {}

    def run(self) -> SessionResult:
        candidates = [self.profile_name, *self.start["fallback"]]
        interrupted = None
        for index, name in enumerate(candidates):
            profile = self.profiles[name]
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
            status, reason, text, repairs = self._run_profile(
                name, profile, interrupted
            )
            self.chain.append(
                {
                    "profile": name,
                    "outcome": status,
                    "reason": reason,
                    "retries": self.retries.get(name, []),
                }
            )
            self.repairs += repairs
            if status != "unavailable":
                return SessionResult(
                    status,
                    reason,
                    text,
                    self.chain,
                    self.repairs,
                    self.turns,
                    self.ledger,
                )
            interrupted = (name, reason)
            log(f"profile {name} unavailable ({reason}); trying the next fallback")
        reasons = "; ".join(
            f"{c['profile']}: {c['reason']}" for c in self.chain if c["outcome"] != "ok"
        )
        return SessionResult(
            "failed",
            f"all profiles unavailable ({reasons})",
            None,
            self.chain,
            self.repairs,
            self.turns,
            self.ledger,
        )

    def _run_profile(self, name: str, profile: dict, interrupted):
        driver = drivers.get(profile["driver"])
        conversation = driver.start(self.system, self.user)
        if interrupted:
            conversation = driver.start(
                self.system,
                self.user
                + f"\n\nNote: a previous attempt on profile {interrupted[0]} was "
                f"interrupted ({interrupted[1]}); inspect before continuing.",
            )
        key = models.api_key(profile)
        cap = self.start["max_cost_usd"]
        limit_turns = self.max_turns or profile["max_turns"]
        repairs = 0
        while True:
            if self.turns >= limit_turns:
                return (
                    "failed",
                    f"max_turns ({limit_turns}) exceeded on {name}",
                    None,
                    repairs,
                )
            attempts: list = []
            try:
                payload = post(
                    profile,
                    key,
                    driver.body(profile, self.system, conversation, PRODUCER_TOOLS),
                    attempts,
                    urlopen=self.urlopen,
                    sleep=self.sleep,
                )
            except Unavailable as exc:
                self._note_attempts(name, attempts)
                return "unavailable", str(exc), None, repairs
            except Fatal as exc:
                self._note_attempts(name, attempts)
                return "failed", f"{name}: {exc}", None, repairs
            self._note_attempts(name, attempts)
            self.turns += 1
            turn_tokens = self.ledger.add_api(name, payload)
            cost = self.ledger.cost()
            if cap is not None and cost is not None and cost > cap:
                return (
                    "failed",
                    f"max_cost_usd ({cap:g}) exceeded: {self.ledger.cost_text()}",
                    None,
                    repairs,
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
                    None,
                    repairs,
                )
            try:
                reply = driver.read(payload)
            except drivers.Malformed as exc:
                return "failed", f"{name}: {exc}", None, repairs
            if reply.refused:
                return "failed", f"{name}: the model refused the request", None, repairs
            if not reply.calls:
                text = reply.text or ""
                accepted, blocked, reasons = self.validator(text)
                if accepted:
                    return ("blocked" if blocked else "ok"), "finished", text, repairs
                if repairs < self.max_repairs:
                    repairs += 1
                    bounded = "\n".join(f"- {reason}" for reason in reasons[:10])
                    conversation.append(
                        {
                            "role": "user",
                            "content": (
                                "Your previous answer was not a valid plan.md artifact. "
                                "Fix exactly these problems and return the complete, "
                                "raw plan.md again (no code fence around it):\n"
                                f"{bounded}"
                            ),
                        }
                    )
                    log(
                        f"validation refused the artifact ({len(reasons)} reasons); one repair attempt"
                    )
                    continue
                return (
                    "failed",
                    "the artifact stayed invalid after the single repair attempt: "
                    + "; ".join(reasons[:5]),
                    text,
                    repairs,
                )
            results = []
            for call_id, tool_name, raw in reply.calls:
                try:
                    arguments = json.loads(raw) if isinstance(raw, str) else raw
                except ValueError:
                    arguments = None
                result = self.tools.execute(tool_name, arguments)
                log(f"turn {self.turns}: {tool_name} -> {one_line(result, 80)}")
                results.append((call_id, result))
            driver.append(conversation, reply, results)

    def _note_attempts(self, name: str, attempts: list) -> None:
        for index, attempt in enumerate(attempts):
            if attempt["outcome"] != "ok" and index + 1 < len(attempts):
                self.retries.setdefault(name, []).append(
                    {
                        "reason": attempt["outcome"],
                        "attempt_seconds": round(attempt["seconds"], 3),
                        "wait_seconds": round(attempt.get("wait", 0.0), 3),
                    }
                )


# ---------------------------------------------------------------------------- CLI transport


def run_cli_producer(
    profile: dict,
    prompt: str,
    worktree: Path,
    *,
    command_key: str = "read_only_command",
    extra_prompt: str = "",
) -> cli_driver.CliRun:
    """Run one producer-role CLI process through cli_driver with its read-only command.

    The command comes from the profile's `read_only_command` (verified read-only in
    models.py), never the implementer's workspace-write `command`. The final message
    is captured by the host (-o {last_message_file}, in a scratch directory outside the
    source worktree). Raises cli_driver.CliRefused when the command cannot be built.
    """
    env = child_env()
    for variable in profile["pass_env"]:
        value = os.environ.get(variable)
        if value is not None:
            env[variable] = value
    secrets = cli_driver.secret_values(
        os.environ, [profile.get("api_key_env")] + list(profile["pass_env"])
    )
    full_prompt = prompt + extra_prompt
    return cli_driver.run_cli(
        command=profile[command_key],
        prompt=full_prompt,
        prompt_via="stdin",
        usage_format=profile["usage_format"],
        timeout_seconds=profile["timeout_seconds"],
        worktree=worktree,
        env=env,
        secrets=secrets,
    )


# ---------------------------------------------------------------------------- metrics


def metrics_name(role: str, invocation_id: str) -> str:
    """The role metrics file name: role-<role>-<invocation-id>.json."""
    safe_role = re.sub(r"[^A-Za-z0-9_.-]", "_", role)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", invocation_id)
    return f"role-{safe_role}-{safe_id}.json"


def write_role_metrics(task_dir: Path, record: dict) -> Path:
    """Atomically write one bounded role metrics record; returns its path.

    The record is schema-versioned (role-metrics/1), UTF-8/LF, capped at
    METRICS_CAP characters, and redacted of secret values by the caller's redaction
    step. A write error refuses the run loudly rather than losing accounting.
    """
    text = json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if len(text) > METRICS_CAP:
        raise Refusal(
            f"the role metrics record is {len(text)} characters, over the "
            f"{METRICS_CAP} cap"
        )
    task_dir = Path(task_dir)
    path = task_dir / metrics_name(record["role"], record["invocation_id"])
    if path.is_symlink():
        raise Refusal(f"refusing to write through a symlink: {path}")
    temp = task_dir / f".{path.name}.tmp"
    temp.write_text(text, encoding="utf-8", newline="")
    os.replace(temp, path)
    return path


def load_role_metrics(task_dir: Path) -> list[dict]:
    """Every role-*.json record of a task directory, sorted by file name.

    A malformed record is skipped with a stderr warning, never silently counted twice:
    each file name carries one invocation id, so re-reading is idempotent.
    """
    task_dir = Path(task_dir)
    records = []
    if not task_dir.is_dir():
        return records
    for path in sorted(task_dir.glob("role-*.json")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            log(
                f"warning: skipping malformed role metrics {path.name}: {one_line(exc, 120)}"
            )
            continue
        if isinstance(record, dict) and record.get("schema") == METRICS_SCHEMA:
            records.append(record)
    return records


def role_usd(task_dir: Path) -> tuple[float, bool]:
    """(sum of known cost_usd over all role metrics, any unknown) of one task.

    Null cost (subscription billing or absent usage) stays unknown, never zero; the sum
    is recomputed from the invocation-id files, so it is idempotent across a resume.
    """
    total = 0.0
    unknown = False
    for record in load_role_metrics(task_dir):
        cost = record.get("cost_usd")
        if cost is None:
            unknown = True
        elif isinstance(cost, (int, float)) and not isinstance(cost, bool):
            total += cost
    return total, unknown


def impl_usd(task_dir: Path) -> tuple[float, bool]:
    """(known USD sum over the task's impl-<n>.json, any null) of the implementer stage.

    Recomputed from the round files — each file name carries its round — so a
    resume counts every round exactly once and never double counts. A null
    cost_usd (a driver "cli" subscription round or unreported usage) stays
    unknown, never zero.
    """
    total = 0.0
    unknown = False
    task_dir = Path(task_dir)
    if not task_dir.is_dir():
        return total, unknown
    for path in sorted(task_dir.glob("impl-*.json")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            log(
                f"warning: skipping malformed implementer metrics {path.name}: {one_line(exc, 120)}"
            )
            unknown = True
            continue
        if not isinstance(record, dict):
            unknown = True
            continue
        cost = record.get("cost_usd")
        if cost is None:
            unknown = True
        elif isinstance(cost, (int, float)) and not isinstance(cost, bool):
            total += cost
    return total, unknown


def invocation_usd(task_dir: Path) -> dict:
    """Per-stage external USD of one task, recomputed from its invocation files.

    Returns {"stages": {stage: (known sum, any unknown)}, "total": (sum, unknown),
    "files": <count of files read>}. role-*.json records contribute their record's
    `role` stage (the planner today), impl-<n>.json files the "implementer" stage.
    Every file name carries its invocation/round id, so re-reading is idempotent:
    a resume recomputes the same totals instead of double counting, and planner
    USD lands in the task's External USD alongside the implementer's. Unknown
    (null) cost stays unknown, never zero.
    """
    stages: dict[str, list] = {}
    files = 0

    def add(stage: str, cost) -> None:
        known, unknown = stages.get(stage, (0.0, False))
        if cost is None:
            stages[stage] = (known, True)
        elif isinstance(cost, (int, float)) and not isinstance(cost, bool):
            stages[stage] = (known + cost, unknown)
        else:
            stages[stage] = (known, True)

    task_dir = Path(task_dir)
    for record in load_role_metrics(task_dir):
        files += 1
        add(str(record.get("role") or "role"), record.get("cost_usd"))
    impl_paths = (
        [
            path
            for path in sorted(task_dir.glob("impl-*.json"))
            if path.is_file() and not path.is_symlink()
        ]
        if task_dir.is_dir()
        else []
    )
    if impl_paths:
        files += len(impl_paths)
        impl_total, impl_unknown = impl_usd(task_dir)
        stages["implementer"] = (impl_total, impl_unknown)
    total = 0.0
    unknown = False
    for known, is_unknown in stages.values():
        total += known
        unknown = unknown or is_unknown
    return {
        "stages": {stage: stages[stage] for stage in sorted(stages)},
        "total": (total, unknown),
        "files": files,
    }


def redact_record(record, secrets: list[str]) -> dict:
    """Recursively redact secret values from every string in a metrics record."""

    def walk(value):
        if isinstance(value, str):
            return cli_driver.redact(value, secrets)
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    return walk(record)
