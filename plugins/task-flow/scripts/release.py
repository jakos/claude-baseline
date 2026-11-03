# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Compute and apply a task-flow release: one target, one approval, script-decided steps.

Usage (from the repository root; stdlib only, started by `uv run --script`):

  release.py targets [--root DIR] [--config PATH]
  release.py check --target T [<version> | --major | --minor | --patch] [--since REF] [--date YYYY-MM-DD]
  release.py verify --target T
  release.py apply --target T [<version> | flags] [--since REF] [--date D] [--entry-file PATH] [--dry-run]

`--root` defaults to "." and `--config` to `<root>/.claude/task-flow.release.toml`.
`--target` is optional when the config has exactly one target. Exit codes: 0 ok,
1 a refusal or a failed check (nothing changed), 2 a config or usage error.

Decisions, in short form:

1. The config is a TOML file (default `.claude/task-flow.release.toml`, override with
   `--config`), parsed strictly with tomllib; unknown keys anywhere are a config error.
2. Schema per `[targets.<name>]`: `tag` (required, contains `{version}` exactly once),
   `changelog` (required, repo-relative), `scope` (required, non-empty), `branches`
   (default `["main"]`), `checks` (default `[]`, each an argument list, so no shell
   quoting on any OS) and at least one `[[targets.<name>.version_files]]` with `path`,
   `format` (`"json"` | `"toml"`) and `selector`.
3. Selector: dot-separated segments, each `key` or `key[field=value]`; the filter form
   picks the one element of the list `key` whose `field` equals `value`, and exactly one
   element must match. TOML files allow only plain `key` segments (line replacement).
4. Version strings are strict `MAJOR.MINOR.PATCH`, compared as integer tuples.
5. The base of the commit range is the highest-versioned tag matching the target's tag
   pattern, or `--since <ref>`; with neither, `check` refuses with `no-base` and asks for
   `--since`, so the first release states its starting point once, explicitly.
6. Version inference (no explicit version, no flag): a breaking change (`!` after the
   type or scope, or `BREAKING CHANGE:` / `BREAKING-CHANGE:` in the body) gives major,
   any `feat` gives minor, anything else patch. Below 1.0.0 a breaking change gives
   minor, never an automatic 1.0.0; `--major` on 0.x gives 1.0.0. An explicit version
   is taken as given and must be higher than the current one.
7. Files are read with `read_bytes().decode("utf-8")` and written with
   `write_bytes(text.encode("utf-8"))`, so line endings are never translated. JSON goes
   through the json module (detected indent, key order kept, `\r\n` kept); TOML is a
   single-line replacement that must match exactly one line, else error `toml-replace`.
8. The changelog entry groups commits (Breaking changes / Features / Fixes / Other, the
   order given, newest first) and is prepended after a `# ` title line, or creates
   `# Changelog` when the file does not exist.
9. `check` collects every failure and prints one JSON object (indent 2); `verify` runs
   each check as an argument list, under the `checker` role's command rules from
   `rules.py`; `apply` re-runs the check logic, computes every new file text in memory
   (any error there is exit 2 with nothing written) and only then writes the files,
   `git add -- <files>`, `git commit -m "chore(release): <target> <version>"` and
   `git tag -a <tag> -m "<target> <version>"`. `apply` never leaves the machine, never
   deletes or moves a tag and never rewinds history.

Stdlib only, so it runs on macOS, Linux and Windows.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from rules import check_command

