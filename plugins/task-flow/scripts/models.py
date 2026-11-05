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

[profiles.<name>] keys (required: driver, model, billing; base_url on the API
drivers, api_key_env unless billing = "local" on the API drivers and unless
billing = "subscription" on driver "cli"):

  driver                      "openai-loop" | "anthropic-loop" | "cli"
  base_url                    http(s) URL; "/chat/completions" is appended on
                              "openai-loop", "/messages" on "anthropic-loop"
                              (required on the API drivers; refused on "cli")
  model                       model id string (required on every driver; on "cli" it
                              is metadata only — the model is chosen by the CLI's
                              own -m argument in `command`)
  api_key_env                 NAME of an environment variable holding the key
                              (required unless billing = "local", and on "cli"
                              only with billing = "api")
  billing                     "subscription" | "api" | "local"
  max_turns                   int > 0                       (default 60; refused when
                              set on "cli": the CLI owns its internal loop)
  max_cost_usd                number >= 0                   (default: no limit; refused
                              when set on "cli" — see timeout_seconds)
  max_context                 int > 0, prompt tokens        (default: no limit; refused
                              when set on "cli")
  price_input_per_mtok        number >= 0, USD per 1M tokens (default 0)
  price_cached_input_per_mtok number >= 0                   (default 0)
  price_output_per_mtok       number >= 0                   (default 0)
  price_cache_write_per_mtok  number >= 0, USD per 1M prompt-cache-write tokens
                              (default: price_input_per_mtok)
  fallback                    list of other profile names   (default [])
  enabled                     bool                          (default true)
  retries                     int >= 0                      (default 3; refused when
                              set on "cli": there are no per-request retries)
  retry_base_seconds          number >= 0                   (default 2; refused on "cli")
  request_timeout_seconds     number > 0                    (default 300; refused on "cli")
  command_timeout_seconds     number > 0                    (default 600; refused when
                              set on "cli": the CLI's deadline is timeout_seconds)
  temperature                 number 0..2                   (default: provider's;
                              rejected on "anthropic-loop" and on "cli")
  max_output_tokens           int > 0                       (default: provider's;
                              "anthropic-loop" sends it as max_tokens and
                              defaults to 16000 when unset; refused on "cli")
  max_tokens_param            "max_tokens" | "max_completion_tokens", the body key
                              max_output_tokens is sent under (default
                              "max_tokens"; only "max_tokens" on "anthropic-loop";
                              refused on "cli")
  extra_body                  table merged into the request body; the reserved
                              keys are per driver — "openai-loop": model,
                              messages, tools, tool_choice; "anthropic-loop":
                              those plus system, max_tokens, stream, temperature,
                              top_p, top_k; refused on "cli"
  post_edit                   list of commands run by scripts/implement.py after each
                              successful write_file/edit_file call, with "{path}"
                              replaced by the written file (default []; refused when
                              set on "cli": a CLI uses its own tools, so there are no
                              write_file/edit_file calls to hook)
  prompt_extras               list of files scripts/implement.py reads before the round
                              and appends to the prompt (default [])

  "cli" driver keys (only valid on driver "cli"; `command` is required there):

  command                     list of non-empty strings: the program and its
                              arguments. Only the placeholders {worktree},
                              {prompt_file} and {last_message_file} are expanded
                              (each argument is expanded on its own; no shell,
                              no other interpolation)
  read_only_command           same shape and placeholders as `command`, but used
                              instead of `command` by producer roles that must not
                              write (the planner): the argv is verified read-only
                              (verify_read_only_command; initially only a Codex
                              `exec --sandbox read-only` command that captures the
                              final message with -o {last_message_file} passes),
                              required when this profile serves such a role or is
                              one of its fallbacks (default: none; never inferred
                              from the profile name)
  timeout_seconds             number > 0, the whole CLI run's deadline
                              (default 600)
  prompt_via                  "stdin" | "file" | "arg" (default "stdin"): how the
                              prompt reaches the CLI — on stdin, in a temporary
                              file whose path replaces {prompt_file} (required
                              when prompt_via = "file"), or appended as one final
                              argument (prompt_via = "arg")
  usage_format                "codex-jsonl" | "none" (default "none"): stdout is
                              parsed as one JSON event per line and turn.completed
                              usage is summed ("codex-jsonl"), or only the final
                              plain stdout message is used ("none")
  pass_env                    list of environment-variable NAMES whose values are
                              additionally passed to the CLI (default []); the
                              runner's allowlist still applies first
  limit_patterns              list of regular expressions; a match against the
                              bounded stderr or final output makes this attempt
                              unavailable and advances the fallback chain
                              (default [])

[roles] keys: planner, implementer, checker, reviewer, backlog. Values: "claude",
"claude:opus", "claude:sonnet", "claude:haiku" (any role), a profile name (planner and
implementer only), "script" (checker only: run scripts/gates.py instead of the checker
agent). The implementer key also accepts a table form, [roles.implementer] with
`default = "<profile>"`, optional bands and complete S/M thresholds. The default
normalizes to the same scalar value; resolve_implementer applies task-aware rules. A scalar and a table never merge across the user and project files: whichever
file sets the key last (the project file wins) replaces the other form entirely, and
unknown keys inside the table are errors, so future routing keys fail loudly until
they are implemented.

CLI:
  models.py show <profile> [--project <repo-root>]
    Print the resolved profile as `key = value` lines and `api_key: set|missing|none
    needed` (never the key). --project defaults to the current directory. Exit 2 with the
    reason on stderr for an unknown profile, no config or invalid config. A profile
    whose api_key_env is ANTHROPIC_API_KEY also gets a warning on stderr (that variable
    makes Claude Code itself bill by API token instead of the subscription).
  models.py role <role> [--project <repo-root>]
    Print the resolved value of [roles] <role>, or `claude` when it is unset or no
    config exists. Exit 2 with the reason on stderr for invalid config or an unknown role.
  models.py roles [--project <repo-root>] [--task-dir <path>]
    Print the resolved [roles] mapping as one JSON object with sorted keys: each of the
    five roles maps to {"value": <configured value, or "claude" when unset>, "kind":
    "claude" | "profile" | "script", "model": <opus|sonnet|haiku suffix of claude:<m>,
    or null>}. Active implementer rules add versioned routing evidence; --task-dir
    resolves the task, otherwise the default is explicitly task-dependent. Previews
    are read-only. Exit 2 with the reason on stderr for invalid config or plan.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
from pathlib import Path

DRIVERS = {"openai-loop", "anthropic-loop", "cli"}
BILLINGS = {"subscription", "api", "local"}
ROLES = {"planner", "implementer", "checker", "reviewer", "backlog"}
# Roles that may name a [profiles.<name>] instead of a Claude value. The planner is a
# producer role: it only reads and returns artifact text, which the trusted runner
# publishes, so a "cli" profile serving it needs a verified read_only_command.
PROFILE_ROLES = {"planner", "implementer"}
PRODUCER_ROLES = {"planner"}
# Strict keys of the alternative implementer table. Unknown keys remain errors.
ROLE_TABLE_KEYS = {"implementer": {"default", "bands", "thresholds"}}
DIMENSIONS = ("steps", "criteria", "commands", "declared_paths")
GATES_KEYS = {"command_timeout_seconds"}
GATES_DEFAULT_TIMEOUT = 600
CLAUDE_VALUES = {"claude", "claude:opus", "claude:sonnet", "claude:haiku"}
PROMPT_VIAS = {"stdin", "file", "arg"}
USAGE_FORMATS = {"codex-jsonl", "none"}
# Placeholders a "cli" profile's command arguments may contain; nothing else is
# expanded, and there is no shell interpolation of any kind.
CLI_PLACEHOLDERS = {"worktree", "prompt_file", "last_message_file"}
# Keys that only make sense on driver "cli" (they are refused on the API drivers).
CLI_ONLY_KEYS = {
    "command",
    "read_only_command",
    "timeout_seconds",
    "prompt_via",
    "usage_format",
    "pass_env",
    "limit_patterns",
}
# The only CLI adapter whose read-only execution is verified today: Codex `exec` with
# an explicit read-only sandbox. A producer role's read_only_command must match this
# shape; anything else is refused instead of guessed from the profile's name.
VERIFIED_READ_ONLY_PROGRAMS = {"codex"}
READ_ONLY_SANDBOX = "read-only"
# Codex flags that would break the read-only guarantee (a workspace-write or full-auto
# sandbox, approval bypass, or a -c/--config override that can change any of them).
READ_ONLY_FORBIDDEN_ARGS = {
    "--full-auto",
    "--dangerously-bypass-approvals-and-sandbox",
    "-c",
    "--config",
    "--ask-for-approval",
    "--oss",
}
# Shell metacharacters are refused inside any read_only_command argument: on Windows a
# .cmd/.bat shim runs through cmd.exe, where they would be live.
SHELL_METACHARS = "&|<>^%!\n\r"
# Keys of the API wire loop that cannot govern a CLI's own internal loop; setting any
# of them explicitly on a "cli" profile is an error (their defaults are fine).
CLI_INCOMPATIBLE_KEYS = {
    "base_url",
    "max_turns",
    "max_cost_usd",
    "max_context",
    "retries",
    "retry_base_seconds",
    "request_timeout_seconds",
    "command_timeout_seconds",
    "temperature",
    "max_output_tokens",
    "max_tokens_param",
    "extra_body",
    "post_edit",
}
# Reserved extra_body keys per driver, checked on the merged profile (the driver and
# extra_body may come from different files), after the user/project merge.
RESERVED_BODY_KEYS = {
    "openai-loop": {"model", "messages", "tools", "tool_choice"},
    "anthropic-loop": {
        "model",
        "system",
        "messages",
        "tools",
        "tool_choice",
        "max_tokens",
        "stream",
        "temperature",
        "top_p",
        "top_k",
    },
}
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


def _check_str_list(value):
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError("must be a list of non-empty strings")


def _check_env_name_list(value):
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("must be a list of environment-variable names")
    for name in value:
        _check_env_name(name)


def _check_regex_list(value):
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError("must be a list of non-empty regular-expression strings")
    for pattern in value:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regular expression {pattern!r}: {exc}") from exc


def _check_extra_body(value):
    if not isinstance(value, dict):
        raise TypeError(f"must be a table, got {value!r}")
    if not _json_compatible(value):
        raise ValueError("must contain only JSON-compatible values")


# key -> (validator, required, default)
PROFILE_KEYS = {
    "driver": (_check_choice(DRIVERS), True, None),
    # base_url is required on the API drivers and refused on "cli"; both are checked
    # after the merge, when the driver is known (it may come from the other file)
    "base_url": (_check_url, False, None),
    "model": (_check_text, True, None),
    "api_key_env": (_check_env_name, False, None),
    "billing": (_check_choice(BILLINGS), True, None),
    "max_turns": (_check_int(1), False, 60),
    "max_cost_usd": (_check_num(0), False, None),
    "max_context": (_check_int(1), False, None),
    "price_input_per_mtok": (_check_num(0), False, 0),
    "price_cached_input_per_mtok": (_check_num(0), False, 0),
    "price_output_per_mtok": (_check_num(0), False, 0),
    "price_cache_write_per_mtok": (_check_num(0), False, None),
    "fallback": (_check_fallback, False, []),
    "enabled": (_check_bool, False, True),
    "retries": (_check_int(0), False, 3),
    "retry_base_seconds": (_check_num(0), False, 2),
    "request_timeout_seconds": (_check_num(0, strict=True), False, 300),
    "command_timeout_seconds": (_check_num(0, strict=True), False, 600),
    "temperature": (_check_temperature, False, None),
    "max_output_tokens": (_check_int(1), False, None),
    "max_tokens_param": (
        _check_choice({"max_tokens", "max_completion_tokens"}),
        False,
        "max_tokens",
    ),
    "extra_body": (_check_extra_body, False, None),
    "post_edit": (_check_str_list, False, []),
    "prompt_extras": (_check_str_list, False, []),
    "command": (_check_str_list, False, None),
    "read_only_command": (_check_str_list, False, None),
    "timeout_seconds": (_check_num(0, strict=True), False, 600),
    "prompt_via": (_check_choice(PROMPT_VIAS), False, "stdin"),
    "usage_format": (_check_choice(USAGE_FORMATS), False, "none"),
    "pass_env": (_check_env_name_list, False, []),
    "limit_patterns": (_check_regex_list, False, []),
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


def _validate_roles(path: Path, roles: object) -> dict:
    """Validate role forms before merging; retain implementer table leaves.

    The scalar/table form replaces the other form across configuration files.
    Two tables merge by leaf before required defaults, targets and bounds are checked.
    load returns scalar role values for compatibility and optionally exposes rules.
    """
    if not isinstance(roles, dict):
        raise ConfigError(f"{path}: roles: must be a table")
    normalized: dict[str, str | dict] = {}
    for role, value in roles.items():
        if role not in ROLES:
            raise ConfigError(
                f"{path}: roles.{role}: unknown role (use {sorted(ROLES)})"
            )
        if isinstance(value, dict):
            accepted = ROLE_TABLE_KEYS.get(role)
            if accepted is None:
                raise ConfigError(
                    f"{path}: roles.{role}: a table is only valid for "
                    f"{sorted(ROLE_TABLE_KEYS)}; use a string value for {role!r}"
                )
            unknown = sorted(set(value) - accepted)
            if unknown:
                raise ConfigError(
                    f"{path}: roles.{role}.{unknown[0]}: unknown key "
                    f"(accepted: {sorted(accepted)}; future rule keys are errors "
                    "until they are implemented)"
                )
            normalized[role] = value
            continue
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{path}: roles.{role}: must be a non-empty string")
        normalized[role] = value
    return normalized


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


def _validate_cli_profile(name, table, full, key_where, where) -> None:
    """The post-merge checks of a driver "cli" profile.

    `table` is the merged raw key set (so "explicitly set" means "set in either
    file"), `full` the profile with defaults filled in, `key_where(key)` the provenance
    prefix for a key-specific error and `where` the profile's file list.
    """
    if "command" not in table:
        raise ConfigError(f"{where}: profiles.{name}.command: required key is missing")
    if not full["command"]:
        raise ConfigError(
            f"{key_where('command')}profiles.{name}.command: must not be empty"
        )
    for key in ("command", "read_only_command"):
        for argument in full[key] or []:
            for match in re.finditer(r"\{([^{}]*)\}", argument):
                placeholder = match.group(1)
                if placeholder not in CLI_PLACEHOLDERS:
                    raise ConfigError(
                        f"{key_where(key)}profiles.{name}.{key}: unknown "
                        f"placeholder {{{placeholder}}} in {argument!r}; allowed: "
                        f"{sorted(CLI_PLACEHOLDERS)}"
                    )
            if key == "read_only_command" and any(
                char in SHELL_METACHARS for char in argument
            ):
                raise ConfigError(
                    f"{key_where(key)}profiles.{name}.{key}: shell metacharacter in "
                    f"{argument!r}; the argv runs without a shell"
                )
    if full["read_only_command"]:
        problem = verify_read_only_command(full["read_only_command"])
        if problem is not None:
            raise ConfigError(
                f"{key_where('read_only_command')}profiles.{name}.read_only_command: "
                f"{problem}"
            )
    has_prompt_file = any("{prompt_file}" in arg for arg in full["command"])
    if full["prompt_via"] == "file" and not has_prompt_file:
        raise ConfigError(
            f'{key_where("prompt_via")}profiles.{name}.prompt_via: "file" requires '
            "{prompt_file} in command"
        )
    if full["prompt_via"] in ("stdin", "arg") and has_prompt_file:
        raise ConfigError(
            f"{key_where('command')}profiles.{name}.command: must not contain "
            f"{{prompt_file}} with prompt_via = {full['prompt_via']!r} (that transport "
            "sends the prompt on stdin, or appends it as one final argument)"
        )
    for key in CLI_INCOMPATIBLE_KEYS:
        if key in table:
            raise ConfigError(
                f'{key_where(key)}profiles.{name}.{key}: not allowed on driver "cli" '
                "(the CLI owns its internal loop; its deadline is timeout_seconds)"
            )
    if full["billing"] == "api":
        if "api_key_env" not in table:
            raise ConfigError(
                f'{where}: profiles.{name}.api_key_env: required when driver = "cli" '
                'and billing = "api"'
            )
    elif "api_key_env" in table:
        raise ConfigError(
            f"{key_where('api_key_env')}profiles.{name}.api_key_env: only valid on "
            'driver "cli" with billing = "api" (a subscription or local CLI uses the '
            "CLI's saved login and needs no key)"
        )


def load(
    project_root: Path | None = None, *, with_rules: bool = False
) -> tuple[dict, dict] | tuple[dict, dict, dict | None]:
    """Return (profiles, roles): merged, validated, defaults filled in profiles."""
    sources = [user_file()]
    if project_root is not None:
        sources.append(project_file(project_root))
    raw_profiles: dict[str, dict] = {}
    raw_roles: dict[str, str | dict] = {}
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
            if isinstance(value, dict) and isinstance(raw_roles.get(role), dict):
                merge_rule(raw_roles[role], value)
            else:
                for key in list(origin):
                    if key[:2] == ("roles", role):
                        del origin[key]
                raw_roles[role] = value
            record_origins(value, ("roles", role), path, origin)
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

        def key_where(key: str, name=name, where=where) -> str:
            """The file that set this key, for a key-specific error message."""
            path = origin.get(("profiles", name, key))
            return f"{path}: " if path is not None else f"{where}: "

        for key, (_, required, _default) in PROFILE_KEYS.items():
            if required and key not in table:
                raise ConfigError(
                    f"{where}: profiles.{name}.{key}: required key is missing"
                )
        full = {}
        for key, (_, _required, default) in PROFILE_KEYS.items():
            value = table.get(key, _ABSENT)
            if value is _ABSENT:
                value = list(default) if isinstance(default, list) else default
            full[key] = value
        if full["driver"] == "cli":
            _validate_cli_profile(name, table, full, key_where, where)
        else:
            if "base_url" not in table:
                raise ConfigError(
                    f"{where}: profiles.{name}.base_url: required key is missing on "
                    f"driver {full['driver']!r}"
                )
            for key in CLI_ONLY_KEYS:
                if key in table:
                    raise ConfigError(
                        f"{key_where(key)}profiles.{name}.{key}: only valid on driver "
                        '"cli"'
                    )
            if table["billing"] != "local" and "api_key_env" not in table:
                raise ConfigError(
                    f"{where}: profiles.{name}.api_key_env: required unless "
                    'billing = "local"'
                )
        if full["driver"] == "anthropic-loop":
            if full["temperature"] is not None:
                raise ConfigError(
                    f"{where}: profiles.{name}.temperature: not allowed on driver "
                    '"anthropic-loop" (the pinned API version rejects temperature on '
                    "every model)"
                )
            if full["max_tokens_param"] != "max_tokens":
                raise ConfigError(
                    f"{where}: profiles.{name}.max_tokens_param: must be "
                    f'"max_tokens" on driver "anthropic-loop", got '
                    f"{full['max_tokens_param']!r}"
                )
        if full["extra_body"]:
            reserved = sorted(
                RESERVED_BODY_KEYS.get(full["driver"], set()) & set(full["extra_body"])
            )
            if reserved:
                raise ConfigError(
                    f"{where}: profiles.{name}.extra_body: must not set the reserved "
                    f"keys {reserved}"
                )
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

    rules = raw_roles.get("implementer")
    if isinstance(rules, dict):
        validate_rules(rules, resolved, origin)
        raw_roles["implementer"] = rules["default"]
    else:
        rules = None
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
        if role not in PROFILE_ROLES:
            raise ConfigError(
                f"{path}: roles.{role}: {value!r} is not valid; only "
                f"{sorted(PROFILE_ROLES)} may name a profile "
                f"(use one of {sorted(CLAUDE_VALUES)})"
            )
        if value not in resolved:
            raise ConfigError(f"{path}: roles.{role}: unknown profile {value!r}")
    return (resolved, raw_roles, rules) if with_rules else (resolved, raw_roles)


def merge_rule(target: dict, source: dict) -> None:
    """Merge table leaves; scalar/table replacements are handled by the caller."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_rule(target[key], value)
        else:
            target[key] = value


def record_origins(value: object, prefix: tuple, path: Path, origins: dict) -> None:
    origins[prefix] = path
    if isinstance(value, dict):
        for key, child in value.items():
            record_origins(child, (*prefix, key), path, origins)


def validate_rules(rules: dict, profiles: dict, origins: dict) -> None:
    def fail(key: str, message: str) -> None:
        parts = ("roles", "implementer", *key.split("."))
        while parts not in origins:
            parts = parts[:-1]
        raise ConfigError(f"{origins[parts]}: roles.implementer.{key}: {message}")

    def target(key: str, value: object) -> None:
        if not isinstance(value, str) or value not in profiles:
            fail(key, f"unknown profile {value!r}; required enabled external profile")
        if not profiles[value]["enabled"]:
            fail(key, f"profile {value!r} is disabled")

    target("default", rules.get("default"))
    bands = rules.get("bands", {})
    if not isinstance(bands, dict):
        fail("bands", "must be a table")
    for band, value in bands.items():
        if band not in {"S", "M", "L"}:
            fail(f"bands.{band}", "unknown band")
        target(f"bands.{band}", value)
    bounds = rules.get("thresholds")
    if not bands:
        if bounds is not None:
            fail("thresholds", "requires nonempty bands")
        return
    if isinstance(bounds, dict) and set(bounds) - {"S", "M"}:
        fail("thresholds." + min(set(bounds) - {"S", "M"}), "unknown bound band")
    if not isinstance(bounds, dict) or set(bounds) != {"S", "M"}:
        fail("thresholds", "requires exactly S and M upper-bound tables")
    for band, table in bounds.items():
        if isinstance(table, dict) and set(table) - set(DIMENSIONS):
            fail(
                f"thresholds.{band}." + min(set(table) - set(DIMENSIONS)),
                "unknown bound key",
            )
        if not isinstance(table, dict) or set(table) != set(DIMENSIONS):
            fail(f"thresholds.{band}", f"requires exactly {DIMENSIONS}")
        for key, value in table.items():
            if not _is_int(value) or value < 0:
                fail(f"thresholds.{band}.{key}", "must be a nonnegative integer")
    if (
        any(bounds["S"][k] > bounds["M"][k] for k in DIMENSIONS)
        or bounds["S"] == bounds["M"]
    ):
        fail(
            "thresholds",
            "S must be componentwise <= M and differ in at least one bound",
        )


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def measure_plan(text: str) -> dict[str, int]:
    """Count declarations, never files on disk. Refuse invalid approved artifacts."""
    import role_contracts

    verdict = role_contracts.validate_plan(text)
    if not verdict.ok or verdict.blocked:
        raise ConfigError("plan.md: " + "; ".join(verdict.reasons or ["blocked plan"]))
    parts, _ = role_contracts._sections(text)
    paths = set()
    for line in role_contracts.unfenced(parts["Profiles"]):
        if line.strip():
            for entry in line.split(":", 1)[1].split(","):
                entry = entry.strip().strip("`").strip().replace("\\", "/")
                if not entry:
                    raise ConfigError("plan.md: Profiles: empty declared path")
                paths.add(entry)
    return {
        "steps": sum(
            bool(role_contracts.STEP.match(line))
            for line in role_contracts.unfenced(parts["Steps"])
        ),
        "criteria": sum(
            bool(role_contracts.AC_LINE.match(line))
            for line in role_contracts.unfenced(parts["Acceptance criteria"])
        ),
        "commands": len(verdict.commands),
        "declared_paths": len(paths),
    }


ROUTING_EVENT = "implementer-routing "


def read_status(task_dir: Path) -> str:
    """Read the attempt status, reporting artifact errors as resumable refusals."""
    path = task_dir / "status.md"
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def attempt_record(
    project_root: Path, task_dir: Path, evidence: dict | None
) -> dict | None:
    """Read and validate the current attempt pin without publishing it."""
    text = read_status(task_dir)
    history = text.split("## History", 1)[-1].split("## Log", 1)[0]
    if evidence is None and ROUTING_EVENT not in history:
        return None
    retry = None
    archive = None
    pin = None
    for line in history.splitlines():
        match = re.search(r"round reset to 0 for attempt (\d+) of epic ([\w-]+) ", line)
        if match and "authorized by the user" in line:
            archive = (
                project_root
                / ".work"
                / "_epics"
                / match[2]
                / "attempts"
                / task_dir.name
                / f"attempt-{match[1]}"
            )
            if not (archive / "spend.md").is_file():
                raise ConfigError("routing retry has no archived spend evidence")
            retry = f"{match[2]}/{match[1]}"
            pin = None
        if ROUTING_EVENT in line:
            try:
                pin = json.loads(line.split(ROUTING_EVENT, 1)[1])
            except ValueError as exc:
                raise ConfigError(
                    "status.md: malformed implementer routing History event"
                ) from exc
    if pin is not None:
        expected = dict(evidence or {}, state="pinned", attempt=retry or "initial")
        if pin != expected:
            raise ConfigError(
                "implementer routing plan/configuration changed; restore the pinned inputs or start an authorized restart/retry"
            )
        return pin
    if evidence is None:
        return None
    round_match = re.search(r"^- round: (\d+)$", text, re.MULTILINE)
    started = (round_match and int(round_match[1]) > 0) or any(
        task_dir.glob("impl-*.json")
    )
    # Epic retries copy old round files before resetting status; those exact archived
    # files do not mean the new attempt has already started.
    archived_retry = bool(
        retry
        and archive
        and round_match
        and round_match[1] in {"0", "1"}
        and all(
            (archive / path.name).is_file()
            and (archive / path.name).read_bytes() == path.read_bytes()
            for path in task_dir.glob("impl-*.json")
        )
    )
    if started and not archived_retry:
        raise ConfigError(
            "routing enabled on a started unpinned attempt; start an authorized restart/retry"
        )
    return dict(evidence, state="pinned", attempt=retry or "initial")


def publish_attempt(task_dir: Path, evidence: dict | None) -> None:
    """Publish once in History after all implementation preflight succeeds."""
    if evidence is None:
        return
    path = task_dir / "status.md"
    text = read_status(task_dir)
    entry = (
        "- " + ROUTING_EVENT + json.dumps(evidence, sort_keys=True, ensure_ascii=False)
    )
    if entry in text.splitlines():
        return
    if "## History" not in text:
        raise ConfigError("status.md: missing History")
    before, sep, after = text.partition("## Log")
    updated = before.rstrip() + "\n" + entry + "\n" + (sep + after if sep else "")
    temporary = path.with_suffix(".routing.tmp")
    temporary.write_text(updated, encoding="utf-8", newline="\n")
    temporary.replace(path)


def resolve_implementer(project_root: Path, task_dir: Path | None = None) -> dict:
    profiles, roles, rules = load(project_root, with_rules=True)
    result = {
        "value": roles.get("implementer", "claude"),
        "configured": "implementer" in roles,
    }
    if not rules or not rules.get("bands"):
        if task_dir is not None:
            # Removing rules must not dispatch a pinned external attempt to Claude.
            attempt_record(project_root, task_dir, None)
        return result
    evidence = {
        "schema": "implementer-routing/1",
        "rule_digest": digest(rules),
        "selected_profile": rules["default"],
        "thresholds": rules["thresholds"],
        "band": None,
        "inputs": None,
        "plan_digest": None,
        "state": "task-dependent",
    }
    if task_dir is not None:
        try:
            text = (task_dir / "plan.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ConfigError(f"{task_dir}/plan.md: {exc}") from exc
        inputs = measure_plan(text)
        band = next(
            (
                b
                for b in ("S", "M")
                if all(inputs[k] <= rules["thresholds"][b][k] for k in DIMENSIONS)
            ),
            "L",
        )
        evidence.update(
            inputs=inputs,
            band=band,
            selected_profile=rules["bands"].get(band, rules["default"]),
            plan_digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            state="preview",
        )
        chosen = evidence["selected_profile"]
        evidence["profile_digest"] = digest(
            {name: profiles[name] for name in [chosen, *profiles[chosen]["fallback"]]}
        )
        result["value"] = chosen
    if task_dir is not None:
        history = read_status(task_dir)
        if ROUTING_EVENT in history:
            pin = attempt_record(project_root, task_dir, evidence)
            if pin and json.dumps(pin, sort_keys=True, ensure_ascii=False) in history:
                evidence = pin
    result["routing"] = evidence
    return result


def verify_read_only_command(command: list[str]) -> str | None:
    """Check a producer role's read_only_command; return the problem or None.

    Only a verified Codex read-only execution passes today: `codex exec` with an
    explicit `--sandbox read-only` (no other sandbox value, no --full-auto, no approval
    bypass, no -c/--config override) and a `-o {last_message_file}` final-message
    capture, so the host — not the model — owns the artifact text. Unverified CLI
    adapters are refused; read-only behavior is never inferred from a profile name.
    """
    if not command:
        return "the command is empty"
    program = command[0]
    stem = program.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = stem.lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        name = name.removesuffix(suffix)
    if name not in VERIFIED_READ_ONLY_PROGRAMS:
        return (
            f"unverified CLI adapter {program!r}: only "
            f"{sorted(VERIFIED_READ_ONLY_PROGRAMS)} with a read-only sandbox is "
            "supported for a producer role"
        )
    rest = command[1:]
    if "exec" not in rest:
        return "the verified read-only form is `codex exec ...`"
    sandbox_values: list[str] = []
    index = 0
    while index < len(rest):
        argument = rest[index]
        if argument in ("--sandbox", "-s"):
            if index + 1 >= len(rest):
                return f"{argument} needs a value"
            sandbox_values.append(rest[index + 1])
            index += 2
            continue
        if argument.startswith("--sandbox="):
            sandbox_values.append(argument.split("=", 1)[1])
        index += 1
    if not sandbox_values:
        return (
            "must set the sandbox explicitly: --sandbox read-only (read-only behavior "
            "is never inferred)"
        )
    for value in sandbox_values:
        if value != READ_ONLY_SANDBOX:
            return (
                f"--sandbox {value!r} is not read-only; a producer role must use "
                f"--sandbox {READ_ONLY_SANDBOX}"
            )
    for argument in rest:
        if argument in READ_ONLY_FORBIDDEN_ARGS or argument.startswith("--config="):
            return (
                f"{argument!r} conflicts with the read-only guarantee (workspace-write "
                "or full-auto sandbox, approval bypass, or a config override)"
            )
    output_captured = False
    index = 0
    while index < len(rest):
        argument = rest[index]
        if argument in ("-o", "--output-last-message"):
            if index + 1 < len(rest) and "{last_message_file}" in rest[index + 1]:
                output_captured = True
            index += 2
            continue
        if argument.startswith("--output-last-message="):
            output_captured = "{last_message_file}" in argument
        index += 1
    if not output_captured:
        return (
            "must capture the final message with -o {last_message_file} "
            "(--output-last-message {last_message_file}); the host publishes the "
            "validated artifact, not the CLI"
        )
    return None


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


def warnings(profiles: dict) -> list[str]:
    """One line per profile whose api_key_env is exactly ANTHROPIC_API_KEY: in a Claude
    Code session that variable makes Claude Code itself bill by API token instead of
    the subscription, so the profile should use another variable name."""
    lines = []
    for name in sorted(profiles):
        profile = profiles[name]
        if profile.get("api_key_env") == "ANTHROPIC_API_KEY":
            lines.append(
                f'profiles.{name}: api_key_env = "ANTHROPIC_API_KEY" - that variable is '
                "also read by Claude Code itself, where it switches billing from the "
                "subscription to per-API-token; use another name, e.g. "
                '"TASK_FLOW_ANTHROPIC_KEY"'
            )
    return lines


def show_text(profile: dict) -> str:
    lines = [f"{key} = {_format(value)}" for key, value in profile.items()]
    if not profile["api_key_env"]:
        lines.append("api_key: none needed")
    else:
        lines.append("api_key: set" if api_key(profile) else "api_key: missing")
    return "\n".join(lines)


def show(profile_name: str, project_root: Path) -> str:
    profiles, _roles = load(project_root)
    if profile_name not in profiles:
        known = ", ".join(sorted(profiles)) or "none"
        raise ConfigError(f"unknown profile {profile_name!r} (known: {known})")
    return show_text(profiles[profile_name])


def resolve_roles(project_root: Path, task_dir: Path | None = None) -> dict:
    """Resolve every role to {"value", "kind", "model"}; unset roles are bare "claude"."""
    _profiles, roles = load(project_root)
    selection = resolve_implementer(project_root, task_dir)
    roles["implementer"] = selection["value"]
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
    if "routing" in selection:
        resolved["implementer"]["routing"] = selection["routing"]
    return resolved


def resolve_role_profile(
    project_root: Path, role: str, override: str | None = None
) -> dict:
    """Resolve one role for one run, with an optional invocation-scoped override.

    Returns {"role", "value", "kind", "profile", "method", "model", "billing",
    "fallback"}: kind is "claude", "profile" or "script"; method is "claude",
    "api", "cli" or "script"; profile is the profile name or None; billing is the
    profile's billing or "subscription (Claude Code)" / "none (script)". The
    override must name an enabled configured profile — never a free-form model or
    command. A producer role (the planner) on a driver "cli" profile, and every
    fallback in its chain, must carry a verified read_only_command. Raises
    ConfigError with file/key diagnostics.
    """
    if role not in ROLES:
        raise ConfigError(f"unknown role {role!r} (use {sorted(ROLES)})")
    profiles, roles = load(project_root)
    value = override if override is not None else roles.get(role, "claude")
    source = "override" if override is not None else f"roles.{role}"
    if override is not None and (value in CLAUDE_VALUES or value == "script"):
        raise ConfigError(
            f"override: {value!r} is not a profile name; an override must name an "
            "enabled configured profile of models.toml, never a free-form model, "
            "Claude value or command"
        )
    if value in CLAUDE_VALUES:
        model = value.split(":", 1)[1] if value.startswith("claude:") else None
        return {
            "role": role,
            "value": value,
            "kind": "claude",
            "profile": None,
            "method": "claude",
            "model": model,
            "billing": "subscription (Claude Code)",
            "fallback": [],
        }
    if value == "script":
        if role != "checker":
            raise ConfigError(f'{source}: "script" is only valid for checker')
        return {
            "role": role,
            "value": value,
            "kind": "script",
            "profile": None,
            "method": "script",
            "model": None,
            "billing": "none (script)",
            "fallback": [],
        }
    if role not in PROFILE_ROLES:
        raise ConfigError(
            f"{source}: {value!r} is not valid; only {sorted(PROFILE_ROLES)} may name a "
            f"profile (use one of {sorted(CLAUDE_VALUES)})"
        )
    if value not in profiles:
        known = ", ".join(sorted(profiles)) or "none"
        raise ConfigError(f"{source}: unknown profile {value!r} (known: {known})")
    profile = profiles[value]
    if not profile["enabled"]:
        raise ConfigError(
            f"{source}: profile {value!r} is disabled (enabled = false); an external "
            "role must name an enabled profile"
        )
    chain = [value, *profile["fallback"]]
    if role in PRODUCER_ROLES:
        for name in chain:
            candidate = profiles[name]
            if candidate["driver"] != "cli":
                continue
            if not candidate["read_only_command"]:
                raise ConfigError(
                    f"profiles.{name}.read_only_command: required when this profile "
                    f'serves the {role} role on driver "cli" (a producer role must '
                    "not write; the runner publishes the validated artifact)"
                )
            problem = verify_read_only_command(candidate["read_only_command"])
            if problem is not None:
                raise ConfigError(f"profiles.{name}.read_only_command: {problem}")
    method = "cli" if profile["driver"] == "cli" else "api"
    return {
        "role": role,
        "value": value,
        "kind": "profile",
        "profile": value,
        "method": method,
        "model": profile["model"],
        "billing": profile["billing"],
        "fallback": list(profile["fallback"]),
    }


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
    select_parser = sub.add_parser(
        "select", help="resolve one role for a run, with an optional override"
    )
    select_parser.add_argument("role")
    select_parser.add_argument("--override", help="a profile name overriding [roles]")
    select_parser.add_argument(
        "--project", default=".", help="repository root (default: .)"
    )
    roles_parser.add_argument(
        "--project", default=".", help="repository root (default: .)"
    )
    roles_parser.add_argument("--task-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "role":
            if args.role not in ROLES:
                raise ConfigError(f"unknown role {args.role!r} (use {sorted(ROLES)})")
            _profiles, roles = load(Path(args.project).resolve())
            print(roles.get(args.role, "claude"))
        elif args.command == "roles":
            mapping = resolve_roles(Path(args.project).resolve(), args.task_dir)
            print(json.dumps(mapping, sort_keys=True))
        elif args.command == "select":
            selection = resolve_role_profile(
                Path(args.project).resolve(), args.role, override=args.override
            )
            print(json.dumps(selection, sort_keys=True))
        else:
            project = Path(args.project).resolve()
            profiles, _roles = load(project)
            if args.profile not in profiles:
                known = ", ".join(sorted(profiles)) or "none"
                raise ConfigError(f"unknown profile {args.profile!r} (known: {known})")
            print(show_text(profiles[args.profile]))
            for line in warnings({args.profile: profiles[args.profile]}):
                print(f"models.py: warning: {line}", file=sys.stderr)
    except ConfigError as exc:
        print(f"models.py: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
