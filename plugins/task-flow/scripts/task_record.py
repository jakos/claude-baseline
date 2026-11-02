# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Render the opt-in finish-time task record and its commit trailers (deterministic).

Usage (from the repository root, or pass explicit paths):

  uv run --script plugins/task-flow/scripts/task_record.py render  --repo <root> --task-dir <abs .work/<id>> [--worktree <path|none>] [--config <path>]
  uv run --script plugins/task-flow/scripts/task_record.py trailers --repo <root> --task-dir <abs .work/<id>> [--worktree <path|none>] [--config <path>]

A repository opts in with `.claude/task-flow.records.toml` in the selected worktree
(or the repository root when the task has none; `--config <path>` overrides):

  [records]
  enabled = true

The file is parsed strictly with tomllib — `[records]` is the only table and
`enabled` its only key, a boolean. A missing file or `enabled = false` is disabled:
both commands exit 0, write nothing, print no trailers and require no task artifacts,
so finish keeps its previous behaviour. An unknown key or a wrong type is an error.

When enabled, both commands read only the root task artifacts of `--task-dir`
(`<repo>/.work/<id>`): `status.md` (task id, round, plan-approved), `plan.md`
(`## Acceptance criteria`), `summary.md` (`## Goal`, `## Out of scope`),
`review-<n>.md` for every round 1..round (`## Acceptance criteria` table,
`## Verdict:`) and the optional `impl-<n>.json` (`profile`, `model`; `unknown` when
absent). Nothing is rewritten by a model; malformed or missing artifacts, duplicate
or missing AC rows, inconsistent rounds and a non-APPROVED final verdict are
refused (exit 2) before any output.

- `render` writes one bounded, schema-versioned (`task-record/1`), UTF-8/LF
  `docs/tasks/<id>.md` into the worktree (or the repository root when
  `--worktree none`): task id, goal, every AC text with its final result, per-round
  verdict and implementer, approval source, final verdict, risk availability and
  omitted scope. No timestamps, no absolute paths; all prose is scrubbed of file
  paths, credential URLs, tokens, private keys, secret assignments and
  attribution/session markers, with visible truncation at fixed limits.
  Regeneration is byte-identical (a no-op); a different existing record, an unsafe
  destination (invalid id, traversal, symlink escape) or any refusal changes no file.
- `trailers` prints the sanitized trailer block for the commit message, one
  `Key: value` per line: `Task-Id`, `Task-Rounds`, repeated `Task-Implementer:
  <profile>/<model>` for the distinct executed pairs and `Task-Plan-Approved:
  user|auto`. `Task-Risk` is not emitted while no merged risk schema exists. Values
  are validated single-line metadata without control characters.

Exit codes: 0 success (or disabled no-op); 2 refusal — invalid config, malformed or
missing artifacts, unsafe destination, conflicting record or unsafe residual input;
the message on stderr names the file and field, never the sensitive value. The helper
never commits, pushes, opens PRs, contacts models or prunes.