DEFAULT_CONFIG = (".claude", "task-flow.release.toml")
TARGET_KEYS = {"tag", "changelog", "scope", "branches", "checks", "version_files"}
VERSION_FILE_KEYS = {"path", "format", "selector"}
FORMATS = {"json", "toml"}
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
CONVENTIONAL_RE = re.compile(r"^(\w+)(\([^)]*\))?(!)?: ")
SEGMENT_RE = re.compile(r"^([^\[\]]+)(?:\[([^\[\]=]+)=([^\[\]]+)\])?$")
IN_FLIGHT_PHASES = {"implementing", "checking", "reviewing"}
ENTRY_GROUPS = (
    ("Breaking changes", lambda c: c.breaking),
    ("Features", lambda c: not c.breaking and c.type == "feat"),
    ("Fixes", lambda c: not c.breaking and c.type == "fix"),
    ("Other", lambda c: not c.breaking and c.type not in ("feat", "fix")),
)


class ConfigError(Exception):
    """A configuration or usage problem; the message names the file and the key path."""


# --- versions and commits -------------------------------------------------


def parse_version(text: str) -> tuple[int, int, int]:
    """Parse a strict MAJOR.MINOR.PATCH string into an integer tuple."""
    if not isinstance(text, str) or not VERSION_RE.match(text):
        raise ValueError(f"not a MAJOR.MINOR.PATCH version: {text!r}")
    return tuple(int(part) for part in text.split("."))  # type: ignore[return-value]


def bump(version: str, kind: str) -> str:
    """Bump `version` by `kind` ("major" | "minor" | "patch")."""
    major, minor, patch = parse_version(version)
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown bump kind: {kind!r}")


@dataclass
class Commit:
    sha: str
    subject: str
    type: str
    breaking: bool


def parse_commit(sha: str, subject: str, body: str) -> Commit:
    """Classify one commit: conventional type, and whether it is breaking."""
    match = CONVENTIONAL_RE.match(subject)
    kind = match.group(1) if match else "other"
    breaking = bool(match and match.group(3)) or (
        "BREAKING CHANGE:" in body or "BREAKING-CHANGE:" in body
    )
    return Commit(sha=sha, subject=subject, type=kind, breaking=breaking)


def infer_bump(current: str, commits: list[Commit]) -> str:
    """major on breaking (minor below 1.0.0), minor on any feat, else patch."""
    if any(c.breaking for c in commits):
        return "minor" if parse_version(current)[0] == 0 else "major"
    if any(c.type == "feat" for c in commits):
        return "minor"
    return "patch"


def propose_version(
    current: str,
    commits: list[Commit],
    explicit: str | None = None,
    kind: str | None = None,
) -> tuple[str, str]:
    """Return (version, bump label): explicit, a flag, or inferred from the commits."""
    if explicit is not None:
        parse_version(explicit)
        return explicit, "explicit"
    if kind is not None:
        return bump(current, kind), kind
    inferred = infer_bump(current, commits)
    return bump(current, inferred), inferred


# --- selectors ------------------------------------------------------------


def _parse_segment(segment: str) -> tuple[str, tuple[str, str] | None]:
    match = SEGMENT_RE.match(segment)
    if match is None or not match.group(1):
        raise ValueError(f"bad selector segment {segment!r}")
    if match.group(2) is None:
        return match.group(1), None
    return match.group(1), (match.group(2), match.group(3))


def _descend(current, key: str, filt: tuple[str, str] | None, selector: str):
    if not isinstance(current, dict) or key not in current:
        raise ValueError(f"selector {selector!r}: no key {key!r}")
    value = current[key]
    if filt is None:
        return value
    field, expected = filt
    if not isinstance(value, list):
        raise TypeError(f"selector {selector!r}: {key!r} is not a list")
    matches = [
        element
        for element in value
        if isinstance(element, dict) and str(element.get(field)) == expected
    ]
    if len(matches) != 1:
        raise ValueError(
            f"selector {selector!r}: segment {key}[{field}={expected}] matches "
            f"{len(matches)} elements, exactly one required"
        )
    return matches[0]


def get_selector(data, selector: str):
    """Read the value at `selector` (dot-separated, `key` or `key[field=value]`)."""
    current = data
    for segment in selector.split("."):
        key, filt = _parse_segment(segment)
        current = _descend(current, key, filt, selector)
    return current


