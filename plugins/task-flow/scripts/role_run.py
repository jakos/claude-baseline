# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Run an external planner, backlog or reviewer through the trusted role runtime.

The host resolves complete inputs before contact and publishes only validated
artifacts. Models return raw plan/review/backlog Markdown, or the exact JSON
requirements/backlog envelope with --from-repo. The host owns publication,
rollback, nullable invocation metrics and one bounded artifact repair.

Use --role planner --task-dir, --role backlog --run-dir [--from-repo], or
--role reviewer --task-dir --round. All roles require --project and --worktree
(path or none); --profile overrides configured selection for this invocation.
Exit 0: published; 3: valid blocked artifact; 1: execution/validation failure;
2: refused preflight. Status transitions belong to orchestration.
"""

import argparse
import json
import os
import re
import subprocess
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
SUPPORTED_ROLES = {"planner", "backlog", "reviewer"}
ROLE_TO_EXTERNAL = {role: f"external-{role}" for role in SUPPORTED_ROLES}
ARTIFACT_OF_ROLE = {"planner": "plan.md", "backlog": "backlog.md"}

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
    parser.add_argument(
        "--role", required=True, help="the producer role (planner, backlog or reviewer)"
    )
    parser.add_argument("--task-dir", help="absolute .work/<task-id> path")
    parser.add_argument("--run-dir", help="absolute .work/_autopilot/<run-id> path")
    parser.add_argument("--from-repo", action="store_true")
    parser.add_argument("--round", type=int, help="review round, at least one")
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


MAX_REQUIRED_INPUT = 1_000_000


def read_required(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if (
        "\0" in text
        or len(text) > MAX_REQUIRED_INPUT
        or any(m.search(text) for m in role_contracts.TRUNCATION_MARKERS)
    ):
        raise Refusal(f"incomplete or oversized required input: {path.name}")
    return text


def safe_destination(directory: Path, name: str) -> Path:
    path = directory / name
    if (
        path.is_symlink()
        or (path.exists() and not path.is_file())
        or path.resolve().parent != directory.resolve()
    ):
        raise Refusal(f"unsafe artifact destination: {path}")
    return path


def git_input(worktree: Path, argv: list[str]) -> str:
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(worktree), *argv],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise Refusal(f"cannot resolve required git input: {argv[0]}")
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeError as exc:
        raise Refusal("required git input is not readable UTF-8") from exc
    if len(text) > MAX_REQUIRED_INPUT:
        raise Refusal("required git input exceeds the complete-input limit")
    return text


def prepare_read_role(args: argparse.Namespace) -> Context:
    ctx = Context()
    ctx.role = args.role
    ctx.external_role = ROLE_TO_EXTERNAL[args.role]
    ctx.repo_root = Path(args.project).resolve()
    ctx.worktree = (
        ctx.repo_root if args.worktree == "none" else Path(args.worktree).resolve()
    )
    if not ctx.repo_root.is_dir() or not ctx.worktree.is_dir():
        raise Refusal("project and worktree must be existing directories")
    if args.role == "backlog":
        if not args.run_dir or args.task_dir or args.round is not None:
            raise Refusal("backlog requires --run-dir, without --task-dir or --round")
        raw_dir = Path(args.run_dir)
        ctx.task_dir = raw_dir.resolve()
        if (
            raw_dir.is_symlink()
            or ctx.task_dir.parent != ctx.repo_root / ".work" / "_autopilot"
            or not ctx.task_dir.is_dir()
        ):
            raise Refusal(
                "run directory must be .work/_autopilot/<run-id> inside project"
            )
        import autopilot

        ctx.thin = "thin: yes" in autopilot.inspect(str(ctx.worktree)).splitlines()
        ctx.from_repo = args.from_repo
        requirements = safe_destination(ctx.task_dir, "requirements.md")
        ctx.requirements = read_required(requirements) if requirements.exists() else ""
        if not ctx.from_repo and not ctx.requirements.strip():
            raise Refusal("ordinary decomposition requires non-empty requirements.md")
        ctx.artifact_name = "backlog.md"
        ctx.artifacts = (
            ["requirements.md", "backlog.md"] if ctx.from_repo else ["backlog.md"]
        )
        ctx.inputs = [
            ("requirements.md", ctx.requirements),
            ("repository inspection", f"thin: {'yes' if ctx.thin else 'no'}"),
        ]
    else:
        if (
            not args.task_dir
            or args.run_dir
            or args.from_repo
            or args.round is None
            or args.round < 1
        ):
            raise Refusal("reviewer requires --task-dir and --round >= 1")
        raw_dir = Path(args.task_dir)
        ctx.task_dir = raw_dir.resolve()
        if (
            raw_dir.is_symlink()
            or ctx.task_dir.parent != ctx.repo_root / ".work"
            or not ctx.task_dir.is_dir()
        ):
            raise Refusal("task directory must be .work/<task-id> inside project")
        status = read_required(ctx.task_dir / "status.md")
        ctx.round = args.round
        if header_field(status, "round") != str(args.round):
            raise Refusal("requested round does not match status round")
        selected_worktree = header_field(status, "worktree")
        if selected_worktree is None:
            raise Refusal("status must declare the worktree")
        expected_worktree = (
            ctx.repo_root
            if selected_worktree == "none"
            else (ctx.repo_root / selected_worktree).resolve()
        )
        if expected_worktree != ctx.worktree:
            raise Refusal("worktree does not match status")
        base = header_field(status, "base")
        if not base or base.startswith("-"):
            raise Refusal("status has no resolvable base")
        git_input(ctx.worktree, ["rev-parse", "--verify", f"{base}^{{commit}}"])
        ctx.plan = read_required(ctx.task_dir / "plan.md")
        verdict = role_contracts.validate_plan(ctx.plan)
        if not verdict.ok or verdict.blocked:
            raise Refusal(
                "review requires a valid non-blocked plan: "
                + "; ".join(verdict.reasons)
            )
        checks = read_required(ctx.task_dir / f"checks-{args.round}.md")
        baseline_path = ctx.task_dir / "checks-0.md"
        baseline = read_required(baseline_path) if baseline_path.exists() else None
        try:
            ctx.current_checks = role_contracts.parse_checks(
                checks, round_number=args.round, commands=verdict.commands
            )
            ctx.baseline_checks = (
                role_contracts.parse_checks(
                    baseline, round_number=0, commands=verdict.commands
                )
                if baseline
                else None
            )
        except ValueError as exc:
            raise Refusal(f"insufficient check evidence: {exc}") from exc
        diff = git_input(
            ctx.worktree, ["diff", "--no-ext-diff", "--no-textconv", base, "--"]
        )
        touched = git_input(
            ctx.worktree, ["diff", "--name-only", "-z", base, "--"]
        ).split("\0")
        untracked = git_input(
            ctx.worktree, ["ls-files", "--others", "--exclude-standard", "-z"]
        ).split("\0")
        paths = sorted(set(filter(None, touched + untracked)))
        ctx.inputs = [("plan.md", ctx.plan), (f"checks-{args.round}.md", checks)]
        if baseline is not None:
            ctx.inputs.append(("checks-0.md", baseline))
        ctx.inputs.extend([("base diff", diff), ("changed files", "\n".join(paths))])
        for name in paths:
            path = ctx.worktree / name
            resolved = path.resolve()
            if (
                role_runtime.review_excluded(path.relative_to(ctx.worktree))
                or not resolved.is_relative_to(ctx.worktree)
                or role_runtime.review_excluded(resolved.relative_to(ctx.worktree))
            ):
                raise Refusal(
                    f"touched file is an excluded or unsafe review input: {name}"
                )
            content = (
                read_required(path)
                if path.exists()
                else git_input(ctx.worktree, ["show", f"{base}:{name}"])
            )
            if "\0" in content or any(
                marker.search(content) for marker in role_contracts.TRUNCATION_MARKERS
            ):
                raise Refusal(f"incomplete or binary required input: {name}")
            ctx.inputs.append(
                (
                    f"full touched file: {name}"
                    if path.exists()
                    else f"deleted base file: {name}",
                    content,
                )
            )
        sections, _ = role_contracts._sections(ctx.plan)
        names = [
            line.partition(":")[0].strip("` ")
            for line in role_contracts.unfenced(sections["Profiles"])
            if line.strip()
        ]
        for name in names:
            profile_path = PLUGIN_ROOT / "skills" / f"profile-{name}" / "SKILL.md"
            profile_text = read_required(profile_path)
            parts = profile_text.split("## Review checklist\n", 1)
            if len(parts) != 2 or not parts[1].strip():
                raise Refusal(f"missing Review checklist: {name}")
            ctx.inputs.append((f"Profile {name} Review checklist", parts[1]))
        ctx.artifact_name = f"review-{args.round}.md"
        ctx.artifacts = [ctx.artifact_name]
    ctx.task_id = ctx.task_dir.name
    ctx.skill_text = read_required(PLUGIN_ROOT / "skills" / "workflow" / "SKILL.md")
    ctx.agent_text = agent_body(args.role)
    ctx.profile_sections = []
    ctx.profile_override = args.profile
    ctx.heartbeat = args.heartbeat
    ctx.invocation_id = args.invocation_id or (
        utc_now().replace("-", "").replace(":", "") + "-" + uuid.uuid4().hex[:8]
    )
    role_runtime.metrics_name(ctx.role, ctx.invocation_id)
    ctx.started = time.monotonic()
    ctx.started_at = utc_now()
    for name in [
        *ctx.artifacts,
        role_runtime.metrics_name(ctx.role, ctx.invocation_id),
    ]:
        safe_destination(ctx.task_dir, name)
    safe_destination(
        ctx.task_dir,
        "." + role_runtime.metrics_name(ctx.role, ctx.invocation_id) + ".tmp",
    )
    try:
        ctx.profiles, _ = models.load(ctx.repo_root)
        ctx.selection = models.resolve_role_profile(
            ctx.repo_root, ctx.role, override=args.profile
        )
        ctx.independence = models.gates(ctx.repo_root)["reviewer_independence"]
    except models.ConfigError as exc:
        raise Refusal(str(exc)) from exc
    if ctx.selection["kind"] != "profile":
        raise Refusal(
            "role is not configured for external execution; use its Claude agent"
        )
    ctx.profile_name = ctx.selection["profile"]
    profile = ctx.profiles[ctx.profile_name]
    if profile["api_key_env"] and not models.api_key(profile):
        raise Refusal(f"{profile['api_key_env']} is not set")
    if ctx.role == "backlog":
        rules_path = ctx.worktree / ".claude" / "task-flow.md"
        if rules_path.exists():
            ctx.inputs.append(("project rules", read_required(rules_path)))
    if ctx.role == "reviewer":
        # Check the starting candidate now; fallbacks are checked immediately before use.
        check_independence(ctx, ctx.profile_name)
    if len(build_prompt(ctx)) + len(build_system(ctx)) > MAX_REQUIRED_INPUT:
        raise Refusal("required review/backlog inputs exceed the complete-input limit")
    return ctx


def check_independence(ctx: Context, name: str) -> None:
    if ctx.role != "reviewer":
        return
    problem = role_runtime.independence_problem(
        ctx.task_dir, ctx.round, ctx.profiles[name], ctx.independence
    )
    if problem:
        if ctx.independence != "warn":
            raise Refusal(f"reviewer independence ({name}): {problem}")
        log(f"warning: reviewer independence ({name}): {problem}")


def prepare(args) -> Context:
    if args.role in ("backlog", "reviewer"):
        return prepare_read_role(args)
    if not args.task_dir or args.run_dir or args.from_repo:
        raise Refusal(
            "planner requires --task-dir; --run-dir and --from-repo are backlog-only"
        )
    ctx = Context()
    if args.role not in SUPPORTED_ROLES:
        raise Refusal(
            f"--role {args.role!r} is not supported by the external runner "
            f"(supported: {sorted(SUPPORTED_ROLES)})"
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
    if ctx.role != "planner":
        sections = [
            f"Role: {ctx.role}\nArtifact directory: {ctx.task_dir}\nRepository root: {ctx.repo_root}\nWorktree: {ctx.worktree}",
            ctx.skill_text,
        ]
        sections.extend(
            f'<file label="{name}">\n{text}\n</file>' for name, text in ctx.inputs
        )
        return "\n\n".join(sections)
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
    if ctx.role == "planner":
        parts.append(ROLE_ADAPTER.strip())
    else:
        output = (
            'a JSON object containing exactly "requirements.md" and "backlog.md", each a Markdown string'
            if ctx.role == "backlog" and ctx.from_repo
            else ctx.artifact_name + " as raw Markdown"
        )
        parts.append(
            "## Role runner adapter (overrides conflicting agent instructions)\nYou are read-only. Never write artifacts or status. Return only "
            + output
            + ". The host validates and publishes; exactly one artifact repair is allowed. Do not wrap the response in a code fence. A blocked backlog has BLOCKED: yes followed by questions under Risks / open questions; do not create tasks. For repository gathering preserve existing requirements verbatim and cite each added bullet with file:line. Review only the supplied evidence and permitted source files; do not retrieve status, implementer narratives or prior reviews."
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------- run


def validate(text: str, ctx: Context):
    """The validator hook: role_contracts against the actual base's gate and manifest."""
    if ctx.role == "backlog":
        try:
            outputs = (
                role_contracts.backlog_envelope(text)
                if ctx.from_repo
                else {"backlog.md": text}
            )
        except ValueError as exc:
            return False, False, [str(exc)]
        verdict = role_contracts.validate_backlog(
            outputs["backlog.md"], run_id=ctx.task_id, thin=ctx.thin
        )
        if ctx.from_repo:
            requirements = role_contracts.validate_requirements(
                outputs["requirements.md"], original=ctx.requirements
            )
            verdict.reasons.extend(requirements.reasons)
            verdict.ok = verdict.ok and requirements.ok
        return verdict.ok, verdict.blocked, verdict.reasons
    if ctx.role == "reviewer":
        verdict = role_contracts.validate_review(
            text,
            round_number=ctx.round,
            plan=ctx.plan,
            current=ctx.current_checks,
            baseline=ctx.baseline_checks,
        )
        return verdict.ok, False, verdict.reasons
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
    if ctx.role == "reviewer":
        tools.review_inputs = {
            (ctx.task_dir / name).resolve()
            for name in ("plan.md", "checks-0.md", f"checks-{ctx.round}.md")
        }
    session = role_runtime.RoleSession(
        ctx.profiles,
        ctx.profile_name,
        build_system(ctx),
        build_prompt(ctx),
        tools,
        lambda text: validate(text, ctx),
        heartbeat=ctx.heartbeat,
        candidate_check=lambda name: check_independence(ctx, name),
        cli_once=lambda profile, extra: run_cli_once(ctx, profile, extra),
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
        check_independence(ctx, name)
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
                "\n\nYour previous answer was not a valid role artifact. Fix exactly "
                "these problems and return the complete role artifact again (required format, no code "
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


def publish_outputs(directory: Path, outputs: dict[str, str], primary: str) -> Path:
    """Stage all validated bytes; roll back all handled replacement failures."""
    originals = {}
    staged = {}
    try:
        for name, text in outputs.items():
            destination = safe_destination(directory, name)
            originals[name] = destination.read_bytes() if destination.exists() else None
            import tempfile

            descriptor, raw = tempfile.mkstemp(prefix=".role-", dir=directory)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(text.replace("\r\n", "\n").encode("utf-8"))
            staged[name] = Path(raw)
        replaced = []
        try:
            for name in outputs:
                os.replace(staged[name], safe_destination(directory, name))
                replaced.append(name)
        except (OSError, Refusal):
            for name in reversed(replaced):
                destination = directory / name
                if originals[name] is None:
                    destination.unlink()
                else:
                    destination.write_bytes(originals[name])
            raise
    except OSError as exc:
        raise Refusal(
            f"artifact publication failed; original outputs restored: {exc}"
        ) from exc
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)
    return directory / primary


