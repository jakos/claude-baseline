# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Read, merge and validate task-flow's models.toml (model profiles and role routing).

Two optional files, both TOML, read as UTF-8:

- user:    ~/.claude/task-flow/models.toml
- project: <repo-root>/.claude/task-flow.models.toml

Merge: profiles by name, then key by key; [roles] key by key; [gates] key by key; the
project file wins. Unknown top-level tables and unknown keys are errors, so typos do
not pass silently. With neither file present there are no profiles and no roles.

[gates] keys:

  command_timeout_seconds     number > 0, per verification command run by
                              scripts/gates.py (default 600)

[profiles.<name>] keys (required: driver, base_url, model, billing; api_key_env is
required unless billing = "local"):

  driver                      "openai-loop"
  base_url                    http(s) URL; "/chat/completions" is appended
  model                       model id string
  api_key_env                 NAME of an environment variable holding the key
  billing                     "subscription" | "api" | "local"
  max_turns                   int > 0                       (default 60)
  max_cost_usd                number >= 0                   (default: no limit)
  max_context                 int > 0, prompt tokens        (default: no limit)
  price_input_per_mtok        number >= 0, USD per 1M tokens (default 0)
  price_cached_input_per_mtok number >= 0                   (default 0)
  price_output_per_mtok       number >= 0                   (default 0)
  fallback                    list of other profile names   (default [])
  enabled                     bool                          (default true)
  retries                     int >= 0                      (default 3)
  retry_base_seconds          number >= 0                   (default 2)
  request_timeout_seconds     number > 0                    (default 300)
  command_timeout_seconds     number > 0                    (default 600)
  temperature                 number 0..2                   (default: provider's)
  max_output_tokens           int > 0, sent as max_tokens   (default: provider's)
  extra_body                  table merged into the request body; the keys model,
                              messages, tools and tool_choice are reserved

[roles] keys: planner, implementer, checker, reviewer, backlog. Values: "claude",
"claude:opus", "claude:sonnet", "claude:haiku" (any role), a profile name (implementer
only), "script" (checker only: run scripts/gates.py instead of the checker agent).

CLI:
  models.py show <profile> [--project <repo-root>]
    Print the resolved profile as `key = value` lines and `api_key: set|missing|none
    needed` (never the key). --project defaults to the current directory. Exit 2 with the
    reason on stderr for an unknown profile, no config or invalid config.
  models.py role <role> [--project <repo-root>]
    Print the resolved value of [roles] <role>, or `claude` when it is unset or no
    config exists. Exit 2 with the reason on stderr for invalid config or an unknown role.
  models.py roles [--project <repo-root>]
    Print the resolved [roles] mapping as one JSON object with sorted keys: each of the
    five roles maps to {"value": <configured value, or "claude" when unset>, "kind":
    "claude" | "profile" | "script", "model": <opus|sonnet|haiku suffix of claude:<m>,
    or null>}. Exit 2 with the reason on stderr for invalid config.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import tomllib

DRIVERS = {"openai-loop"}
BILLINGS = {"subscription", "api", "local"}
ROLES = {"planner", "implementer", "checker", "reviewer", "backlog"}
GATES_KEYS = {"command_timeout_seconds"}
GATES_DEFAULT_TIMEOUT = 600
CLAUDE_VALUES = {"claude", "claude:opus", "claude:sonnet", "claude:haiku"}
RESERVED_BODY_KEYS = {"model", "messages", "tools", "tool_choice"}
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
URL = re.compile(r"^https?://\S+$")

_ABSENT = object()


class ConfigError(Exception):
    """A configuration problem; the message names the file and the key path."""


def user_file() -> Path:
    return Path.home() / ".claude" / "task-flow" / "models.toml"


def project_file(project_root: Path) -> Path:
    return Path(project_root) / ".claude" / "task-flow.models.toml"


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _json_compatible(value) -> bool:
    if value is None or isinstance(value, (str, bool, int, float)):
        return True
    if isinstance(value, list):
        return all(_json_compatible(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _json_compatible(v) for k, v in value.items())
    return False


def _check_choice(choices):
    def check(value):
        if value not in choices:
            raise ValueError(f"must be one of {sorted(choices)}, got {value!r}")

    return check


def _check_url(value):
    if not isinstance(value, str) or not URL.match(value):
        raise ValueError(f"must be an http:// or https:// URL, got {value!r}")


def _check_text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"must be a non-empty string, got {value!r}")


def _check_env_name(value):
    if not isinstance(value, str) or not ENV_NAME.match(value):
        raise ValueError(
            f"must be the NAME of an environment variable, got {value!r} "
            "(never put the key itself here)"
        )


def _check_int(minimum):
    def check(value):
        if not _is_int(value) or value < minimum:
            raise ValueError(f"must be an integer >= {minimum}, got {value!r}")

    return check


def _check_num(minimum, strict=False):
    def check(value):
        if not _is_num(value) or value < minimum or (strict and value == minimum):
            op = ">" if strict else ">="
            raise ValueError(f"must be a number {op} {minimum}, got {value!r}")

    return check


def _check_temperature(value):
    if not _is_num(value) or not 0 <= value <= 2:
        raise ValueError(f"must be a number between 0 and 2, got {value!r}")


def _check_bool(value):
    if not isinstance(value, bool):
        raise TypeError(f"must be true or false, got {value!r}")


def _check_fallback(value):
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"must be a list of profile names, got {value!r}")


def _check_extra_body(value):
    if not isinstance(value, dict):
        raise TypeError(f"must be a table, got {value!r}")
    reserved = sorted(RESERVED_BODY_KEYS & set(value))
    if reserved:
        raise ValueError(f"must not set the reserved keys {reserved}")
    if not _json_compatible(value):
        raise ValueError("must contain only JSON-compatible values")


# key -> (validator, required, default)
PROFILE_KEYS = {
    "driver": (_check_choice(DRIVERS), True, None),
    "base_url": (_check_url, True, None),
    "model": (_check_text, True, None),
    "api_key_env": (_check_env_name, False, None),
    "billing": (_check_choice(BILLINGS), True, None),
    "max_turns": (_check_int(1), False, 60),
    "max_cost_usd": (_check_num(0), False, None),
    "max_context": (_check_int(1), False, None),
    "price_input_per_mtok": (_check_num(0), False, 0),
    "price_cached_input_per_mtok": (_check_num(0), False, 0),
    "price_output_per_mtok": (_check_num(0), False, 0),
    "fallback": (_check_fallback, False, []),
    "enabled": (_check_bool, False, True),
    "retries": (_check_int(0), False, 3),
    "retry_base_seconds": (_check_num(0), False, 2),
    "request_timeout_seconds": (_check_num(0, strict=True), False, 300),
    "command_timeout_seconds": (_check_num(0, strict=True), False, 600),
    "temperature": (_check_temperature, False, None),
    "max_output_tokens": (_check_int(1), False, None),
    "extra_body": (_check_extra_body, False, None),
}


def _read(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    unknown = sorted(set(data) - {"profiles", "roles", "gates"})
    if unknown:
        raise ConfigError(f"{path}: unknown top-level key(s) {unknown}")
    return data


def _validate_profiles(path: Path, profiles) -> dict:
    if not isinstance(profiles, dict):
        raise ConfigError(f"{path}: profiles: must be a table")
    for name, table in profiles.items():
        if not isinstance(table, dict):
            raise ConfigError(f"{path}: profiles.{name}: must be a table")
        for key, value in table.items():
            if key not in PROFILE_KEYS:
                raise ConfigError(f"{path}: profiles.{name}.{key}: unknown key")
            try:
                PROFILE_KEYS[key][0](value)
            except (ValueError, TypeError) as exc:
                raise ConfigError(f"{path}: profiles.{name}.{key}: {exc}") from exc
    return profiles


def _validate_roles(path: Path, roles) -> dict:
    if not isinstance(roles, dict):
        raise ConfigError(f"{path}: roles: must be a table")
    for role, value in roles.items():
        if role not in ROLES:
            raise ConfigError(
                f"{path}: roles.{role}: unknown role (use {sorted(ROLES)})"
            )
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{path}: roles.{role}: must be a non-empty string")
    return roles


def _validate_gates(path: Path, gates) -> dict:
    if not isinstance(gates, dict):
        raise ConfigError(f"{path}: gates: must be a table")
    for key, value in gates.items():
        if key not in GATES_KEYS:
            raise ConfigError(f"{path}: gates.{key}: unknown key")
        try:
            _check_num(0, strict=True)(value)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"{path}: gates.{key}: {exc}") from exc
    return gates


def load(project_root: Path | None = None) -> tuple[dict, dict]:
    """Return (profiles, roles): merged, validated, defaults filled in profiles."""
    sources = [user_file()]
    if project_root is not None:
        sources.append(project_file(project_root))
    raw_profiles: dict[str, dict] = {}
    raw_roles: dict[str, str] = {}
    origin: dict[tuple, Path] = {}
    for path in sources:
        data = _read(path)
        profiles = _validate_profiles(path, data.get("profiles", {}))
        roles = _validate_roles(path, data.get("roles", {}))
        _validate_gates(path, data.get("gates", {}))
        for name, table in profiles.items():
            raw_profiles.setdefault(name, {}).update(table)
            for key in table:
                origin[("profiles", name, key)] = path
        for role, value in roles.items():
            raw_roles[role] = value
            origin[("roles", role)] = path

    resolved: dict[str, dict] = {}
    for name, table in raw_profiles.items():
        files = sorted(
            {
                str(p)
                for (kind, n, *_), p in origin.items()
                if kind == "profiles" and n == name
            }
        )
        where = " + ".join(files)
        for key, (_, required, _default) in PROFILE_KEYS.items():
            if required and key not in table:
                raise ConfigError(
                    f"{where}: profiles.{name}.{key}: required key is missing"
                )
        if table["billing"] != "local" and "api_key_env" not in table:
            raise ConfigError(
                f'{where}: profiles.{name}.api_key_env: required unless billing = "local"'
            )
        full = {}
        for key, (_, _required, default) in PROFILE_KEYS.items():
            value = table.get(key, _ABSENT)
            if value is _ABSENT:
                value = list(default) if isinstance(default, list) else default
            full[key] = value
        resolved[name] = full

    for name, profile in resolved.items():
        path = origin.get(("profiles", name, "fallback"))
        for target in profile["fallback"]:
            if target == name:
                raise ConfigError(
                    f"{path}: profiles.{name}.fallback: must not name itself"
                )
            if target not in resolved:
                raise ConfigError(
                    f"{path}: profiles.{name}.fallback: unknown profile {target!r}"
                )

    for role, value in raw_roles.items():
        path = origin[("roles", role)]
        if value in CLAUDE_VALUES:
            continue
        if value == "script":
            if role != "checker":
                raise ConfigError(
                    f'{path}: roles.{role}: "script" is only valid for checker'
                )
            continue
        if role != "implementer":
            raise ConfigError(
                f"{path}: roles.{role}: {value!r} is not valid; only implementer may name "
                f"a profile (use one of {sorted(CLAUDE_VALUES)})"
            )
        if value not in resolved:
            raise ConfigError(f"{path}: roles.{role}: unknown profile {value!r}")
    return resolved, raw_roles


def gates(project_root: Path | None = None) -> dict:
    """The merged [gates] table: {"command_timeout_seconds": <value or 600>}.

    Sources are the user file and, when given, the project file; merged key by key,
    the project wins, exactly like [roles]. An invalid [gates] table raises ConfigError.
    """
    sources = [user_file()]
    if project_root is not None:
        sources.append(project_file(project_root))
    merged: dict = {}
    for path in sources:
        data = _read(path)
        merged.update(_validate_gates(path, data.get("gates", {})))
    return {
        "command_timeout_seconds": merged.get(
            "command_timeout_seconds", GATES_DEFAULT_TIMEOUT
        )
    }


def api_key(profile: dict) -> str | None:
    """The key from the profile's environment variable, or None when unset or empty."""
    env = profile.get("api_key_env")
    if not env:
        return None
    return os.environ.get(env) or None


def _format(value) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False)


