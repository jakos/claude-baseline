# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Run ONE external producer role (the planner today) and publish its validated artifact.

This is the trusted runner of a role adapter: the model (a configured API or CLI
profile) only reads the repository and returns artifact text; this script validates the
text against scripts/role_contracts.py and atomically publishes only the validated
plan.md under the root task directory. The agent body and contract are the same as the
Claude planner's (agents/planner.md plus skills/workflow/SKILL.md); only the transport
adapter differs — the model cannot write plan.md, the runner does.

Usage:
  uv run --script plugins/task-flow/scripts/role_run.py \\
      --role planner --task-dir <absolute .work/<task-id>> --project <repo-root> \\
      --worktree <path|none> [--profile <name>] [--invocation-id <id>] \\
      [--stack-manifest <manifest.json>] [--heartbeat <seconds>]

  --role            the producer role; only "planner" is implemented (exit 2 otherwise)
  --task-dir        the task's .work/<task-id> folder (absolute); the repository root is
                    the parent of the .work folder that contains it, --project overrides
  --project         the repository root that holds .claude/ and the worktree
  --worktree        the task's worktree, or `none` for the repository root
  --profile         a [profiles.<name>] overriding [roles] planner for this invocation;
                    it must name an enabled configured profile (models.py select)
  --invocation-id   the metrics invocation id (default: a fresh UTC-stamped one)
  --stack-manifest  a JSON file with the machine-built stack/dependency manifest
                    ({"satisfied": [...]}); without it the manifest is built from
                    status.md's base/branch fields only
  --heartbeat       seconds between heartbeat lines on stderr (default 30; 0 disables)

Preflight (exit 2, nothing sent, nothing written): the task dir, project, worktree and
required readable inputs (task.md, status.md, the workflow contract, the agent body, the
profile Conventions, the actual base's project gate, the stack manifest) resolve before
any model contact; the role resolves to an enabled profile (never Claude — with no
external planner configured the orchestrator runs the Claude subagent instead); a CLI
producer profile and its fallbacks carry a verified read_only_command.

Prompt: agents/planner.md's body, the applicable profile Conventions, the workflow
contract (skills/workflow/SKILL.md), the project rules (.claude/task-flow.md of the
worktree = the actual stack base), the task files (task.md, status.md), the base's
Verification commands gate and the stack manifest, then the role transport adapter.

Result handling: the model's final message is validated by role_contracts.validate_plan
against the actual base's gate. On one invalid result exactly one repair attempt is made
with the bounded validation reasons (one extra conversation turn on an API driver, one
extra CLI process), then the run fails. Provider retries and fallback stay bounded
independently and never silently select Claude. The validated artifact (blocked or not)
is atomically written to <task-dir>/plan.md — rejecting unsafe destinations and symlink
escapes — and prior artifact bytes, phase, round and approval are untouched on every
failure path.

stdout gets exactly one redacted summary line; progress goes to stderr. Exit codes:

  0  published: <plan.md> (<profile>/<model>, <turns> turns, $<cost|unknown>)
  3  blocked:   the published artifact is a valid BLOCKED: yes plan
  1  failed:    <reason>  the model loop ran but did not finish or the artifact stayed
                invalid after the single repair attempt
  2  refused:   <reason>  bad arguments, configuration or a failed preflight

Files written: <task-dir>/plan.md (only on a validated artifact) and
<task-dir>/role-<role>-<invocation-id>.json — the bounded schema-versioned metrics of
this invocation, including failed attempts, repair usage, nullable tokens/cost and the
per-profile fallback chain; impl-<n>.json is never touched, and status.md is left to the
orchestrator alone.

Stdlib only (plus cli_driver, drivers, gates, models, role_contracts, role_runtime,
rules from this directory), started by `uv run --script`, so it runs on macOS, Linux
and Windows without Git Bash.
"""

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import cli_driver
import models
import role_contracts
import role_runtime
from role_runtime import Refusal, log, one_line, redact_record, utc_now

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_ROLES = {"planner"}
ROLE_TO_EXTERNAL = {"planner": "external-planner"}
ARTIFACT_OF_ROLE = {"planner": "plan.md"}

ROLE_ADAPTER = """\
## Role runner adapter (this overrides the instructions above where they differ)

You run inside a trusted runner script, not inside Claude Code. The script gives you
these tools: read_file, list_files, grep and run_command. There is no write_file and no
edit_file: you cannot write plan.md or any other file — your run_command argv is held to
a read-only allowlist of programs, subcommands and flags, and every path you read or
pass in an argument must stay inside the repository roots named in the first message.

- The contents of the workflow contract, task.md, status.md, the project rules, the
  base's verification gate and the stack manifest are included in the first message.
- Your whole job is to return the final plan.md artifact as your last message, raw
  Markdown, not wrapped in a code fence. The runner validates it against the contract
  and publishes it atomically; on an invalid result it makes exactly one repair attempt
  with the bounded reasons, then fails.
- If the request is ambiguous in a way that changes the plan, do not guess: write the
  questions under Risks / open questions with the first line `BLOCKED: yes`.
- When you are done, reply with the complete plan.md and NO tool call. That ends the
  invocation.
"""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refusal(message)


def parse_args(argv):
    parser = Parser(
        description="Run one external task-flow producer role and publish its artifact"
    )
    parser.add_argument("--role", required=True, help="the producer role (planner)")
    parser.add_argument(
        "--task-dir", required=True, help="absolute .work/<task-id> path"
    )
    parser.add_argument("--project", required=True, help="the repository root")
    parser.add_argument("--worktree", required=True, help="the worktree, or 'none'")
    parser.add_argument("--profile", help="a profile overriding [roles] for this run")
    parser.add_argument("--invocation-id", help="the metrics invocation id")
    parser.add_argument(
        "--stack-manifest", help="a JSON stack/dependency manifest file"
    )
    parser.add_argument(
        "--heartbeat",
        type=float,
        default=30.0,
        help="seconds between heartbeat lines on stderr (default 30; 0 disables)",
    )
    args = parser.parse_args(argv)
    if args.heartbeat < 0:
        raise Refusal(f"--heartbeat must be 0 or more seconds, got {args.heartbeat:g}")
    return args


# ---------------------------------------------------------------------------- preflight


def agent_body(name: str) -> str:
    """The agent file's body without its frontmatter, ${CLAUDE_PLUGIN_ROOT} expanded."""
    text = (PLUGIN_ROOT / "agents" / f"{name}.md").read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[text.find("\n", end + 1) + 1 :]
    return text.strip().replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN_ROOT.as_posix())