def set_selector(data, selector: str, value) -> None:
    """Set the value at `selector`; the last segment must be a plain key."""
    parsed = [_parse_segment(segment) for segment in selector.split(".")]
    current = data
    for key, filt in parsed[:-1]:
        current = _descend(current, key, filt, selector)
    key, filt = parsed[-1]
    if filt is not None:
        raise ValueError(f"cannot set through a filter segment: {selector!r}")
    if not isinstance(current, dict) or key not in current:
        raise ValueError(f"selector {selector!r}: no key {key!r}")
    current[key] = value


# --- version files --------------------------------------------------------


def read_version(root: Path, vf: dict) -> str:
    """Read the current version from one version file under `root`."""
    path = Path(root) / vf["path"]
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as exc:
        raise ValueError(f"{vf['path']}: cannot read: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"{vf['path']}: not valid UTF-8: {exc}") from exc
    if vf["format"] == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{vf['path']}: invalid JSON: {exc}") from exc
    else:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"{vf['path']}: invalid TOML: {exc}") from exc
    value = get_selector(data, vf["selector"])
    if not isinstance(value, str):
        raise TypeError(f"{vf['path']}: selector {vf['selector']!r} is not a string")
    return value


def detect_indent(text: str) -> int:
    """Leading spaces of the first indented line, default 2."""
    for line in text.splitlines():
        if line.startswith(" "):
            return len(line) - len(line.lstrip(" "))
    return 2


def _header_index(lines: list[str], header: str) -> int | None:
    for index, line in enumerate(lines):
        if line.strip() == header:
            return index
    return None