def show(profile_name: str, project_root: Path) -> str:
    profiles, _roles = load(project_root)
    if profile_name not in profiles:
        known = ", ".join(sorted(profiles)) or "none"
        raise ConfigError(f"unknown profile {profile_name!r} (known: {known})")
    profile = profiles[profile_name]
    lines = [f"{key} = {_format(value)}" for key, value in profile.items()]
    if not profile["api_key_env"]:
        lines.append("api_key: none needed")
    else:
        lines.append("api_key: set" if api_key(profile) else "api_key: missing")
    return "\n".join(lines)


def resolve_roles(project_root: Path) -> dict:
    """Resolve every role to {"value", "kind", "model"}; unset roles are bare "claude"."""
    _profiles, roles = load(project_root)
    resolved = {}
    for role in sorted(ROLES):
        value = roles.get(role, "claude")
        if value in CLAUDE_VALUES:
            kind = "claude"
            model = value.split(":", 1)[1] if value.startswith("claude:") else None
        elif value == "script":
            kind, model = "script", None
        else:
            kind, model = "profile", None
        resolved[role] = {"value": value, "kind": kind, "model": model}
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect task-flow's models.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    show_parser = sub.add_parser("show", help="print one resolved profile")
    show_parser.add_argument("profile")
    show_parser.add_argument(
        "--project", default=".", help="repository root (default: .)"
    )
    role_parser = sub.add_parser("role", help="print the value of one role")
    role_parser.add_argument("role")
    role_parser.add_argument(
        "--project", default=".", help="repository root (default: .)"
    )
    roles_parser = sub.add_parser(
        "roles", help="print the resolved role mapping as JSON"
    )
    roles_parser.add_argument(
        "--project", default=".", help="repository root (default: .)"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "role":
            if args.role not in ROLES:
                raise ConfigError(f"unknown role {args.role!r} (use {sorted(ROLES)})")
            _profiles, roles = load(Path(args.project).resolve())
            print(roles.get(args.role, "claude"))
        elif args.command == "roles":
            mapping = resolve_roles(Path(args.project).resolve())
            print(json.dumps(mapping, sort_keys=True))
        else:
            print(show(args.profile, Path(args.project).resolve()))
    except ConfigError as exc:
        print(f"models.py: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