def conventions(profile_name: str) -> str | None:
    """The Conventions section of a profile SKILL.md, or None when there is none."""
    path = PLUGIN_ROOT / "skills" / f"profile-{profile_name}" / "SKILL.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^## Conventions\s*$", text, re.MULTILINE)
    if not match:
        return None
    rest = text[match.start() :]
    nxt = re.search(r"^## (?!Conventions)", rest, re.MULTILINE)
    return (rest[: nxt.start()] if nxt else rest).strip()


def header_field(text: str, key: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("- "):
            name, sep, value = line[2:].partition(":")
            if sep and name.strip() == key:
                return value.strip()
    return None


def build_stack_manifest(
    status_text: str, task_id: str, manifest_file: Path | None
) -> dict:
    """The machine-built stack/dependency manifest: never a model rewrite.

    From status.md's base/branch/worktree fields, plus the optional JSON file's
    "satisfied" prerequisite list (declared earlier stack tasks satisfied by stack
    order, even when their source is unmerged on the current checkout).
    """
    manifest = {
        "task-id": task_id,
        "base": header_field(status_text, "base"),
        "branch": header_field(status_text, "branch"),
        "worktree": header_field(status_text, "worktree"),
        "satisfied": [],
    }
    if manifest_file is not None:
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise Refusal(
                f"--stack-manifest: cannot read {manifest_file}: {exc}"
            ) from exc
        if not isinstance(data, dict) or not isinstance(
            data.get("satisfied", []), list
        ):
            raise Refusal(
                f"--stack-manifest: {manifest_file} must be a JSON object with a "
                '"satisfied" list of prerequisite ids'
            )
        manifest["satisfied"] = [str(item) for item in data["satisfied"]]
        manifest["source"] = str(manifest_file)
    return manifest


def gate_commands_of_base(worktree: Path) -> list[list[str]]:
    """The actual stack base's project gate: the worktree's .claude/task-flow.md block.

    The worktree is the selected actual base's checkout, so its own gate is the
    inherited check list — not stale root main.
    """
    path = worktree / ".claude" / "task-flow.md"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Refusal(
            f"the actual base has no project gate: {path} is missing"
        ) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise Refusal(f"cannot read the base's project gate {path}: {exc}") from exc
    try:
        commands = role_contracts._gate_argvs(text)
    except ValueError as exc:
        raise Refusal(f"{path}: refused command line: {exc}") from exc
    if not commands:
        raise Refusal(f"{path}: no Verification commands block, or it is empty")
    return commands


class Context:
    """Everything preflight resolved; nothing here contacts a model."""


def prepare(args) -> Context:
    ctx = Context()
    if args.role not in SUPPORTED_ROLES:
        raise Refusal(
            f"--role {args.role!r} is not supported by the external runner "
            f"(supported: {sorted(SUPPORTED_ROLES)}); backlog and reviewer are a later task"
        )
    ctx.role = args.role
    ctx.external_role = ROLE_TO_EXTERNAL[args.role]
    ctx.artifact_name = ARTIFACT_OF_ROLE[args.role]
    ctx.task_dir = Path(args.task_dir).resolve()
    if not ctx.task_dir.is_dir():
        raise Refusal(f"task dir not found: {ctx.task_dir}")
    if ctx.task_dir.parent.name != ".work":
        raise Refusal(f"task dir must be inside a .work folder: {ctx.task_dir}")
    ctx.repo_root = Path(args.project).resolve()
    if not ctx.repo_root.is_dir():
        raise Refusal(f"project root not found: {ctx.repo_root}")
    if ctx.repo_root != ctx.task_dir.parent.parent:
        log(
            f"note: --project {ctx.repo_root} is not the parent of {ctx.task_dir}'s "
            ".work folder; the task dir wins for the repository root"
        )
        ctx.repo_root = ctx.task_dir.parent.parent
    ctx.task_id = ctx.task_dir.name

    for name in ("task.md", "status.md"):
        if not (ctx.task_dir / name).is_file():
            raise Refusal(f"{name} is missing in {ctx.task_dir}")
    ctx.task_text = (ctx.task_dir / "task.md").read_text(encoding="utf-8")
    ctx.status_text = (ctx.task_dir / "status.md").read_text(encoding="utf-8")

    if args.worktree == "none":
        ctx.worktree = ctx.repo_root
    else:
        ctx.worktree = Path(args.worktree).resolve()
    if not ctx.worktree.is_dir():
        raise Refusal(f"worktree not found: {ctx.worktree}")

    try:
        ctx.profiles, _roles = models.load(ctx.repo_root)
    except models.ConfigError as exc:
        raise Refusal(f"invalid configuration: {exc}") from exc
    try:
        ctx.selection = models.resolve_role_profile(
            ctx.repo_root, args.role, override=args.profile
        )
    except models.ConfigError as exc:
        raise Refusal(str(exc)) from exc
    if ctx.selection["kind"] != "profile":
        raise Refusal(
            f"the {args.role} role is {ctx.selection['kind']!r}, not an external profile; "
            "with no external planner configured the orchestrator runs the Claude "
            "subagent — nothing for this runner to do"
        )
    ctx.profile_name = ctx.selection["profile"]
    profile = ctx.profiles[ctx.profile_name]
    if profile["api_key_env"] and not models.api_key(profile):
        raise Refusal(
            f"profile {ctx.profile_name!r}: environment variable "
            f"{profile['api_key_env']} is not set"
        )

    # required readable inputs, resolved before any model contact
    ctx.skill_text = (PLUGIN_ROOT / "skills" / "workflow" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    ctx.agent_text = agent_body(args.role)
    ctx.profile_sections = []
    for name in sorted(role_contracts.KNOWN_PROFILES):
        section = conventions(name)
        if section is not None:
            ctx.profile_sections.append(f"# Profile `{name}`\n\n{section}")
    project_rules_path = ctx.worktree / ".claude" / "task-flow.md"
    ctx.project_rules = (
        project_rules_path.read_text(encoding="utf-8")
        if project_rules_path.is_file()
        else ""
    )
    ctx.gate_commands = gate_commands_of_base(ctx.worktree)
    manifest_file = Path(args.stack_manifest).resolve() if args.stack_manifest else None
    if manifest_file is not None and not manifest_file.is_file():
        raise Refusal(f"--stack-manifest not found: {manifest_file}")
    ctx.stack = build_stack_manifest(ctx.status_text, ctx.task_id, manifest_file)

    ctx.invocation_id = args.invocation_id or (
        utc_now().replace("-", "").replace(":", "") + "-" + uuid.uuid4().hex[:8]
    )
    ctx.heartbeat = args.heartbeat
    ctx.profile_override = args.profile
    ctx.started = time.monotonic()
    ctx.started_at = utc_now()
    return ctx


# ---------------------------------------------------------------------------- prompt


def build_prompt(ctx: Context) -> str:
    """The whole user message: paths, the contract, rules, gate, manifest, task files."""
    sections = [
        f"Role: {ctx.role}",
        f"Task directory: {ctx.task_dir}",
        f"Repository root: {ctx.repo_root}",
        (
            f"Worktree (your working directory and the root of your file tools): "
            f"{ctx.worktree}"
        ),
        f"Plugin root: {PLUGIN_ROOT}",
        "",
        (
            "The files below are included for you; your file tools reach the "
            "repository roots named above and cannot write anything."
        ),
    ]
    files = [
        ("workflow contract (skills/workflow/SKILL.md)", ctx.skill_text),
        ("project rules (.claude/task-flow.md of the actual base)", ctx.project_rules),
        ("task.md", ctx.task_text),
        ("status.md", ctx.status_text),
    ]
    for label, text in files:
        sections.append(f'<file label="{label}">\n{text}\n</file>')
    gate_lines = [" ".join(f'"{arg}"' for arg in argv) for argv in ctx.gate_commands]
    sections.append(
        "<file label=\"the actual stack base's project gate (.claude/task-flow.md "
        "Verification commands; your plan's verification list must retain every one of "
        'these inherited checks)">\n```\n' + "\n".join(gate_lines) + "\n```\n</file>"
    )
    sections.append(
        '<file label="stack/dependency manifest (machine-built)">\n'
        + json.dumps(ctx.stack, ensure_ascii=False, indent=1)
        + "\n</file>"
    )
    return "\n\n".join(sections)


def build_system(ctx: Context) -> str:
    parts = [ctx.agent_text]
    parts.extend(ctx.profile_sections)
    parts.append(ROLE_ADAPTER.strip())
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------- run


def validate(text: str, ctx: Context):
    """The validator hook: role_contracts against the actual base's gate and manifest."""
    verdict = role_contracts.validate_plan(
        text, gate_commands=ctx.gate_commands, stack=ctx.stack
    )
    return verdict.ok, verdict.blocked, verdict.reasons


def run_api(ctx: Context) -> role_runtime.SessionResult:
    tools = role_runtime.ProducerTools(
        ctx.worktree,
        [ctx.worktree, PLUGIN_ROOT, ctx.task_dir, ctx.repo_root],
        ctx.external_role,
        ctx.profiles[ctx.profile_name]["command_timeout_seconds"],
    )
    session = role_runtime.RoleSession(
        ctx.profiles,
        ctx.profile_name,
        build_system(ctx),
        build_prompt(ctx),
        tools,
        lambda text: validate(text, ctx),
        heartbeat=ctx.heartbeat,
    )
    return session.run()


def run_cli_once(ctx: Context, profile: dict, extra_prompt: str = ""):
    prompt = build_system(ctx) + "\n\n" + build_prompt(ctx)
    return role_runtime.run_cli_producer(
        profile, prompt, ctx.worktree, extra_prompt=extra_prompt
    )


def run_cli(ctx: Context) -> role_runtime.SessionResult:
    """One CLI producer run with exactly one repair process on an invalid result."""
    profile = ctx.profiles[ctx.profile_name]
    chain: list[dict] = []
    repairs = 0
    candidates = [ctx.profile_name, *profile["fallback"]]
    ledger = role_runtime.UsageLedger(ctx.profiles)
    for index, name in enumerate(candidates):
        candidate = ctx.profiles[name]
        if index > 0:
            skip = None
            if not candidate["enabled"]:
                skip = "disabled"
            elif candidate["api_key_env"] and not models.api_key(candidate):
                skip = f"{candidate['api_key_env']} is not set"
            if skip:
                chain.append(
                    {
                        "profile": name,
                        "outcome": "skipped",
                        "reason": skip,
                        "retries": [],
                    }
                )
                continue
        try:
            result = run_cli_once(ctx, candidate)
        except cli_driver.CliRefused as exc:
            chain.append(
                {
                    "profile": name,
                    "outcome": "unavailable",
                    "reason": str(exc),
                    "retries": [],
                }
            )
            log(f"profile {name} unavailable ({exc}); trying the next fallback")
            continue
        ledger.add_cli(name, result.usage, result.usage_malformed)
        limit_hit = False
        for pattern in candidate["limit_patterns"]:
            if re.search(pattern, result.stderr) or re.search(
                pattern, result.final_message or ""
            ):
                limit_hit = True
        if limit_hit:
            chain.append(
                {
                    "profile": name,
                    "outcome": "unavailable",
                    "reason": "a limit_pattern matched the bounded output",
                    "retries": [],
                }
            )
            continue
        if result.timed_out:
            chain.append(
                {
                    "profile": name,
                    "outcome": "failed",
                    "reason": f"timed out after {candidate['timeout_seconds']:g} s and was killed",
                    "retries": [],
                }
            )
            return _cli_result(
                ctx, "failed", chain[-1]["reason"], None, ledger, repairs, chain
            )
        if result.failure_event or (result.exit_code not in (0, None)):
            chain.append(
                {
                    "profile": name,
                    "outcome": "failed",
                    "reason": f"exit code {result.exit_code}, failure event: {result.failure_event}",
                    "retries": [],
                }
            )
            return _cli_result(
                ctx, "failed", chain[-1]["reason"], None, ledger, repairs, chain
            )
        text = result.final_message or ""
        ok, blocked, reasons = validate(text, ctx)
        if ok:
            return _cli_result(
                ctx,
                "blocked" if blocked else "ok",
                "finished",
                text,
                ledger,
                repairs,
                chain,
                name,
            )
        if repairs == 0:
            repairs += 1
            bounded = "\n".join(f"- {reason}" for reason in reasons[:10])
            extra = (
                "\n\nYour previous answer was not a valid plan.md artifact. Fix exactly "
                "these problems and return the complete, raw plan.md again (no code "
                f"fence around it):\n{bounded}\n"
            )
            try:
                retry = run_cli_once(ctx, candidate, extra_prompt=extra)
            except cli_driver.CliRefused as exc:
                chain.append(
                    {
                        "profile": name,
                        "outcome": "unavailable",
                        "reason": str(exc),
                        "retries": [],
                    }
                )
                continue
            ledger.add_cli(name, retry.usage, retry.usage_malformed)
            text = retry.final_message or ""
            ok, blocked, reasons = validate(text, ctx)
            if ok:
                return _cli_result(
                    ctx,
                    "blocked" if blocked else "ok",
                    "finished",
                    text,
                    ledger,
                    repairs,
                    chain,
                    name,
                )
            return _cli_result(
                ctx,
                "failed",
                "the artifact stayed invalid after the single repair attempt: "
                + "; ".join(reasons[:5]),
                text,
                ledger,
                repairs,
                chain,
                name,
            )
        return _cli_result(
            ctx,
            "failed",
            "the artifact was invalid: " + "; ".join(reasons[:5]),
            text,
            ledger,
            repairs,
            chain,
            name,
        )
    reasons = "; ".join(
        f"{c['profile']}: {c['reason']}" for c in chain if c["outcome"] != "ok"
    )
    return _cli_result(
        ctx,
        "failed",
        f"all profiles unavailable ({reasons})",
        None,
        ledger,
        repairs,
        chain,
    )


def _cli_result(ctx, status, reason, text, ledger, repairs, chain, profile_name=None):
    # A validation outcome (ok/blocked/failed after the repair attempt) belongs to
    # the profile that produced it: record it in the chain exactly once, like the
    # API session does, so the metrics name the profile that ran.
    if profile_name is not None and (
        not chain or chain[-1].get("profile") != profile_name
    ):
        chain.append(
            {
                "profile": profile_name,
                "outcome": status,
                "reason": reason,
                "retries": [],
            }
        )
    result = role_runtime.SessionResult(
        status, reason, text, chain, repairs, turns=1 if text else 0, ledger=ledger
    )
    return result


# ---------------------------------------------------------------------------- publish


def publish(ctx: Context, text: str) -> Path:
    """Atomically replace only the validated artifact under the root task directory.

    The destination must be the task dir's own plan.md — a regular path inside .work,
    not a symlink, not escaping the task dir — and the write is a temp file plus
    os.replace, so a crash never leaves a half-written artifact and a refusal leaves
    the existing bytes untouched.
    """
    destination = ctx.task_dir / ctx.artifact_name
    if destination.is_symlink():
        raise Refusal(f"refusing to publish through a symlink: {destination}")
    resolved = destination.resolve()
    try:
        resolved.relative_to(ctx.task_dir.resolve())
    except ValueError:
        raise Refusal(
            f"the artifact destination escapes the task dir: {destination}"
        ) from None
    if ctx.task_dir.resolve().parent.name != ".work":
        raise Refusal(f"not inside a .work directory: {ctx.task_dir}")
    temp = ctx.task_dir / f".{ctx.artifact_name}.tmp"
    temp.write_text(text, encoding="utf-8", newline="")
    os.replace(temp, destination)
    return destination


# ---------------------------------------------------------------------------- metrics


def build_metrics(ctx: Context, result, ledger) -> dict:
    profiles_used = []
    for entry in result.chain:
        profiles_used.append(
            {
                "profile": entry["profile"],
                "outcome": entry["outcome"],
                "reason": entry.get("reason"),
                "retries": entry.get("retries", []),
            }
        )
    tokens = ledger.tokens() if ledger is not None else None
    record = {
        "schema": role_runtime.METRICS_SCHEMA,
        "role": ctx.role,
        "invocation_id": ctx.invocation_id,
        "task_id": ctx.task_id,
        "artifact": ctx.artifact_name,
        "selection": {
            "profile": ctx.selection["profile"],
            "method": ctx.selection["method"],
            "model": ctx.selection["model"],
            "billing": ctx.selection["billing"],
            "override": args_profile(ctx),
        },
        "transport": ctx.selection["method"],
        "result": result.status,
        "reason": one_line(result.reason, 500),
        "repair_attempts": result.repairs,
        "turns": result.turns,
        "started_at": ctx.started_at,
        "finished_at": utc_now(),
        "duration_seconds": round(time.monotonic() - ctx.started, 3),
        "profiles": profiles_used,
        "tokens": tokens,
        "cost_usd": ledger.cost() if ledger is not None else None,
        "cost_known_usd": ledger.cost_known() if ledger is not None else 0.0,
    }
    secrets = cli_driver.secret_values(
        os.environ,
        [p.get("api_key_env") for p in ctx.profiles.values()],
    )
    return redact_record(record, secrets)


def args_profile(ctx) -> str | None:
    """The --profile override of this invocation, or None when [roles] was used."""
    return ctx.profile_override


# ---------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    try:
        ctx = prepare(args)
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError) as exc:
        print(f"refused: cannot read an input: {exc}", file=sys.stderr)
        return 2

    profile = ctx.profiles[ctx.profile_name]
    try:
        if profile["driver"] == "cli":
            result = run_cli(ctx)
        else:
            result = run_api(ctx)
    except role_runtime.Fatal as exc:
        result = role_runtime.SessionResult("failed", str(exc), None, [], 0, 0, None)
    except (OSError, UnicodeDecodeError) as exc:
        result = role_runtime.SessionResult(
            "failed", f"runtime error: {exc}", None, [], 0, 0, None
        )
    ledger = result.ledger or role_runtime.UsageLedger(ctx.profiles)

    record = build_metrics(ctx, result, ledger)
    try:
        role_runtime.write_role_metrics(ctx.task_dir, record)
    except Refusal as exc:
        print(f"failed: cannot write role metrics: {exc}", file=sys.stderr)
        return 1

    if result.status not in ("ok", "blocked"):
        print(f"failed: {one_line(result.reason, 300)}")
        return 1
    try:
        destination = publish(ctx, result.text or "")
    except Refusal as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    summary = (
        f"published: {destination.name} ({ctx.selection['profile']}/"
        f"{ctx.selection['model']}, {result.repairs} repair(s), {ledger.cost_text()})"
    )
    print(summary)
    return 3 if result.status == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