def _first_header(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if line.startswith("["):
            return index
    return len(lines)


def rewrite_version(text: str, vf: dict, old: str, new: str) -> str:
    """Return `text` with the selected version value changed from `old` to `new`."""
    selector = vf["selector"]
    if vf["format"] == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{vf['path']}: invalid JSON: {exc}") from exc
        if get_selector(data, selector) != old:
            raise ValueError(
                f"{vf['path']}: selector holds a different value than {old!r}"
            )
        set_selector(data, selector, new)
        out = json.dumps(data, indent=detect_indent(text), ensure_ascii=False)
        if "\r\n" in text:
            out = out.replace("\n", "\r\n") + "\r\n"
        else:
            out += "\n"
        return out
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{vf['path']}: invalid TOML: {exc}") from exc
    if get_selector(data, selector) != old:
        raise ValueError(f"{vf['path']}: selector holds a different value than {old!r}")
    segments = selector.split(".")
    if any("[" in segment for segment in segments):
        raise ValueError(
            f"{vf['path']}: TOML selectors allow only plain key segments, got {selector!r}"
        )
    key = segments[-1]
    header = "[" + ".".join(segments[:-1]) + "]" if len(segments) > 1 else None
    lines = text.splitlines(keepends=True)
    if header is None:
        start, end = 0, _first_header(lines)
    else:
        index = _header_index(lines, header)
        if index is None:
            raise ValueError(f"{vf['path']}: toml-replace: no table header {header}")
        start = index + 1
        end = start
        while end < len(lines) and not lines[end].startswith("["):
            end += 1
    pattern = re.compile(
        r"^(\s*" + re.escape(key) + r"\s*=\s*)([\"'])" + re.escape(old) + r"\2"
    )
    matches = []
    for index in range(start, end):
        match = pattern.match(lines[index].rstrip("\r\n"))
        if match:
            matches.append((index, match))
    if len(matches) != 1:
        where = header or "the top of the file"
        raise ValueError(
            f"{vf['path']}: toml-replace: {key!r} = {old!r} matched {len(matches)} "
            f"lines in {where}, exactly one required"
        )
    index, match = matches[0]
    line = lines[index]
    ending = "\r\n" if line.endswith("\r\n") else "\n"
    body = line.rstrip("\r\n")
    lines[index] = (
        match.group(1) + match.group(2) + new + match.group(2) + body[match.end() :]
    )
    lines[index] += ending
    return "".join(lines)


# --- changelog ------------------------------------------------------------


def render_entry(version: str, day: str, commits: list[Commit]) -> str:
    """Render one changelog entry; empty groups are left out, newest commit first."""
    parts = [f"## {version} - {day}"]
    for title, keep in ENTRY_GROUPS:
        group = [commit for commit in commits if keep(commit)]
        if not group:
            continue
        parts.append(f"### {title}")
        parts.extend(f"- {commit.subject} ({commit.sha[:7]})" for commit in group)
    return "\n\n".join(parts) + "\n"


def prepend_entry(existing: str | None, entry: str) -> str:
    """Prepend `entry` to the changelog text `existing` (None: create the file)."""
    if existing is None:
        return "# Changelog\n\n" + entry
    first_line = existing.split("\n", 1)[0]
    if first_line.startswith("# "):
        rest = existing[len(first_line) + 1 :].lstrip("\r\n")
        if rest:
            return f"{first_line}\n\n{entry}\n{rest}"
        return f"{first_line}\n\n{entry}"
    return entry + "\n" + existing


# --- git helpers ----------------------------------------------------------


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run one git command in `root` as an argument list (never a shell)."""
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, encoding="utf-8", check=False
    )


def current_branch(root: Path) -> str:
    result = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else "?"


def tag_regex(pattern: str) -> re.Pattern:
    parts = pattern.split("{version}")
    return re.compile("^" + r"(\d+\.\d+\.\d+)".join(re.escape(p) for p in parts) + "$")


def last_tag(root: Path, pattern: str) -> tuple[str, str] | None:
    """The highest-versioned tag matching the tag pattern, as (tag, version)."""
    result = git(root, "tag", "--list", pattern.replace("{version}", "*"))
    if result.returncode != 0:
        return None
    regex = tag_regex(pattern)
    best: tuple[str, str] | None = None
    for tag in result.stdout.splitlines():
        tag = tag.strip()
        match = regex.match(tag)
        if match and (
            best is None or parse_version(match.group(1)) > parse_version(best[1])
        ):
            best = (tag, match.group(1))
    return best


def commits_since(root: Path, base: str, scope: list[str]) -> list[Commit]:
    """Conventional commits in base..HEAD touching the scope, newest first."""
    result = git(
        root,
        "log",
        "--no-merges",
        f"{base}..HEAD",
        "--format=%H%x1f%s%x1f%b%x1e",
        "--",
        *scope,
    )
    if result.returncode != 0:
        return []
    commits = []
    for record in result.stdout.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split("\x1f")
        if len(parts) < 3:
            continue
        sha, subject = parts[0], parts[1]
        body = "\x1f".join(parts[2:])
        commits.append(parse_commit(sha, subject, body))
    return commits


def tasks_in_flight(root: Path) -> list[str]:
    """Ids of .work/<id>/ tasks whose phase is implementing, checking or reviewing."""
    work = Path(root) / ".work"
    ids = []
    if work.is_dir():
        for status in sorted(work.glob("*/status.md")):
            try:
                text = status.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line in text.splitlines():
                if line.startswith("- phase:"):
                    phase = line.partition(":")[2].strip()
                    if phase in IN_FLIGHT_PHASES:
                        ids.append(status.parent.name)
                    break
    return ids


# --- config ---------------------------------------------------------------


def _require_str(value, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{what}: must be a non-empty string")
    return value


def _require_rel_path(value, what: str) -> str:
    _require_str(value, what)
    if Path(value).is_absolute() or ".." in Path(value).parts:
        raise ConfigError(f"{what}: must be a repo-relative path, got {value!r}")
    return value


def _require_str_list(value, what: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ConfigError(f"{what}: must be a non-empty list of non-empty strings")
    return list(value)


def _validate_target(path: Path, name: str, table) -> dict:
    where = f"{path}: targets.{name}"
    if not isinstance(table, dict):
        raise ConfigError(f"{where}: must be a table")
    unknown = sorted(set(table) - TARGET_KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {unknown}")
    for key in ("tag", "changelog", "scope", "version_files"):
        if key not in table:
            raise ConfigError(f"{where}: required key {key!r} is missing")
    tag = _require_str(table["tag"], f"{where}.tag")
    if tag.count("{version}") != 1:
        raise ConfigError(
            f"{where}.tag: must contain {{version}} exactly once, got {tag!r}"
        )
    changelog = _require_rel_path(table["changelog"], f"{where}.changelog")
    scope = [
        _require_rel_path(item, f"{where}.scope")
        for item in _require_str_list(table["scope"], f"{where}.scope")
    ]
    branches = _require_str_list(table.get("branches", ["main"]), f"{where}.branches")
    checks_raw = table.get("checks", [])
    if not isinstance(checks_raw, list) or not all(
        isinstance(check, list)
        and check
        and all(isinstance(arg, str) and arg for arg in check)
        for check in checks_raw
    ):
        raise ConfigError(f"{where}.checks: must be a list of non-empty argument lists")
    version_files_raw = table["version_files"]
    if not isinstance(version_files_raw, list) or not version_files_raw:
        raise ConfigError(f"{where}.version_files: must be a non-empty list")
    version_files = []
    for index, vf in enumerate(version_files_raw):
        vwhere = f"{where}.version_files[{index}]"
        if not isinstance(vf, dict):
            raise ConfigError(f"{vwhere}: must be a table")
        unknown = sorted(set(vf) - VERSION_FILE_KEYS)
        if unknown:
            raise ConfigError(f"{vwhere}: unknown key(s) {unknown}")
        for key in VERSION_FILE_KEYS:
            if key not in vf:
                raise ConfigError(f"{vwhere}: required key {key!r} is missing")
        vf_path = _require_rel_path(vf["path"], f"{vwhere}.path")
        fmt = vf["format"]
        if fmt not in FORMATS:
            raise ConfigError(f'{vwhere}.format: must be "json" or "toml", got {fmt!r}')
        selector = _require_str(vf["selector"], f"{vwhere}.selector")
        try:
            segments = [_parse_segment(segment) for segment in selector.split(".")]
        except ValueError as exc:
            raise ConfigError(f"{vwhere}.selector: {exc}") from exc
        if fmt == "toml" and any(filt is not None for _key, filt in segments):
            raise ConfigError(
                f"{vwhere}.selector: TOML selectors allow only plain key segments, "
                f"got {selector!r}"
            )
        version_files.append({"path": vf_path, "format": fmt, "selector": selector})
    return {
        "tag": tag,
        "changelog": changelog,
        "scope": scope,
        "branches": branches,
        "checks": [list(check) for check in checks_raw],
        "version_files": version_files,
    }


def load_config(root: Path, path: Path | None) -> dict:
    """Load and strictly validate the release config; defaults are filled in."""
    if path is None:
        path = Path(root).joinpath(*DEFAULT_CONFIG)
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ConfigError(f"{path}: no release config; pass --config <path>") from None
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: not valid UTF-8: {exc}") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    unknown = sorted(set(data) - {"targets"})
    if unknown:
        raise ConfigError(f"{path}: unknown top-level key(s) {unknown}")
    targets = data.get("targets", {})
    if not isinstance(targets, dict) or not targets:
        raise ConfigError(f"{path}: targets: must be a non-empty table")
    return {
        "targets": {
            name: _validate_target(path, name, table) for name, table in targets.items()
        }
    }


def pick_target(config: dict, name: str | None) -> tuple[str, dict]:
    """Resolve the target name; with one target and no name, that target is used."""
    targets = config["targets"]
    if name is None:
        if len(targets) == 1:
            name = next(iter(targets))
        else:
            raise ConfigError(f"several targets, pass --target: {', '.join(targets)}")
    if name not in targets:
        raise ConfigError(f"unknown target {name!r} (known: {', '.join(targets)})")
    return name, targets[name]


# --- check / verify / apply -----------------------------------------------


def evaluate(
    root: Path,
    name: str,
    target: dict,
    explicit: str | None,
    kind: str | None,
    since: str | None,
    day: str,
) -> dict:
    """Evaluate every precondition; collect all failures without stopping at the first."""
    failures: list[dict] = []
    branch = current_branch(root)
    if branch not in target["branches"]:
        failures.append(
            {
                "code": "branch",
                "message": f"branch {branch!r} is not one of {target['branches']}",
            }
        )
    result = git(root, "status", "--porcelain")
    if result.returncode != 0:
        failures.append(
            {"code": "dirty", "message": f"git status failed: {result.stderr.strip()}"}
        )
    elif result.stdout.strip():
        failures.append(
            {
                "code": "dirty",
                "message": "the working tree is dirty (git status --porcelain)",
            }
        )
    base = None
    if since:
        check = git(root, "rev-parse", "-q", "--verify", f"{since}^{{commit}}")
        if check.returncode != 0:
            failures.append(
                {
                    "code": "bad-since",
                    "message": f"--since {since!r} is not a commit: {check.stderr.strip()}",
                }
            )
        else:
            base = since
    else:
        found = last_tag(root, target["tag"])
        if found is None:
            failures.append(
                {
                    "code": "no-base",
                    "message": (
                        f"no tag matches {target['tag']!r} and no --since was given; "
                        "pass --since <ref> to name the starting point of the first release"
                    ),
                }
            )
        else:
            base = found[0]
    commits: list[Commit] = []
    if base is not None:
        commits = commits_since(root, base, target["scope"])
        if not commits:
            failures.append(
                {
                    "code": "no-commits",
                    "message": f"no commits in {base}..HEAD touching {target['scope']}",
                }
            )
    versions: list[str] = []
    read_error: str | None = None
    for vf in target["version_files"]:
        try:
            versions.append(read_version(root, vf))
        except (ValueError, TypeError) as exc:
            read_error = str(exc)
    current: str | None = None
    if read_error is not None:
        failures.append({"code": "bad-version", "message": read_error})
    elif len(set(versions)) > 1:
        current = "disagree"
        pairs = [
            f"{vf['path']}={v}" for vf, v in zip(target["version_files"], versions)
        ]
        failures.append(
            {
                "code": "versions-disagree",
                "message": f"version files disagree: {', '.join(pairs)}",
            }
        )
    else:
        current = versions[0]
        try:
            parse_version(current)
        except ValueError:
            failures.append(
                {
                    "code": "bad-version",
                    "message": f"current version {current!r} is not MAJOR.MINOR.PATCH",
                }
            )
    proposal_base = (
        current
        if current not in (None, "disagree")
        else (versions[0] if versions else None)
    )
    version: str | None = None
    bump_label: str | None = None
    if proposal_base is not None:
        try:
            version, bump_label = propose_version(
                proposal_base, commits, explicit=explicit, kind=kind
            )
        except ValueError as exc:
            failures.append(
                {"code": "bad-version", "message": f"cannot propose a version: {exc}"}
            )
    if (
        version is not None
        and explicit is not None
        and current not in (None, "disagree")
    ):
        try:
            not_higher = parse_version(version) <= parse_version(current)
        except ValueError:
            not_higher = False
        if not_higher:
            failures.append(
                {
                    "code": "not-higher",
                    "message": (
                        f"explicit version {version} is not higher than the current {current}"
                    ),
                }
            )
    tag = target["tag"].replace("{version}", version) if version is not None else None
    if (
        tag is not None
        and git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode == 0
    ):
        failures.append({"code": "tag-exists", "message": f"tag {tag} already exists"})
    in_flight = tasks_in_flight(root)
    if in_flight:
        failures.append(
            {
                "code": "task-in-flight",
                "message": f"task-flow tasks in flight: {', '.join(in_flight)}",
            }
        )
    files = [vf["path"] for vf in target["version_files"]] + [target["changelog"]]
    return {
        "ok": not failures,
        "target": name,
        "branch": branch,
        "current": current,
        "version": version,
        "bump": bump_label,
        "base": base,
        "commits": len(commits),
        "tag": tag,
        "files": files,
        "changelog": target["changelog"],
        "commit_message": f"chore(release): {name} {version}"
        if version is not None
        else None,
        "entry": render_entry(version, day, commits) if version is not None else None,
        "failures": failures,
        "_commits": commits,
        "_date": day,
    }


def today() -> str:
    """Today's date as YYYY-MM-DD (UTC)."""
    return datetime.now(UTC).date().isoformat()


def _bump_kind(args) -> str | None:
    if args.major:
        return "major"
    if args.minor:
        return "minor"
    if args.patch:
        return "patch"
    return None


def run_check(root: Path, name: str, target: dict, args) -> dict:
    """Evaluate the preconditions and return the JSON report (see `check`)."""
    day = args.date or today()
    state = evaluate(
        root, name, target, args.version, _bump_kind(args), args.since, day
    )
    return {key: value for key, value in state.items() if not key.startswith("_")}


def run_verify(root: Path, target: dict) -> int:
    """Run every configured check as an argument list; print one line per check."""
    passed = 0
    failed = 0
    for argv in target["checks"]:
        command = " ".join(argv)
        executable = shutil.which(argv[0])
        if executable is None:
            print(f"ERROR {command}: {argv[0]} not found on PATH")
            failed += 1
            continue
        reason = check_command(command, "checker")
        if reason is not None:
            print(f"DENIED {command}: {reason}")
            failed += 1
            continue
        result = subprocess.run(
            [executable, *argv[1:]],
            cwd=root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0:
            print(f"PASS {command}")
            passed += 1
        else:
            print(f"FAIL {command} (exit {result.returncode})")
            output = (result.stdout or "") + (result.stderr or "")
            for line in output.splitlines()[-30:]:
                print(f"    {line}")
            failed += 1
    if failed:
        print(f"failed: {failed} of {len(target['checks'])} checks")
        return 1
    print(f"ok: {passed} checks passed")
    return 0


def run_apply(root: Path, name: str, target: dict, args) -> int:
    """Re-run the check, then write the files, commit and tag — or dry-run."""
    day = args.date or today()
    state = evaluate(
        root, name, target, args.version, _bump_kind(args), args.since, day
    )
    if not state["ok"]:
        for failure in state["failures"]:
            print(f"refused: {failure['code']}: {failure['message']}")
        return 1
    version = state["version"]
    current = state["current"]
    tag = state["tag"]
    message = state["commit_message"]
    try:
        writes = []
        for vf in target["version_files"]:
            text = (root / vf["path"]).read_bytes().decode("utf-8")
            writes.append((vf["path"], rewrite_version(text, vf, current, version)))
        if args.entry_file:
            entry_text = Path(args.entry_file).read_bytes().decode("utf-8")
            entry = entry_text if entry_text.endswith("\n") else entry_text + "\n"
        else:
            entry = render_entry(version, state["_date"], state["_commits"])
        changelog_rel = target["changelog"]
        changelog_path = root / changelog_rel
        existing = (
            changelog_path.read_bytes().decode("utf-8")
            if changelog_path.exists()
            else None
        )
        changelog_text = prepend_entry(existing, entry)
    except (OSError, ValueError, TypeError) as exc:
        print(f"release.py: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        for rel, _text in writes:
            print(f"would write: {rel}: {current} -> {version}")
        action = "create" if existing is None else "prepend"
        print(f"would write: {changelog_rel}: {action}")
        print(f"would commit: {message}")
        print(f"would tag: {tag} (annotated)")
        print("ok: dry run, nothing changed")
        return 0
    files = [rel for rel, _text in writes] + [changelog_rel]
    for rel, text in writes + [(changelog_rel, changelog_text)]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    result = git(root, "add", "--", *files)
    if result.returncode != 0:
        print(f"failed: add: {result.stderr.strip()}")
        return 1
    result = git(root, "commit", "-m", message)
    if result.returncode != 0:
        print(f"failed: commit: {result.stderr.strip()}")
        return 1
    result = git(root, "tag", "-a", tag, "-m", f"{name} {version}")
    if result.returncode != 0:
        print(f"failed: tag: {result.stderr.strip()}")
        return 1
    sha = git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    print(f"ok: {sha} {message}; tag {tag}")
    return 0


def run_targets(root: Path, config: dict) -> int:
    """One line per target: `<name> <current version> <tag pattern>`."""
    for name, target in config["targets"].items():
        versions = []
        for vf in target["version_files"]:
            try:
                versions.append(read_version(root, vf))
            except (ValueError, TypeError):
                versions.append(None)
        if None in versions or len(set(versions)) != 1:
            version = "disagree"
        else:
            version = versions[0]
        print(f"{name} {version} {target['tag']}")
    return 0


# --- CLI ------------------------------------------------------------------


def _date_arg(text: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {text!r}")
    try:
        date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a real date: {text!r}") from None
    return text


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root", default=argparse.SUPPRESS, help="repository root (default: .)"
    )
    parser.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="release config path (default: <root>/.claude/task-flow.release.toml)",
    )


def _add_release_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target", default=None, help="target name (default: the only one)"
    )
    parser.add_argument("version", nargs="?", default=None, help="explicit new version")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--major", action="store_true", help="bump the major version")
    group.add_argument("--minor", action="store_true", help="bump the minor version")
    group.add_argument("--patch", action="store_true", help="bump the patch version")
    parser.add_argument(
        "--since", default=None, help="base ref instead of the last matching tag"
    )
    parser.add_argument(
        "--date", type=_date_arg, default=None, help="release date (default: today)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute and apply a task-flow release."
    )
    _add_common(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    targets_parser = sub.add_parser(
        "targets", help="list targets with version and tag pattern"
    )
    _add_common(targets_parser)
    check_parser = sub.add_parser(
        "check", help="evaluate every precondition, print JSON"
    )
    _add_common(check_parser)
    _add_release_args(check_parser)
    verify_parser = sub.add_parser("verify", help="run the target's checks")
    _add_common(verify_parser)
    verify_parser.add_argument(
        "--target", default=None, help="target name (default: the only one)"
    )
    apply_parser = sub.add_parser(
        "apply", help="bump versions, changelog, commit and tag"
    )
    _add_common(apply_parser)
    _add_release_args(apply_parser)
    apply_parser.add_argument(
        "--entry-file", default=None, help="changelog entry text file"
    )
    apply_parser.add_argument(
        "--dry-run", action="store_true", help="print what would happen"
    )
    args = parser.parse_args(argv)
    if args.command in ("check", "apply") and args.version and _bump_kind(args):
        parser.error("pass either a version or a bump flag, not both")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    root = Path(getattr(args, "root", "."))
    config_arg = getattr(args, "config", None)
    try:
        config = load_config(root, Path(config_arg) if config_arg else None)
        if args.command == "targets":
            return run_targets(root, config)
        name, target = pick_target(config, args.target)
        if args.command == "check":
            report = run_check(root, name, target, args)
            print(json.dumps(report, indent=2))
            return 0 if report["ok"] else 1
        if args.command == "verify":
            return run_verify(root, target)
        return run_apply(root, name, target, args)
    except ConfigError as exc:
        print(f"release.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