def publish(ctx: Context, text: str) -> Path:
    """Atomically replace only the validated artifact under the root task directory.

    The destination must be the task dir's own plan.md — a regular path inside .work,
    not a symlink, not escaping the task dir — and the write is a temp file plus
    os.replace, so a crash never leaves a half-written artifact and a refusal leaves
    the existing bytes untouched.
    """
    if ctx.role != "planner":
        outputs = (
            role_contracts.backlog_envelope(text)
            if ctx.role == "backlog" and ctx.from_repo
            else {ctx.artifact_name: text}
        )
        return publish_outputs(ctx.task_dir, outputs, ctx.artifact_name)
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
                "model": ctx.profiles[entry["profile"]]["model"],
                "method": "cli"
                if ctx.profiles[entry["profile"]]["driver"] == "cli"
                else "api",
                "billing": ctx.profiles[entry["profile"]]["billing"],
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
        "round": getattr(ctx, "round", None),
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
        "tokens_known": ledger.tokens_known() if ledger is not None else None,
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

    try:
        result = run_api(ctx)
    except (role_runtime.Fatal, Refusal) as exc:
        result = role_runtime.SessionResult("failed", str(exc), None, [], 0, 0, None)
    except (OSError, UnicodeDecodeError) as exc:
        result = role_runtime.SessionResult(
            "failed", f"runtime error: {exc}", None, [], 0, 0, None
        )
    ledger = result.ledger or role_runtime.UsageLedger(ctx.profiles)

    record = build_metrics(ctx, result, ledger)
    try:
        role_runtime.write_role_metrics(ctx.task_dir, record)
    except (Refusal, OSError) as exc:
        print(f"failed: cannot write role metrics: {exc}", file=sys.stderr)
        return 1

    if result.status not in ("ok", "blocked"):
        print(f"failed: {one_line(result.reason, 300)}")
        return 1
    try:
        destination = publish(ctx, result.text or "")
    except (Refusal, OSError) as exc:
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
