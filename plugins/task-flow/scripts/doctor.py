# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Check whether this machine, this Claude Code session and this repository are ready for task-flow.

CLI:
  uv run --script plugins/task-flow/scripts/doctor.py [--project <dir>] [--online]

  --project  the repository to check (default: the current directory)
  --online   additionally send one authenticated GET <base_url>/models per profile in use
             (the provider's model list; it costs no tokens)

Output, UTF-8 on stdout:

  # task-flow doctor
  | # | Check | Result | Detail |
  | 1 | uv on PATH | PASS | uv 0.12.21 (...) at /c/.../uv |
  ...
  ## Fixes
  3. <check name>: fix (PowerShell): ...
  3. <check name>: fix (POSIX): ...
  ## Summary
  <n> PASS, <n> WARN, <n> FAIL

Every WARN and FAIL row has at least one fix line under `## Fixes`, carrying its row
number; `## Fixes` reads `none` when there is nothing to fix. No secret is ever printed:
every line passes through a redaction step that replaces the value of every key-looking
environment variable (and of every profile's api_key_env, and ANTHROPIC_API_KEY) with
`***`.

Exit codes: 0 when no check is FAIL, 1 when at least one check is FAIL, 2 on a usage
error. The doctor changes nothing: it writes no file and sets no variable, and the only
registry access (HKCU\\Environment on Windows) is read-only.

Local checks, in fixed order (each is a plain function; every input is an argument):

  1. uv on PATH            which("uv") found -> PASS with its path and `uv --version`;
                          missing -> FAIL with the PowerShell and POSIX installer
                          one-liners from astral.sh and "restart Claude Code".
  2. Python for the scripts  the highest requires-python in the `# /// script` headers of
                          <plugin root>/scripts/*.py (parsed, not hard-coded) is compared
                          with the running interpreter's sys.version_info -> PASS/FAIL.
  3. git                   which("git") and `git --version`; when the project is a work
                          tree, `git worktree list` must succeed -> PASS. Missing git ->
                          FAIL. The worktree probe failing inside a repo -> FAIL "git
                          without worktree support" (fix: upgrade git).
  4. repository            not inside a git work tree -> FAIL (fix: `git init`) and checks
                          5-6 are skipped; otherwise PASS with the top level.
  5. commit                `git rev-parse --verify --quiet HEAD` -> PASS; otherwise FAIL
                          (fix: `git commit --allow-empty -m "chore: initial commit"`).
  6. working tree          `git status --porcelain --no-optional-locks` ignoring `.work/`
                          is empty -> PASS; otherwise WARN with the count.
  7. plugin source         the plugin root in use (Path(__file__).parents[1]) and the
                          version of its .claude-plugin/plugin.json -> PASS. When the
                          project tracks a .claude-plugin/plugin.json whose name is
                          "task-flow" and the plugin root in use is not that directory ->
                          WARN "the session runs the installed task-flow, not this working
                          tree", with CLAUDE_CODE_PLUGIN_DIRS fixes (`;` on PowerShell, `:`
                          on POSIX) and "restart Claude Code".
  8. project config        .claude/task-flow.md absent -> PASS "absent: profile defaults
                          apply"; readable as UTF-8 -> PASS; otherwise FAIL.
  9. settings files        each existing .claude/settings.json,
                          .claude/settings.local.json and ~/.claude/settings.json must
                          parse as JSON -> PASS; one that does not -> FAIL (Claude Code
                          ignores a settings file it cannot parse completely).
 10. permission rules      the README's allow rules (REQUIRED_ALLOW) are looked up in the
                          union of the permissions.allow lists of the files of check 9; a
                          rule counts as present when the exact string is there or a bare
                          "Bash" allow rule is. Missing rules -> WARN listing them, with
                          one JSON fragment as the fix. Never FAIL.
 11. ANTHROPIC_API_KEY     set and non-empty -> WARN "Claude Code bills this session by
                          token instead of the subscription" (length shown, value never),
                          with the unset fixes for both shells; unset -> PASS.
 12. models.toml           models.load(project) -> PASS naming the files found (user,
                          project, or "none: every role is Claude"); ConfigError -> FAIL
                          with its message (file and key path) and the template as the
                          fix. Checks 13-14 do not run then.
 13. roles                 one PASS row listing models.resolve_roles().
 14. implementer profiles  only when [roles] implementer names a profile: one row per
                          aspect for the primary and for each name in its fallback list:
                          - enabled = false: FAIL on the primary (implement.py refuses
                            it), WARN on a fallback (skipped);
                          - key: billing = "local" without api_key_env -> PASS "no key
                            needed"; variable set -> PASS "`<NAME>` set (<n> chars)";
                            missing -> FAIL on the primary, WARN on a fallback; on
                            Windows, when the variable exists in the user environment
                            but not in this process, the detail says the session was
                            started before it was set and the fix is to restart Claude
                            Code, otherwise the PowerShell and POSIX ways to set it;
                          - base_url: FAIL when the scheme is not http/https or the host
                            is empty; WARN when it has a query or fragment, ends with the
                            driver's endpoint suffix (/chat/completions, /messages) or is
                            http:// to a non-local host (the key would travel
                            unencrypted);
                          - limits: WARN when max_turns > 200, when billing = "api" and
                            max_cost_usd is unset (no cost cap), and when billing = "api"
                            with all three price_* keys 0 (the cost then reads 0 USD);
                          - driver: DRIVER_CHECKS has one entry per driver doctor knows;
                            a profile whose driver has no entry -> WARN.

Online check (only with --online, only for the enabled profiles of check 14 that have
their key or need none): GET {base_url}/models with the driver's auth headers
(Authorization: Bearer <key> on openai-loop; x-api-key and anthropic-version on
anthropic-loop), via urllib.request, timeout min(request_timeout_seconds, 20) s:
  - HTTP 200 with JSON data[*].id -> PASS "HTTP 200 in <ms> ms; model `<id>` listed", or
    WARN "model `<id>` not among the <n> listed ids" (at most 5 ids shown, the configured
    id's closest matches first via difflib.get_close_matches);
  - 401/403 -> FAIL "key rejected"; 404 -> WARN "no model list at this URL: reachable,
    key not proven"; other HTTP status -> FAIL with the status code; connection error or
    timeout -> FAIL with the error class and the latency. No response body is printed
    except the parsed ids.

Stdlib only (plus models, imported from this directory), started by `uv run --script`,
so it runs on macOS, Linux and Windows without Git Bash. LF line endings; text read and
written as UTF-8 explicitly; git through subprocess with an argument list and a timeout.
"""

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import models

GIT_TIMEOUT_SECONDS = 30
ONLINE_TIMEOUT_CAP_SECONDS = 20
MAX_SHOWN_IDS = 5
MAX_TURNS_WARN = 200
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
REDACTED = "***"
MIN_REDACT_LENGTH = 4
SECRET_NAME_TOKENS = {"KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL"}
REQUIRED_ALLOW = [
    "Bash(uv run:*)",
    "Bash(git -C:*)",
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(git grep:*)",
    "Bash(git ls-files:*)",
    "Bash(git add:*)",
    "Bash(git worktree add:*)",
    "Bash(git switch:*)",
    "Bash(git rev-parse:*)",
]
# One entry per driver models.DRIVERS knows; test_doctor.py asserts the two stay equal.
# "auth" says which header carries the key on the online model-list request;
# "endpoint" is the path the driver appends to base_url itself.
DRIVER_CHECKS = {
    "openai-loop": {
        "online": "GET {base_url}/models with Authorization: Bearer <key>",
        "auth": "bearer",
        "endpoint": "/chat/completions",
    },
    "anthropic-loop": {
        "online": "GET {base_url}/models with x-api-key and anthropic-version headers",
        "auth": "x-api-key",
        "endpoint": "/messages",
        # keep in sync with drivers.ANTHROPIC_VERSION (doctor imports only models)
        "extra_headers": {"anthropic-version": "2026-10-21"},
    },
}

Which = Callable[[str], "str | None"]
Run = Callable[[list[str], "str | None"], subprocess.CompletedProcess]


@dataclass
class Check:
    """One report row: name, PASS/WARN/FAIL, a one-line detail and the fix lines."""

    name: str
    result: str
    detail: str
    fixes: list[str] = field(default_factory=list)


def make_run(timeout: int = GIT_TIMEOUT_SECONDS) -> Run:
    """A subprocess runner with a timeout; the only way this script starts a process."""

    def run(argv: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, cwd=cwd, capture_output=True, check=False, timeout=timeout
        )

    return run


def try_run(
    run: Run, argv: list[str], cwd: str | None = None
) -> subprocess.CompletedProcess | None:
    """run() that returns None instead of raising on OSError or timeout."""
    try:
        return run(argv, cwd)
    except (OSError, subprocess.TimeoutExpired):
        return None


def output_of(result: subprocess.CompletedProcess | None) -> str:
    if result is None:
        return ""
    return result.stdout.decode("utf-8", "replace").strip()


def windows_user_env(name: str) -> str | None:
    """One variable from the Windows user environment (HKCU\\Environment); read-only."""
    if sys.platform != "win32":
        return None
    import winreg  # stdlib, Windows only

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return value if isinstance(value, str) else None


# --- checks 1-3: the tools ------------------------------------------------------------


def check_uv(which: Which, run: Run) -> list[Check]:
    path = which("uv")
    if path is None:
        return [
            Check(
                "uv on PATH",
                "FAIL",
                "uv not found on PATH",
                [
                    (
                        "fix (PowerShell): powershell -ExecutionPolicy ByPass -c "
                        '"irm https://astral.sh/uv/install.ps1 | iex", then restart Claude Code'
                    ),
                    (
                        "fix (POSIX): curl -LsSf https://astral.sh/uv/install.sh | sh, "
                        "then restart Claude Code"
                    ),
                ],
            )
        ]
    version = output_of(try_run(run, [path, "--version"]))
    return [Check("uv on PATH", "PASS", f"{version or 'unknown version'} at {path}")]


REQUIREMENT_LINE = re.compile(r'^#\s*requires-python\s*=\s*"([^"]+)"')
CLAUSE = re.compile(r"^(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)$")


def script_requirements(scripts_dir: Path) -> list[tuple[str, str]]:
    """(requires-python spec, file name) of every script's `# /// script` header."""
    found: list[tuple[str, str]] = []
    for path in sorted(scripts_dir.glob("*.py")):
        in_block = False
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped == "# /// script":
                in_block = True
            elif stripped == "# ///":
                in_block = False
            elif in_block:
                match = REQUIREMENT_LINE.match(stripped)
                if match:
                    found.append((match.group(1), path.name))
    return found


def parse_requirement(spec: str) -> list[tuple[str, tuple[int, ...]]]:
    """A requires-python spec as [(operator, version tuple)] clauses."""
    clauses = []
    for part in spec.split(","):
        match = CLAUSE.match(part.strip())
        if not match:
            raise ValueError(f"unsupported requires-python clause: {part.strip()!r}")
        clauses.append(
            (match.group(1), tuple(int(x) for x in match.group(2).split(".")))
        )
    return clauses


def satisfies(version_info: tuple, clauses: list[tuple[str, tuple[int, ...]]]) -> bool:
    have = tuple(version_info)
    for operator, want in clauses:
        left, right = have[: len(want)], want
        if operator == ">=" and not left >= right:
            return False
        if operator == "<=" and not left <= right:
            return False
        if operator == ">" and not left > right:
            return False
        if operator == "<" and not left < right:
            return False
        if operator == "==" and left != right:
            return False
        if operator == "!=" and left == right:
            return False
    return True


def check_python(scripts_dir: Path, version_info: tuple) -> list[Check]:
    specs = [spec for spec, _source in script_requirements(scripts_dir)]
    if not specs:
        return [
            Check(
                "Python for the scripts",
                "FAIL",
                f"no requires-python header found in {scripts_dir}/*.py",
                [
                    (
                        'fix: add a `# /// script` header with requires-python = ">=3.14" '
                        "to every script"
                    )
                ],
            )
        ]
    highest = max(
        specs, key=lambda spec: max(version for _op, version in parse_requirement(spec))
    )
    running = ".".join(str(part) for part in tuple(version_info)[:3])
    detail = (
        f'Python {running} satisfies requires-python = "{highest}" '
        f"(highest of {len(specs)} scripts)"
    )
    if satisfies(version_info, parse_requirement(highest)):
        return [Check("Python for the scripts", "PASS", detail)]
    return [
        Check(
            "Python for the scripts",
            "FAIL",
            detail,
            [
                (
                    f'fix: install a Python that satisfies requires-python = "{highest}" '
                    "(e.g. uv python install 3.14), then restart Claude Code"
                )
            ],
        )
    ]


def check_git(which: Which, run: Run, project_root: Path) -> list[Check]:
    path = which("git")
    if path is None:
        return [
            Check(
                "git",
                "FAIL",
                "git not found on PATH",
                [
                    (
                        "fix: install git from https://git-scm.com/downloads "
                        "and restart Claude Code"
                    )
                ],
            )
        ]
    version = output_of(try_run(run, [path, "--version"])) or "unknown version"
    inside = try_run(
        run, ["git", "-C", str(project_root), "rev-parse", "--is-inside-work-tree"]
    )
    if inside is not None and inside.returncode == 0 and output_of(inside) == "true":
        worktrees = try_run(run, ["git", "-C", str(project_root), "worktree", "list"])
        if worktrees is None or worktrees.returncode != 0:
            return [
                Check(
                    "git",
                    "FAIL",
                    f"{version} at {path}, but git worktree list fails: "
                    "git without worktree support",
                    [
                        "fix: upgrade git to a version with worktree support (git >= 2.5)"
                    ],
                )
            ]
        return [Check("git", "PASS", f"{version} at {path}; worktree support ok")]
    return [Check("git", "PASS", f"{version} at {path}")]


# --- checks 4-6: the repository ---------------------------------------------------------


def check_repository(run: Run, project_root: Path) -> tuple[Check, str | None]:
    top = try_run(run, ["git", "-C", str(project_root), "rev-parse", "--show-toplevel"])
    if top is None or top.returncode != 0:
        return (
            Check(
                "repository",
                "FAIL",
                f"{project_root} is not inside a git work tree",
                ["fix: git init (and commit; see the commit check)"],
            ),
            None,
        )
    toplevel = output_of(top)
    return (Check("repository", "PASS", f"top level {toplevel}"), toplevel)


def check_commit(run: Run, project_root: Path) -> Check:
    head = try_run(
        run,
        ["git", "-C", str(project_root), "rev-parse", "--verify", "--quiet", "HEAD"],
    )
    if head is not None and head.returncode == 0:
        return Check("commit", "PASS", "HEAD resolves")
    return Check(
        "commit",
        "FAIL",
        "no commit yet: the worktree for each task needs one",
        ['fix: git commit --allow-empty -m "chore: initial commit"'],
    )


def check_working_tree(run: Run, project_root: Path) -> Check:
    status = try_run(
        run,
        [
            "git",
            "-C",
            str(project_root),
            "--no-optional-locks",
            "status",
            "--porcelain",
        ],
    )
    if status is None or status.returncode != 0:
        return Check(
            "working tree",
            "FAIL",
            "git status failed",
            ["fix: run git status in the repository and resolve the error"],
        )
    paths = []
    for line in output_of(status).splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip('"')
        if path == ".work" or path.startswith(".work/"):
            continue
        paths.append(path)
    if not paths:
        return Check("working tree", "PASS", "clean (ignoring .work/)")
    shown = ", ".join(paths[:5]) + (" ..." if len(paths) > 5 else "")
    return Check(
        "working tree",
        "WARN",
        f"{len(paths)} uncommitted change(s) outside .work/: {shown}",
        [
            (
                "fix: commit or stash them; task-run asks before starting when the root "
                "has uncommitted changes and no worktree is used"
            )
        ],
    )


# --- checks 7-11: the session and its files ---------------------------------------------


def same_dir(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return left.resolve() == right.resolve()


def check_plugin_source(
    project_root: Path, plugin_root: Path, tracked: list[str]
) -> Check:
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    try:
        version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    except (OSError, UnicodeDecodeError, ValueError, KeyError):
        return Check(
            "plugin source",
            "FAIL",
            f"cannot read the version from {manifest}",
            ["fix: repair .claude-plugin/plugin.json in the plugin root"],
        )
    working = None
    for rel in tracked:
        if not rel.endswith(".claude-plugin/plugin.json"):
            continue
        try:
            data = json.loads((project_root / rel).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if data.get("name") == "task-flow":
            working = (project_root / rel).parent.parent
            break
    if working is not None and not same_dir(working, plugin_root):
        return Check(
            "plugin source",
            "WARN",
            f"task-flow {version} from {plugin_root}, but this repository tracks its own "
            f"task-flow at {working}: the session runs the installed task-flow, not "
            "this working tree",
            [
                (
                    f'fix (PowerShell): $env:CLAUDE_CODE_PLUGIN_DIRS = "{working};'
                    '$env:CLAUDE_CODE_PLUGIN_DIRS" (entries separated by ;), '
                    "then restart Claude Code"
                ),
                (
                    f'fix (POSIX): export CLAUDE_CODE_PLUGIN_DIRS="{working}:'
                    '$CLAUDE_CODE_PLUGIN_DIRS", then restart Claude Code'
                ),
            ],
        )
    return Check("plugin source", "PASS", f"task-flow {version} at {plugin_root}")


def check_project_config(project_root: Path) -> Check:
    path = project_root / ".claude" / "task-flow.md"
    if not path.exists():
        return Check("project config", "PASS", "absent: profile defaults apply")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Check(
            "project config",
            "FAIL",
            f"{path} is not readable as UTF-8: {exc.__class__.__name__}",
            ["fix: make .claude/task-flow.md valid UTF-8 Markdown"],
        )
    return Check("project config", "PASS", f"readable, {len(text.splitlines())} lines")


def settings_paths(project_root: Path, home: Path) -> list[Path]:
    return [
        project_root / ".claude" / "settings.json",
        project_root / ".claude" / "settings.local.json",
        home / ".claude" / "settings.json",
    ]


def check_settings(paths: list[Path]) -> list[Check]:
    rows: list[Check] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            rows.append(
                Check(
                    f"settings {path}",
                    "FAIL",
                    f"{path} does not parse as JSON ({exc.__class__.__name__}): Claude "
                    "Code ignores a settings file it cannot parse completely",
                    [f"fix: repair the JSON in {path}"],
                )
            )
        else:
            rows.append(Check(f"settings {path}", "PASS", "parses as JSON"))
    return rows


def rule_present(rule: str, allow: list[str]) -> bool:
    return rule in allow or "Bash" in allow


def check_permissions(paths: list[Path]) -> Check:
    allow: list[str] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        permissions = data.get("permissions") if isinstance(data, dict) else None
        if isinstance(permissions, dict) and isinstance(permissions.get("allow"), list):
            allow += [rule for rule in permissions["allow"] if isinstance(rule, str)]
    missing = [rule for rule in REQUIRED_ALLOW if not rule_present(rule, allow)]
    if missing:
        return Check(
            "permission rules",
            "WARN",
            f"missing from permissions.allow: {', '.join(missing)}",
            [
                "fix: add to .claude/settings.json -> permissions.allow: "
                + json.dumps(missing)
            ],
        )
    return Check(
        "permission rules",
        "PASS",
        f"all {len(REQUIRED_ALLOW)} recommended allow rules present",
    )


def check_anthropic_key(env: dict) -> Check:
    value = env.get("ANTHROPIC_API_KEY")
    if value:
        return Check(
            "ANTHROPIC_API_KEY",
            "WARN",
            f"set ({len(value)} chars): Claude Code bills this session by token instead "
            "of the subscription",
            [
                (
                    "fix (PowerShell): Remove-Item Env:ANTHROPIC_API_KEY; "
                    '[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", $null, '
                    '"User"); restart Claude Code'
                ),
                (
                    "fix (POSIX): unset ANTHROPIC_API_KEY and remove it from your shell "
                    "profile; restart Claude Code"
                ),
            ],
        )
    return Check("ANTHROPIC_API_KEY", "PASS", "not set")


# --- checks 12-14: models.toml and the profiles ------------------------------------------


def check_models(project_root: Path) -> tuple[Check, dict | None, dict | None]:
    try:
        profiles, roles = models.load(project_root)
    except models.ConfigError as exc:
        return (
            Check(
                "models.toml",
                "FAIL",
                str(exc),
                [
                    (
                        "fix: edit the file and key path named in the detail; valid keys: "
                        "plugins/task-flow/templates/models.toml"
                    )
                ],
            ),
            None,
            None,
        )
    found = []
    user = models.user_file()
    if user.exists():
        found.append(f"user: {user}")
    project = models.project_file(project_root)
    if project.exists():
        found.append(f"project: {project}")
    detail = "; ".join(found) if found else "none: every role is Claude"
    return (Check("models.toml", "PASS", detail), profiles, roles)


def check_roles(resolved: dict) -> Check:
    parts = []
    for role in sorted(resolved):
        entry = resolved[role]
        value = entry["value"]
        suffix = " (profile)" if entry["kind"] == "profile" else ""
        parts.append(f"{role} = {value}{suffix}")
    return Check("roles", "PASS", ", ".join(parts))


def _prefix(name: str, is_primary: bool) -> str:
    return f"profile {name} ({'primary' if is_primary else 'fallback'})"


def key_check(
    name: str, profile: dict, is_primary: bool, env: dict, user_env_lookup
) -> Check:
    prefix = _prefix(name, is_primary)
    env_name = profile.get("api_key_env")
    if profile["billing"] == "local" and not env_name:
        return Check(f"{prefix}: key", "PASS", 'no key needed (billing = "local")')
    if not env_name:
        return Check(f"{prefix}: key", "PASS", "no api_key_env configured")
    value = env.get(env_name)
    if value:
        return Check(f"{prefix}: key", "PASS", f"`{env_name}` set ({len(value)} chars)")
    level = "FAIL" if is_primary else "WARN"
    if user_env_lookup(env_name) is not None:
        return Check(
            f"{prefix}: key",
            level,
            f"`{env_name}` is set for new processes but not in this session: Claude "
            "Code was started before it was set",
            ["fix: restart Claude Code so the session inherits the user environment"],
        )
    return Check(
        f"{prefix}: key",
        level,
        f"`{env_name}` is not set",
        [
            (
                f'fix (PowerShell): [Environment]::SetEnvironmentVariable("{env_name}", '
                '"<key>", "User"); then restart Claude Code'
            ),
            (
                f"fix (POSIX): export {env_name}=<key> in your shell profile; "
                "then restart Claude Code"
            ),
        ],
    )


def base_url_check(name: str, profile: dict, is_primary: bool) -> Check:
    prefix = _prefix(name, is_primary)
    url = profile["base_url"]
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return Check(
            f"{prefix}: base_url",
            "FAIL",
            f"{url!r} is not an http(s) URL with a host",
            [
                (
                    f"fix: set base_url in [profiles.{name}] to an http(s) URL, "
                    "e.g. https://api.mistral.ai/v1"
                )
            ],
        )
    warns = []
    if parts.query or parts.fragment:
        warns.append("it has a query or fragment")
    endpoint = DRIVER_CHECKS.get(profile["driver"], {}).get(
        "endpoint", "/chat/completions"
    )
    if parts.path.rstrip("/").endswith(endpoint):
        warns.append(f"it ends with {endpoint}, which the driver appends itself")
    if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
        warns.append("http to a non-local host: the key would travel unencrypted")
    if warns:
        return Check(
            f"{prefix}: base_url",
            "WARN",
            f"{url}: " + "; ".join(warns),
            [
                (
                    f"fix: set base_url in [profiles.{name}] to a plain https:// URL "
                    f"without the endpoint suffix ({endpoint})"
                )
            ],
        )
    return Check(f"{prefix}: base_url", "PASS", url)


def limits_check(name: str, profile: dict, is_primary: bool) -> Check:
    prefix = _prefix(name, is_primary)
    warns = []
    if profile["max_turns"] > MAX_TURNS_WARN:
        warns.append(f"max_turns = {profile['max_turns']} > {MAX_TURNS_WARN}")
    if profile["billing"] == "api" and profile["max_cost_usd"] is None:
        warns.append('billing = "api" but max_cost_usd is unset (no cost cap)')
    if (
        profile["billing"] == "api"
        and profile["price_input_per_mtok"] == 0
        and profile["price_cached_input_per_mtok"] == 0
        and profile["price_output_per_mtok"] == 0
    ):
        warns.append(
            "all price_* keys are 0: the cost reads 0 USD and max_cost_usd can never act"
        )
    if not warns:
        return Check(f"{prefix}: limits", "PASS", "ok")
    fixes = []
    if any("max_turns" in warn for warn in warns):
        fixes.append(f"fix: set max_turns <= {MAX_TURNS_WARN} in [profiles.{name}]")
    if any("max_cost_usd" in warn for warn in warns):
        fixes.append(f"fix: set max_cost_usd in [profiles.{name}] (the cost cap)")
    if any("price" in warn for warn in warns):
        fixes.append(f"fix: set the price_*_per_mtok keys in [profiles.{name}]")
    return Check(f"{prefix}: limits", "WARN", "; ".join(warns), fixes)


def check_implementer_profiles(
    profiles: dict,
    roles: dict,
    env: dict,
    user_env_lookup: Callable[[str], "str | None"],
) -> tuple[list[Check], list[tuple[str, dict]]]:
    """Rows for the primary implementer profile and each of its fallbacks.

    Also returns the (name, profile) pairs that are enabled and have their key (or need
    none): the candidates for the online check.
    """
    rows: list[Check] = []
    targets: list[tuple[str, dict]] = []
    name = roles.get("implementer")
    if not name or name not in profiles:
        return rows, targets
    primary = profiles[name]
    chain = [(name, primary, True)]
    chain += [(fallback, profiles[fallback], False) for fallback in primary["fallback"]]
    for profile_name, profile, is_primary in chain:
        prefix = _prefix(profile_name, is_primary)
        if not profile["enabled"]:
            if is_primary:
                rows.append(
                    Check(
                        f"{prefix}: enabled",
                        "FAIL",
                        "enabled = false: implement.py refuses a disabled primary profile",
                        [
                            (
                                f"fix: set enabled = true in [profiles.{profile_name}] or "
                                "point [roles] implementer at another profile"
                            )
                        ],
                    )
                )
            else:
                rows.append(
                    Check(
                        f"{prefix}: enabled",
                        "WARN",
                        "enabled = false: the fallback is skipped",
                        [
                            (
                                f'fix: remove "{profile_name}" from the primary\'s fallback '
                                f"or set enabled = true in [profiles.{profile_name}]"
                            )
                        ],
                    )
                )
        else:
            rows.append(Check(f"{prefix}: enabled", "PASS", "enabled"))
        rows.append(key_check(profile_name, profile, is_primary, env, user_env_lookup))
        rows.append(base_url_check(profile_name, profile, is_primary))
        rows.append(limits_check(profile_name, profile, is_primary))
        driver = profile["driver"]
        if driver in DRIVER_CHECKS:
            rows.append(
                Check(
                    f"{prefix}: driver",
                    "PASS",
                    f"{driver}: online check is {DRIVER_CHECKS[driver]['online']}",
                )
            )
        else:
            rows.append(
                Check(
                    f"{prefix}: driver",
                    "WARN",
                    f"doctor has no checks for driver {driver}",
                    [
                        (
                            f"fix: add an entry for driver {driver!r} to DRIVER_CHECKS "
                            "in scripts/doctor.py"
                        )
                    ],
                )
            )
        if profile["enabled"] and (
            not profile["api_key_env"] or env.get(profile["api_key_env"])
        ):
            targets.append((profile_name, profile))
    return rows, targets


# --- the online check -------------------------------------------------------------------


def check_online(name: str, profile: dict, env: dict, http_timeout: float) -> Check:
    base = profile["base_url"].rstrip("/")
    url = base + "/models"
    row_name = f"online {name}: model list"
    driver = DRIVER_CHECKS.get(profile["driver"], {})
    headers = dict(driver.get("extra_headers", {}))
    key = env.get(profile["api_key_env"]) if profile.get("api_key_env") else None
    if key:
        if driver.get("auth") == "x-api-key":
            headers["x-api-key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=http_timeout) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        ms = round((time.monotonic() - started) * 1000)
        if exc.code in (401, 403):
            return Check(
                row_name,
                "FAIL",
                f"HTTP {exc.code} in {ms} ms: key rejected",
                [
                    (
                        f"fix: check the key held in {profile.get('api_key_env')} for "
                        f"[profiles.{name}]"
                    )
                ],
            )
        if exc.code == 404:
            return Check(
                row_name,
                "WARN",
                f"HTTP 404 in {ms} ms: no model list at this URL: reachable, key not proven",
                [f"fix: check base_url in [profiles.{name}]: {url} has no /models"],
            )
        return Check(
            row_name,
            "FAIL",
            f"HTTP {exc.code} in {ms} ms",
            [f"fix: check base_url in [profiles.{name}]: {url}"],
        )
    except (urllib.error.URLError, OSError) as exc:
        ms = round((time.monotonic() - started) * 1000)
        reason = getattr(exc, "reason", exc)
        return Check(
            row_name,
            "FAIL",
            f"{exc.__class__.__name__} after {ms} ms: {reason}",
            [
                f"fix: check base_url, network and proxy settings for [profiles.{name}]: {url}"
            ],
        )
    ms = round((time.monotonic() - started) * 1000)
    if status != 200:
        return Check(
            row_name,
            "FAIL",
            f"HTTP {status} in {ms} ms",
            [f"fix: {url} returned {status}"],
        )
    try:
        payload = json.loads(body.decode("utf-8"))
        entries = payload["data"] if isinstance(payload, dict) else None
        ids = (
            [
                e["id"]
                for e in entries
                if isinstance(e, dict) and isinstance(e.get("id"), str)
            ]
            if isinstance(entries, list)
            else []
        )
    except (ValueError, UnicodeDecodeError, TypeError, KeyError):
        return Check(
            row_name,
            "WARN",
            f"HTTP 200 in {ms} ms but the body has no JSON model list (data[*].id)",
            [f"fix: check base_url in [profiles.{name}]: {url}"],
        )
    model = profile["model"]
    if model in ids:
        return Check(row_name, "PASS", f"HTTP 200 in {ms} ms; model `{model}` listed")
    shown = (
        difflib.get_close_matches(model, ids, n=MAX_SHOWN_IDS)
        or sorted(ids)[:MAX_SHOWN_IDS]
    )
    return Check(
        row_name,
        "WARN",
        f"HTTP 200 in {ms} ms; model `{model}` not among the {len(ids)} listed ids; "
        f"closest: {', '.join(shown)}",
        [
            (
                f"fix: set `model` in [profiles.{name}] to one of the ids from GET {url} "
                f"(shown: {', '.join(shown)})"
            )
        ],
    )


# --- redaction and rendering --------------------------------------------------------------


def secret_values(env: dict, extra_names: list) -> list[str]:
    """The values that must never be printed: key-looking variables and api_key_env names."""
    names = {"ANTHROPIC_API_KEY"}
    for name in extra_names:
        if name:
            names.add(name)
    for var, value in env.items():
        if not value:
            continue
        if any(token in SECRET_NAME_TOKENS for token in var.upper().split("_")):
            names.add(var)
    values = {env[name] for name in names if env.get(name)}
    return sorted(values, key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    for value in secrets:
        if len(value) >= MIN_REDACT_LENGTH:
            text = text.replace(value, REDACTED)
    return text


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render(checks: list[Check], secrets: list[str]) -> str:
    lines = ["# task-flow doctor", "| # | Check | Result | Detail |"]
    for number, check in enumerate(checks, 1):
        lines.append(
            f"| {number} | {cell(check.name)} | {check.result} | {cell(check.detail)} |"
        )
    lines.append("## Fixes")
    fixes = [
        f"{number}. {check.name}: {fix}"
        for number, check in enumerate(checks, 1)
        if check.result in ("WARN", "FAIL")
        for fix in check.fixes
    ]
    lines.extend(fixes or ["none"])
    counts = {
        result: sum(1 for c in checks if c.result == result)
        for result in ("PASS", "WARN", "FAIL")
    }
    lines.append("## Summary")
    lines.append(f"{counts['PASS']} PASS, {counts['WARN']} WARN, {counts['FAIL']} FAIL")
    return redact("\n".join(lines) + "\n", secrets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doctor.py",
        description="Check that this machine, session and repository are ready for task-flow.",
    )
    parser.add_argument(
        "--project", default=".", help="repository to check (default: .)"
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="also GET <base_url>/models once per profile in use (no tokens)",
    )
    args = parser.parse_args(argv)
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    project = Path(args.project).resolve()
    env = dict(os.environ)
    run = make_run()
    which = shutil.which
    plugin_root = Path(__file__).resolve().parents[1]
    home = Path.home()

    checks: list[Check] = []
    checks += check_uv(which, run)
    checks += check_python(plugin_root / "scripts", sys.version_info)
    checks += check_git(which, run, project)
    repo_check, toplevel = check_repository(run, project)
    checks.append(repo_check)
    tracked: list[str] = []
    if toplevel is not None:
        checks.append(check_commit(run, project))
        checks.append(check_working_tree(run, project))
        listed = try_run(run, ["git", "-C", str(project), "ls-files", "-z"])
        if listed is not None and listed.returncode == 0:
            tracked = [
                p for p in listed.stdout.decode("utf-8", "replace").split("\0") if p
            ]
    checks.append(check_plugin_source(project, plugin_root, tracked))
    checks.append(check_project_config(project))
    settings = settings_paths(project, home)
    checks += check_settings(settings)
    checks.append(check_permissions(settings))
    checks.append(check_anthropic_key(env))
    models_check, profiles, roles = check_models(project)
    checks.append(models_check)
    if profiles is not None and roles is not None:
        checks.append(check_roles(models.resolve_roles(project)))
        rows, targets = check_implementer_profiles(
            profiles, roles, env, windows_user_env
        )
        checks += rows
        if args.online:
            for profile_name, profile in targets:
                timeout = min(
                    profile["request_timeout_seconds"], ONLINE_TIMEOUT_CAP_SECONDS
                )
                checks.append(check_online(profile_name, profile, env, timeout))
    secrets = secret_values(
        env, [profile.get("api_key_env") for profile in (profiles or {}).values()]
    )
    print(render(checks, secrets), end="")
    return 1 if any(check.result == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