Stdlib only, started by `uv run --script`, so it runs on macOS, Linux and Windows.
"""

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_RELPATH = Path(".claude") / "task-flow.records.toml"
RECORDS_RELPATH = Path("docs") / "tasks"
SCHEMA = "task-record/1"
TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
STATUS_ID_RE = re.compile(r"^#\s+Status:\s*(\S+)\s*$")
ROUND_LINE_RE = re.compile(r"^-\s+round:\s*(\d+)\s*$")
APPROVED_LINE_RE = re.compile(r"^-\s+plan-approved:\s*(.*)$")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
AC_LINE_RE = re.compile(r"^AC(\d+):\s*(.*)$")
AC_ROW_RE = re.compile(r"^AC(\d+)$")
VERDICT_RE = re.compile(r"^##\s+Verdict:\s*(\S+)\s*$")

MAX_GOAL_CHARS = 500
MAX_SCOPE_CHARS = 1000
MAX_AC_CHARS = 400
MAX_RECORD_BYTES = 64 * 1024
MAX_TRAILER_VALUE_CHARS = 100
TRUNCATED = "… [truncated]"
RISK_TEXT = "unavailable (no merged risk schema)"
UNKNOWN = "unknown"
VERDICTS = ("APPROVED", "CHANGES_REQUESTED")
AC_RESULTS = ("PASS", "FAIL")
APPROVALS = ("user", "auto")
REDACTED = "<redacted>"
IMPLEMENTER_PART_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]*")

# Scrubbing patterns, applied in this order. Narrow and documented on purpose: the
# helper does not claim to detect arbitrary secrets in prose.
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
URL_CREDENTIALS_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]+://)[^\s/@:]+:[^\s/@]+@")
TOKEN_RES = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_\-]{22,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization|auth|"
    r"private[_-]?key)\b(\s*[:=]\s*)[^\s,;]+"
)
PATH_RES = (
    re.compile(r"\\\\[^\s<>\"'`|]+"),
    re.compile(r"(?<![:\w])[A-Za-z]:[\\/][^\s<>\"'`|]*"),
    re.compile(
        r"(?<![\w@.])/(?:Users|home|tmp|temp|var|opt|etc|root|mnt|srv|usr|local|"
        r"private|workspace)(?:/[^\s<>\"'`|]*)?"
    ),
    re.compile(r"~/[^\s<>\"'`|]*"),
)
ATTRIBUTION_RES = (
    re.compile(r"(?i)co-authored-by:[^\n]*\b(?:claude|anthropic)\b"),
    re.compile(r"(?i)claude-session:[^\n]*"),
    re.compile(r"(?i)claude\.ai/code/session[^\n]*"),
    re.compile(r"(?i)generated with[^\n]*claude code[^\n]*"),
)


class RecordError(Exception):
    """A refusal: the message goes to stderr, no file is changed."""


@dataclass
class RoundInfo:
    number: int
    verdict: str
    results: dict[str, str]
    implementer: tuple[str, str]


@dataclass
class Record:
    task_id: str
    goal: str
    approval: str
    omitted_scope: str
    acs: list[tuple[str, str]]
    rounds: list[RoundInfo]

    @property
    def final_verdict(self) -> str:
        return self.rounds[-1].verdict

    def implementer_pairs(self) -> list[str]:
        """The distinct executed `profile/model` pairs, in first-seen round order."""
        seen: list[str] = []
        for round_info in self.rounds:
            pair = f"{round_info.implementer[0]}/{round_info.implementer[1]}"
            if pair not in seen:
                seen.append(pair)
        return seen


# --------------------------------------------------------------------------- scrub


def scrub(text: str) -> str:
    """Replace paths, credential URLs, tokens, key material and attribution markers."""
    text = PRIVATE_KEY_RE.sub(REDACTED, text)
    text = URL_CREDENTIALS_RE.sub(r"\1" + REDACTED + "@", text)
    for pattern in TOKEN_RES:
        text = pattern.sub(REDACTED, text)
    text = SECRET_ASSIGNMENT_RE.sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)
    for pattern in PATH_RES:
        text = pattern.sub(REDACTED, text)
    for pattern in ATTRIBUTION_RES:
        text = pattern.sub(REDACTED, text)
    return text


def clean_field(text: str, what: str) -> str:
    """Scrub and fail closed on unsafe residual input (control characters, key markers)."""
    text = scrub(text)
    for char in text:
        if (ord(char) < 0x20 and char != "\n") or ord(char) == 0x7F:
            raise RecordError(f"{what}: control character in exported text; refusing")
    if "-----BEGIN" in text:
        raise RecordError(f"{what}: unredacted private-key marker; refusing")
    return text


def one_line(text: str, limit: int, what: str) -> str:
    """Scrub, collapse to one line and truncate visibly at `limit` characters."""
    text = clean_field(" ".join(text.split()), what)
    if len(text) > limit:
        text = text[: limit - len(TRUNCATED)] + TRUNCATED
    return text


# ----------------------------------------------------------------------------- io


def read_text(path: Path, what: str) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise RecordError(f"missing required artifact: {what}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise RecordError(
            f"{what}: cannot read as UTF-8: {type(exc).__name__}"
        ) from exc


def split_sections(text: str) -> list[tuple[str, list[str]]]:
    """[(heading, body lines)] in file order; fenced code blocks are skipped."""
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    fence: int | None = None
    for line in text.splitlines():
        fence_match = FENCE_RE.match(line)
        if fence is None:
            if fence_match:
                fence = len(fence_match.group(1))
                continue
            heading = HEADING_RE.match(line)
            if heading:
                current = (heading.group(1), [])
                sections.append(current)
                continue
            if current is not None:
                current[1].append(line)
        elif fence_match and len(fence_match.group(1)) >= fence:
            fence = None
    return sections


def require_section(text: str, name: str, what: str) -> list[str]:
    found = [body for heading, body in split_sections(text) if heading == name]
    if len(found) != 1:
        raise RecordError(
            f"{what}: the '## {name}' section must occur exactly once "
            f"(found {len(found)})"
        )
    return found[0]


# ------------------------------------------------------------------------ parsing


def parse_status(text: str, dir_name: str) -> tuple[str, int, str]:
    ids = [m.group(1) for m in map(STATUS_ID_RE.match, text.splitlines()) if m]
    if len(ids) != 1:
        raise RecordError(
            f"status.md: the '# Status: <id>' heading must occur exactly once "
            f"(found {len(ids)})"
        )
    task_id = ids[0]
    if not TASK_ID_RE.match(task_id):
        raise RecordError(
            "status.md: invalid task id (expected [a-z0-9][a-z0-9-]*, at most 64 chars)"
        )
    if task_id != dir_name:
        raise RecordError(
            f"status.md: task id does not match the task directory name {dir_name!r}"
        )
    rounds = [m.group(1) for m in map(ROUND_LINE_RE.match, text.splitlines()) if m]
    if len(rounds) != 1:
        raise RecordError("status.md: the '- round:' line must occur exactly once")
    round_n = int(rounds[0])
    if round_n < 1:
        raise RecordError(f"status.md: round is {round_n}; a record needs round >= 1")
    approved = [
        m.group(1).strip() for m in map(APPROVED_LINE_RE.match, text.splitlines()) if m
    ]
    if len(approved) != 1:
        raise RecordError(
            "status.md: the '- plan-approved:' line must occur exactly once"
        )
    if not approved[0].startswith("yes"):
        raise RecordError(
            "status.md: plan-approved is not yes; no record before approval"
        )
    approval = "auto" if "--auto" in approved[0] else "user"
    return task_id, round_n, approval


def parse_plan_acs(text: str) -> list[tuple[str, str]]:
    body = require_section(text, "Acceptance criteria", "plan.md")
    acs: dict[str, str] = {}
    order: list[str] = []
    current: str | None = None
    for line in body:
        stripped = line.strip()
        match = AC_LINE_RE.match(stripped)
        if match:
            current = f"AC{match.group(1)}"
            if current in acs:
                raise RecordError(f"plan.md: duplicate criterion id {current}")
            acs[current] = match.group(2).strip()
            order.append(current)
        elif stripped and current is not None:
            acs[current] = f"{acs[current]} {stripped}"
        elif not stripped:
            current = None
    if not acs:
        raise RecordError("plan.md: no 'AC<n>:' lines under '## Acceptance criteria'")
    result = []
    for acid in sorted(acs, key=lambda a: int(a[2:])):
        head, sep, _tail = acs[acid].partition(" Verify:")
        result.append((acid, head.strip() if sep else acs[acid]))
    return result


def parse_review(text: str, n: int, plan_acs: set[str]) -> tuple[dict[str, str], str]:
    what = f"review-{n}.md"
    body = require_section(text, "Acceptance criteria", what)
    results: dict[str, str] = {}
    for line in body:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        match = AC_ROW_RE.match(cells[0])
        if not match:
            continue
        acid = f"AC{match.group(1)}"
        if acid in results:
            raise RecordError(f"{what}: duplicate AC row {acid}")
        if cells[1] not in AC_RESULTS:
            raise RecordError(
                f"{what}: AC row {acid} has a result that is not PASS or FAIL"
            )
        results[acid] = cells[1]
    missing = sorted(plan_acs - set(results))
    extra = sorted(set(results) - plan_acs)
    if missing or extra:
        raise RecordError(
            f"{what}: AC rows do not match plan.md "
            f"(missing: {', '.join(missing) or 'none'}; "
            f"unexpected: {', '.join(extra) or 'none'})"
        )
    verdicts = [m.group(1) for m in map(VERDICT_RE.match, text.splitlines()) if m]
    if len(verdicts) != 1:
        raise RecordError(
            f"{what}: the '## Verdict:' heading must occur exactly once "
            f"(found {len(verdicts)})"
        )
    if verdicts[0] not in VERDICTS:
        raise RecordError(
            f"{what}: unknown verdict (expected APPROVED or CHANGES_REQUESTED)"
        )
    return results, verdicts[0]


def implementer_pair(task_dir: Path, n: int) -> tuple[str, str]:
    path = task_dir / f"impl-{n}.json"
    if not path.is_file():
        return UNKNOWN, UNKNOWN
    what = f"impl-{n}.json"
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RecordError(
            f"{what}: cannot parse as JSON: {type(exc).__name__}"
        ) from exc
    if not isinstance(data, dict):
        raise RecordError(f"{what}: the file must be a JSON object")
    profile = data.get("profile")
    if not (isinstance(profile, str) and profile.strip()):
        chain = data.get("fallback_chain")
        if (
            isinstance(chain, list)
            and chain
            and isinstance(chain[-1], str)
            and chain[-1].strip()
        ):
            profile = chain[-1]
        else:
            profile = UNKNOWN
    model = data.get("model")
    if not (isinstance(model, str) and model.strip()):
        model = UNKNOWN
    profile = clean_field(profile.strip(), f"{what} profile")
    model = clean_field(model.strip(), f"{what} model")
    for name, value in (("profile", profile), ("model", model)):
        if "\n" in value or "\r" in value:
            raise RecordError(f"{what}: {name} is not single-line; refusing")
    return profile, model


def build_record(task_dir: Path) -> Record:
    status = read_text(task_dir / "status.md", "status.md")
    plan = read_text(task_dir / "plan.md", "plan.md")
    summary = read_text(task_dir / "summary.md", "summary.md")
    task_id, round_n, approval = parse_status(status, task_dir.name)
    acs = parse_plan_acs(plan)
    plan_ac_ids = {acid for acid, _text in acs}
    rounds: list[RoundInfo] = []
    for n in range(1, round_n + 1):
        review_path = task_dir / f"review-{n}.md"
        if not review_path.is_file():
            raise RecordError(
                f"missing required artifact: review-{n}.md (status.md round is {round_n})"
            )
        results, verdict = parse_review(
            read_text(review_path, f"review-{n}.md"), n, plan_ac_ids
        )
        rounds.append(RoundInfo(n, verdict, results, implementer_pair(task_dir, n)))
    if (task_dir / f"review-{round_n + 1}.md").exists():
        raise RecordError(
            f"inconsistent rounds: review-{round_n + 1}.md exists but status.md round "
            f"is {round_n}"
        )
    final = rounds[-1].verdict
    if final != "APPROVED":
        raise RecordError(
            f"the final review-{round_n}.md verdict is {final}, not APPROVED; no record"
        )
    goal = one_line(
        " ".join(require_section(summary, "Goal", "summary.md")),
        MAX_GOAL_CHARS,
        "summary.md '## Goal'",
    )
    scope = one_line(
        " ".join(require_section(summary, "Out of scope", "summary.md")),
        MAX_SCOPE_CHARS,
        "summary.md '## Out of scope'",
    )
    if not scope:
        scope = "none recorded"
    clean_acs = [
        (acid, one_line(text, MAX_AC_CHARS, f"plan.md {acid}")) for acid, text in acs
    ]
    return Record(
        task_id=task_id,
        goal=goal,
        approval=approval,
        omitted_scope=scope,
        acs=clean_acs,
        rounds=rounds,
    )


# ----------------------------------------------------------------------- rendering


def render_record(record: Record) -> str:
    lines = [
        f"# Task record: {record.task_id}",
        "",
        f"- schema: {SCHEMA}",
        f"- task: {record.task_id}",
        f"- approval: {record.approval}",
        f"- rounds: {len(record.rounds)}",
        f"- final-verdict: {record.final_verdict}",
        f"- risk: {RISK_TEXT}",
        "",
        "## Goal",
        "",
        record.goal,
        "",
        "## Acceptance criteria",
        "",
    ]
    final_results = record.rounds[-1].results
    for acid, text in record.acs:
        lines.append(f"- {acid}: {text} — {final_results[acid]}")
    lines += ["", "## Rounds", ""]
    for round_info in record.rounds:
        profile, model = round_info.implementer
        lines.append(
            f"- round {round_info.number}: {round_info.verdict} "
            f"— implementer {profile}/{model}"
        )
    lines += ["", "## Out of scope", "", record.omitted_scope, ""]
    text = "\n".join(lines)
    if len(text.encode("utf-8")) > MAX_RECORD_BYTES:
        raise RecordError(
            f"the rendered record exceeds {MAX_RECORD_BYTES} bytes; refusing"
        )
    return text


def validate_trailer_value(key: str, value: str) -> None:
    if any((ord(char) < 0x20 and char != "\t") or ord(char) == 0x7F for char in value):
        raise RecordError(f"trailer {key}: control character in value; refusing")
    if len(value) > MAX_TRAILER_VALUE_CHARS:
        raise RecordError(f"trailer {key}: value longer than {MAX_TRAILER_VALUE_CHARS}")
    if key == "Task-Id" and not TASK_ID_RE.match(value):
        raise RecordError("trailer Task-Id: value is not a valid task id; refusing")
    if key == "Task-Rounds" and not value.isdigit():
        raise RecordError("trailer Task-Rounds: value is not a number; refusing")
    if key == "Task-Plan-Approved" and value not in APPROVALS:
        raise RecordError("trailer Task-Plan-Approved: value must be user or auto")
    if key == "Task-Implementer":
        parts = value.split("/")
        if len(parts) != 2 or not all(
            IMPLEMENTER_PART_RE.fullmatch(part) for part in parts
        ):
            raise RecordError(
                "trailer Task-Implementer: value is not <profile>/<model>; refusing"
            )


def render_trailers(record: Record) -> list[str]:
    """The sanitized trailer block; `Task-Risk` is absent until a risk schema merges."""
    entries = [
        ("Task-Id", record.task_id),
        ("Task-Rounds", str(len(record.rounds))),
    ]
    entries += [("Task-Implementer", pair) for pair in record.implementer_pairs()]
    entries.append(("Task-Plan-Approved", record.approval))
    lines = []
    for key, value in entries:
        validate_trailer_value(key, value)
        lines.append(f"{key}: {value}")
    return lines


# --------------------------------------------------------------------- publishing


def load_records_config(path: Path) -> bool:
    """Strictly parse the records config; return whether records are enabled."""
    if not path.exists():
        return False
    if not path.is_file():
        raise RecordError(f"{path}: not a regular file")
    try:
        data = tomllib.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RecordError(
            f"{path}: cannot parse as strict TOML ({type(exc).__name__})"
        ) from exc
    if not isinstance(data, dict):
        raise RecordError(f"{path}: the file must be a TOML table")
    unknown = sorted(set(data) - {"records"})
    if unknown:
        raise RecordError(
            f"{path}: unknown top-level key(s): {', '.join(unknown)} "
            "(only [records] is allowed)"
        )
    records = data["records"]
    if not isinstance(records, dict):
        raise RecordError(f"{path}: [records] must be a table")
    unknown = sorted(set(records) - {"enabled"})
    if unknown:
        raise RecordError(
            f"{path}: unknown key(s) in [records]: {', '.join(unknown)} "
            "(only enabled is allowed)"
        )
    if "enabled" not in records:
        raise RecordError(f"{path}: [records] is missing the enabled key")
    enabled = records["enabled"]
    if not isinstance(enabled, bool):
        raise RecordError(f"{path}: [records] enabled must be a boolean")
    return enabled


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def safe_destination(out_repo: Path, task_id: str) -> Path:
    """The record path inside <out_repo>/docs/tasks, refusing traversal and escapes."""
    if not TASK_ID_RE.match(task_id):
        raise RecordError("invalid task id (expected [a-z0-9][a-z0-9-]*)")
    root = out_repo.resolve()
    current = root
    for part in RECORDS_RELPATH.parts:
        current = current / part
        if is_link(current):
            raise RecordError(
                f"unsafe destination: {RECORDS_RELPATH.as_posix()} contains a symlink "
                f"({part})"
            )
        if current.exists() and not current.is_dir():
            raise RecordError(
                f"unsafe destination: {current.name} exists and is not a directory"
            )
    dest = current / f"{task_id}.md"
    if is_link(dest):
        raise RecordError("unsafe destination: the record path is a symlink")
    if dest.exists() and not dest.is_file():
        raise RecordError(
            "unsafe destination: the record path exists and is not a regular file"
        )
    resolved = dest.resolve()
    if resolved.parent != current.resolve():
        raise RecordError("unsafe destination: the record resolves outside docs/tasks")
    try:
        resolved.relative_to(root)
    except ValueError:
        raise RecordError(
            "unsafe destination: the record resolves outside the repository"
        ) from None
    return dest


# -------------------------------------------------------------------------- cli


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the opt-in finish-time task record and its commit trailers."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("render", "write docs/tasks/<id>.md into the worktree"),
        ("trailers", "print the sanitized Task-* trailer block"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--repo", required=True, help="the repository root")
        command.add_argument(
            "--task-dir", required=True, help="the abs <repo>/.work/<id> directory"
        )
        command.add_argument(
            "--worktree",
            default="none",
            help="the task's worktree (the record is written there), or 'none' for "
            "the repository root",
        )
        command.add_argument(
            "--config",
            default=None,
            help="records config path (default: <worktree|repo>/.claude/"
            "task-flow.records.toml)",
        )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        repo = Path(args.repo)
        if not repo.is_dir():
            raise RecordError(f"not a directory: {repo}")
        repo = repo.resolve()
        task_dir = Path(args.task_dir)
        if not task_dir.is_dir():
            raise RecordError(f"not a directory: {task_dir}")
        task_dir = task_dir.resolve()
        if task_dir.parent.name != ".work" or task_dir.parent.parent != repo:
            raise RecordError(
                f"task-dir is not <repo>/.work/<id> of {repo}: {task_dir}"
            )
        out_repo = repo if args.worktree in (None, "none") else Path(args.worktree)
        if not out_repo.is_dir():
            raise RecordError(f"not a directory: {out_repo}")
        out_repo = out_repo.resolve()
        config = Path(args.config) if args.config else out_repo / CONFIG_RELPATH
        if not load_records_config(config):
            if args.command == "trailers":
                print(
                    f"task_record: disabled: {config} does not enable records",
                    file=sys.stderr,
                )
            else:
                print(f"disabled: {config} does not enable records; nothing to do")
            return 0
        record = build_record(task_dir)
        trailers = render_trailers(record)
        if args.command == "trailers":
            for line in trailers:
                print(line)
            return 0
        text = render_record(record)
        dest = safe_destination(out_repo, record.task_id)
        data = text.encode("utf-8")
        rel = f"{RECORDS_RELPATH.as_posix()}/{record.task_id}.md"
        if dest.exists():
            if dest.read_bytes() == data:
                print(f"unchanged: {rel}")
                return 0
            raise RecordError(
                f"conflicting existing record: {rel}; refusing to overwrite"
            )
        (out_repo / RECORDS_RELPATH).mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"created: {rel}")
        return 0
    except RecordError as exc:
        for line in str(exc).splitlines():
            print(f"task_record: {line}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
