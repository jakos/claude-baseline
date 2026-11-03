# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Validate, render and update a repository's AI-native SDLC status (opt-in).

Usage (from the repository root, or pass `--repo <path>`):

    uv run --script plugins/task-flow/scripts/sdlc_status.py check
    uv run --script plugins/task-flow/scripts/sdlc_status.py render
    uv run --script plugins/task-flow/scripts/sdlc_status.py done <stage>/<criterion> ...
    uv run --script plugins/task-flow/scripts/sdlc_status.py finish --task-file <path>

The single source is `docs/task-flow/sdlc-status.toml` in the selected repository
(default: the current directory). Stage state is derived, never stored: all criteria
done = achieved, some done = partial, none done = missing.

- `check` never writes. Exit 0 when the source is valid and both generated files —
  the `sdlc:status` block of `README.md` and the whole `docs/task-flow/ai-native-sdlc.md`
  page — are current; exit 1 with the reasons on stderr otherwise.
- `render` validates everything first, then rewrites the block between exactly one
  ordered `<!-- sdlc:status:start/end -->` pair in `README.md` (missing, duplicate or
  reversed markers are refused, never replaced silently) and the whole page. A refusal
  exits 2 and changes no file. Repeated runs change no bytes (UTF-8, LF).
- `done` marks criteria done in the source; every marked criterion needs an existing
  evidence path. Unknown or malformed ids and criteria without evidence are refused
  before any write, so one bad id in a batch changes nothing.
- `finish --task-file <path>` reads the optional bullet
  `- SDLC criteria closed by this task: <stage>/<criterion>[, ...]` from the task file
  (which may live outside the repository, e.g. at the main repository root while the
  changes belong in the worktree), validates the whole batch, marks those criteria
  done, re-renders and re-checks. A missing source or an absent/empty declaration is a
  byte-for-byte no-op (exit 0, no file created or changed); an invalid declaration
  exits 2 with no file changed. Repeated finish is idempotent.

Stdlib only, started by `uv run --script`, so it runs on macOS, Linux and Windows.
"""

import argparse
import itertools
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

STAGES = (
    "plan",
    "design",
    "build",
    "test",
    "review",
    "release",
    "deploy",
    "maintain",
    "measure",
)
STAGE_TITLES = {stage: stage.capitalize() for stage in STAGES}
STATES = ("done", "open")
DERIVED_STATES = ("achieved", "partial", "missing")
SOURCE_RELPATH = Path("docs") / "task-flow" / "sdlc-status.toml"
README_RELPATH = Path("README.md")
PAGE_RELPATH = Path("docs") / "task-flow" / "ai-native-sdlc.md"
README_START = "<!-- sdlc:status:start -->"
README_END = "<!-- sdlc:status:end -->"
DIAGRAM_NAME = "sdlc-status"
MAX_BLOCK_LINES = 70
DECLARATION_RE = re.compile(r"^-\s+SDLC criteria closed by this task:\s*(.*)$")
CRITERION_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REF_RE = re.compile(r"^([a-z][a-z0-9-]*)/([a-z][a-z0-9-]*)$")
DRIVE_RE = re.compile(r"^[A-Za-z]:")

# Explicit fill/text/stroke classes for the three derived states; the light fills with
# dark text stay readable in GitHub's light and dark themes, and every node also
# carries its state as text.
STAGE_COLORS = {
    "achieved": "fill:#d4f8d4,stroke:#1a7f37,color:#0b3d0b",
    "partial": "fill:#fff3cd,stroke:#9a6700,color:#5c4400",
    "missing": "fill:#ffd7d5,stroke:#cf222e,color:#6e1119",
}

HUMAN_STAGE = {
    "plan": ("approves the plan — the approval gate cannot be skipped", "L1"),
    "design": ("reviews the design captured in plan.md before anything is built", "L1"),
    "build": ("reviews the diff and the check results of every round", "L2"),
    "test": ("reads checks-n.md; CI re-runs the gates on every pull request", "L2"),
    "review": ("reads the independent reviewer's verdict and decides", "L1"),
    "release": (
        "approves every release; push, PR and tag only after asking",
        "L3 (aspirational)",
    ),
    "deploy": ("decides and runs every deployment today", "L0"),
    "maintain": ("triages the signals that become tasks", "L2"),
    "measure": ("reads this status page and the delivery metrics", "L2"),
}

SOURCE_HEADER = """\
# AI-native SDLC status — the single source of the generated status view.
#
# `plugins/task-flow/scripts/sdlc_status.py` reads this file from the repository root
# (override with `--repo`): `check` (a gate) validates it and both generated files,
# `render` rewrites the `sdlc:status` block of README.md and the whole
# docs/task-flow/ai-native-sdlc.md page, `done` flips criteria to done, and `finish`
# does that for the criteria a task declares before its commit.
#
# Format: one `[stage.<id>]` table per stage — exactly the nine canonical stages in
# order (plan, design, build, test, review, release, deploy, maintain, measure) — each
# with a `title` and a nonempty `criteria` list of `[[stage.<id>.criteria]]` tables
# with exactly these keys:
#   id       criterion id, `[a-z][a-z0-9-]*`, unique within its stage
#   title    one-line description
#   state    `done` or `open`
#   task     id of the task that closes the criterion; empty means historical (when
#            done) or unplanned (when open) — no task id is invented
#   evidence repository-relative path (forward slashes, no `..`) that must resolve
#            inside this repository and exist; required nonempty when `state = "done"`,
#            empty means none is recorded
#
# Stage state is derived, never stored: all criteria done = achieved, some = partial,
# none = missing.

"""


class StatusError(Exception):
    """A refusal: the message goes to stderr, no file is changed."""


class MarkerError(Exception):
    """A marker is missing, duplicated or out of order in a file."""


@dataclass
class Criterion:
    id: str
    title: str
    state: str
    task: str
    evidence: str


@dataclass
class Stage:
    id: str
    title: str
    criteria: list[Criterion] = field(default_factory=list)


# ---------------------------------------------------------------------------- io


def read_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise StatusError(f"{path}: cannot read as UTF-8: {exc}") from exc


def write_if_changed(path: Path, text: str) -> bool:
    """Write UTF-8/LF bytes; return True when the file changed."""
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def toml_string(value: str) -> str:
    """A TOML basic string; json.dumps output is valid TOML for these values."""
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------- source


def parse_source(data: object) -> tuple[list[Stage], list[str]]:
    """Structure validation; returns the stages and every problem found."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return [], ["the file must be a TOML table"]
    unknown = sorted(set(data) - {"stage"})
    if unknown:
        problems.append(
            f"unknown top-level key(s): {', '.join(unknown)} (only [stage.*] is allowed)"
        )
    stage_tbl = data.get("stage")
    if not isinstance(stage_tbl, dict):
        problems.append("missing [stage] table")
        return [], problems
    ids = list(stage_tbl)
    if ids != list(STAGES):
        found = ", ".join(ids) if ids else "none"
        problems.append(
            "stages must be exactly "
            + ", ".join(STAGES)
            + f" in that order; found: {found}"
        )
    stages: list[Stage] = []
    for sid, entry in stage_tbl.items():
        if sid not in STAGES:
            problems.append(
                f"unknown stage id {sid!r} (known stages: {', '.join(STAGES)})"
            )
            continue
        if not isinstance(entry, dict):
            problems.append(f"stage {sid!r}: must be a table")
            continue
        bad = sorted(set(entry) - {"title", "criteria"})
        if bad:
            problems.append(
                f"stage {sid!r}: unknown key(s) {', '.join(bad)} (allowed: title, criteria)"
            )
        title = entry.get("title", STAGE_TITLES[sid])
        if not isinstance(title, str) or not title.strip():
            problems.append(f"stage {sid!r}: title must be a nonempty string")
            title = STAGE_TITLES[sid]
        criteria = entry.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            problems.append(
                f"stage {sid!r}: criteria must be a nonempty list of "
                f"[[stage.{sid}.criteria]] tables"
            )
            continue
        crits: list[Criterion] = []
        seen: set[str] = set()
        for i, item in enumerate(criteria):
            where = f"stage {sid!r} criterion #{i + 1}"
            if not isinstance(item, dict):
                problems.append(f"{where}: must be a table")
                continue
            keys = ("id", "title", "state", "task", "evidence")
            bad = sorted(set(item) - set(keys))
            if bad:
                problems.append(
                    f"{where}: unknown key(s) {', '.join(bad)} "
                    f"(allowed: {', '.join(keys)})"
                )
            missing = [k for k in keys if k not in item]
            if missing:
                problems.append(f"{where}: missing key(s) {', '.join(missing)}")
                continue
            values = {k: item[k] for k in keys}
            if not all(isinstance(v, str) for v in values.values()):
                problems.append(f"{where}: every value must be a string")
                continue
            cid = values["id"]
            if not CRITERION_ID_RE.match(cid):
                problems.append(f"{where}: id {cid!r} must match [a-z][a-z0-9-]*")
            if cid in seen:
                problems.append(f"stage {sid!r}: duplicate criterion id {cid!r}")
            seen.add(cid)
            if not values["title"].strip():
                problems.append(f"{where}: title must be nonempty")
            if values["state"] not in STATES:
                problems.append(
                    f"{where}: state must be 'done' or 'open', got {values['state']!r}"
                )
            task = values["task"]
            if task and not TASK_ID_RE.match(task):
                problems.append(
                    f"{where}: task {task!r} must match [a-z0-9][a-z0-9-]* or be empty"
                )
            crits.append(
                Criterion(
                    cid, values["title"], values["state"], task, values["evidence"]
                )
            )
        stages.append(Stage(sid, title, crits))
    if problems:
        return [], problems
    return stages, []


def evidence_problem(evidence: str, repo: Path) -> str | None:
    """Why `evidence` is not a safe repository-relative path, or None."""
    if "\\" in evidence:
        return "must use / as the separator, not backslashes"
    if DRIVE_RE.match(evidence):
        return "must not be a Windows drive path"
    if evidence.startswith("/"):
        return "must not be an absolute path"
    parts = evidence.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "must be a plain relative path without empty, . or .. parts"
    root = repo.resolve()
    target = (repo / evidence).resolve()
    if target == root or root in target.parents:
        return None
    return "must resolve inside the repository root"


def validate_stages(stages: list[Stage], repo: Path) -> None:
    """Evidence validation; raises StatusError with every problem found."""
    problems: list[str] = []
    for stage in stages:
        for crit in stage.criteria:
            where = f"stage {stage.id!r} criterion {crit.id!r}"
            if crit.state == "done" and not crit.evidence:
                problems.append(
                    f"{where}: state is done, so evidence must be a nonempty, "
                    "existing repository-relative path"
                )
            if crit.evidence:
                problem = evidence_problem(crit.evidence, repo)
                if problem:
                    problems.append(f"{where}: evidence {crit.evidence!r} {problem}")
                elif not (repo / crit.evidence).exists():
                    problems.append(
                        f"{where}: evidence {crit.evidence!r} does not exist under {repo}"
                    )
    if problems:
        raise StatusError("\n".join(problems))


def load_source(path: Path, repo: Path) -> list[Stage]:
    """Read, parse and validate the source file; StatusError on any problem."""
    if not path.is_file():
        raise StatusError(f"missing source: {path}")
    try:
        data = tomllib.loads(read_text(path))
    except tomllib.TOMLDecodeError as exc:
        raise StatusError(f"{path}: invalid TOML: {exc}") from exc
    stages, problems = parse_source(data)
    if problems:
        raise StatusError(f"{path}:\n" + "\n".join(f"  - {p}" for p in problems))
    validate_stages(stages, repo)
    return stages


def dump_source(stages: list[Stage]) -> str:
    """The deterministic TOML text of `stages` (round-trips through load_source)."""
    parts = [SOURCE_HEADER]
    for stage in stages:
        parts.append(f"[stage.{stage.id}]\n")
        parts.append(f"title = {toml_string(stage.title)}\n\n")
        for crit in stage.criteria:
            parts.append(f"[[stage.{stage.id}.criteria]]\n")
            parts.append(f"id = {toml_string(crit.id)}\n")
            parts.append(f"title = {toml_string(crit.title)}\n")
            parts.append(f"state = {toml_string(crit.state)}\n")
            parts.append(f"task = {toml_string(crit.task)}\n")
            parts.append(f"evidence = {toml_string(crit.evidence)}\n\n")
    return "".join(parts)


# ---------------------------------------------------------------------------- deriving


def stage_state(stage: Stage) -> str:
    """achieved (all done), partial (some) or missing (none)."""
    done = sum(1 for c in stage.criteria if c.state == "done")
    if done == len(stage.criteria):
        return "achieved"
    if done:
        return "partial"
    return "missing"


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|")


def diagram_body(stages: list[Stage]) -> str:
    """The nine-node Mermaid flowchart, shared by the README block and the page."""
    lines = ["flowchart TD"]
    for stage in stages:
        label = f"{stage.title} ({stage_state(stage)})"
        lines.append(f"  {stage.id}[{toml_string(label)}]")
    for left, right in itertools.pairwise(stages):
        lines.append(f"  {left.id} --> {right.id}")
    for state in DERIVED_STATES:
        lines.append(f"  classDef {state} {STAGE_COLORS[state]}")
    for state in DERIVED_STATES:
        ids = [s.id for s in stages if stage_state(s) == state]
        if ids:
            lines.append(f"  class {','.join(ids)} {state}")
    return "\n".join(lines)


def status_table(stages: list[Stage]) -> list[str]:
    """The nine-row stage table (shared by the README block and the page)."""
    lines = [
        "| Stage | State | Achieved criteria | Missing criteria | Next task |",
        "| --- | --- | --- | --- | --- |",
    ]
    for stage in stages:
        done_titles = [c.title for c in stage.criteria if c.state == "done"]
        open_crits = [c for c in stage.criteria if c.state == "open"]
        next_tasks: list[str] = []
        for crit in open_crits:
            label = crit.task or "unplanned"
            if label not in next_tasks:
                next_tasks.append(label)
        achieved = "; ".join(_escape_cell(t) for t in done_titles) or "—"
        missing = "; ".join(_escape_cell(c.title) for c in open_crits) or "—"
        lines.append(
            f"| {stage.title} | {stage_state(stage)} | {achieved} | {missing} "
            f"| {', '.join(next_tasks) or '—'} |"
        )
    return lines


def criteria_table(stages: list[Stage]) -> list[str]:
    lines = [
        "| Stage | Criterion | Title | State | Evidence | Closing task |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for stage in stages:
        for crit in stage.criteria:
            evidence = f"`{crit.evidence}`" if crit.evidence else "—"
            task = crit.task or ("—" if crit.state == "done" else "unplanned")
            lines.append(
                f"| {stage.id} | {crit.id} | {_escape_cell(crit.title)} "
                f"| {crit.state} | {evidence} | {task} |"
            )
    return lines


# ---------------------------------------------------------------------------- rendering


PAGE_INTRO = """\
# AI-native SDLC status

task-flow's purpose is an AI-native SDLC: agents plan, implement, check and review
software, and a human stays behind every gate — the plan approval, the round verdict
and the release approval. Autonomy here is assisted, never unattended. Locally, ADLC
(agent-driven development life cycle) means the same development life cycle run by
agents under those human gates; it is not a claim that no human is involved.

This page and the `sdlc:status` block of [the front page](../../README.md) are
generated by `plugins/task-flow/scripts/sdlc_status.py` from
[`docs/task-flow/sdlc-status.toml`](sdlc-status.toml) — the committed source of every
stage's criteria, states, evidence and closing tasks. Do not edit them by hand:
`uv run --script plugins/task-flow/scripts/sdlc_status.py render` regenerates both,
`uv run --script plugins/task-flow/scripts/sdlc_status.py check` (one of this
repository's gates, see [Deterministic gates](gates.md)) fails when they are stale,
and `/task-flow:task-finish` marks the criteria a task declares done and re-renders
before its commit (see
[orchestration](../../plugins/task-flow/skills/workflow/orchestration.md)).
"""

AUTONOMY_SECTION = """\
## Autonomy levels (aspirational)

A direction, not a claim — no stage above L2 is reached today:

| Level | Meaning |
| --- | --- |
| L0 | the human does the work |
| L1 | the agent drafts, the human decides |
| L2 | the agent runs gated steps, the human owns the gates |
| L3 | the agent runs a whole stage, the human audits |
| L4 | unattended — explicitly not a goal |

Current limits: the achieved stages are achieved against the criteria below with a
human at every gate; Deploy is missing entirely and stays with a human (L0); no
unattended operation is implemented or claimed anywhere.
"""

SOURCES_SECTION = """\
## Sources

- [`docs/task-flow/sdlc-status.toml`](sdlc-status.toml) — the committed criteria
  source; this page and the front-page block are generated from it.
- `plugins/task-flow/scripts/sdlc_status.py` — validation, `check`, `render`, `done`
  and `finish`.
- [The front page](../../README.md) — the generated `sdlc:status` block.
- [Commands](commands.md) — `/task-flow:task-finish` runs the refresh before its
  commit.
"""


def readme_block_body(stages: list[Stage]) -> str:
    """The generated body between the sdlc:status markers of README.md."""
    lines = [
        (
            "Generated from [`docs/task-flow/sdlc-status.toml`](docs/task-flow/sdlc-status.toml)"
            " by `plugins/task-flow/scripts/sdlc_status.py render` — do not edit by hand;"
            " details: [AI-native SDLC status](docs/task-flow/ai-native-sdlc.md)."
        ),
        "",
        f"<!-- diagram:{DIAGRAM_NAME} -->",
        "```mermaid",
        diagram_body(stages),
        "```",
        f"<!-- /diagram:{DIAGRAM_NAME} -->",
        "",
        *status_table(stages),
    ]
    body = "\n".join(lines) + "\n"
    count = len(body.splitlines())
    if count > MAX_BLOCK_LINES:
        raise StatusError(
            f"the generated sdlc:status block would be {count} lines, "
            f"over the {MAX_BLOCK_LINES}-line limit"
        )
    return body


def page_text(stages: list[Stage]) -> str:
    """The complete generated docs/task-flow/ai-native-sdlc.md page."""
    parts = [PAGE_INTRO, "\n## Current status\n\n"]
    parts.append(
        "Generated from [`docs/task-flow/sdlc-status.toml`](sdlc-status.toml); "
        "do not edit by hand.\n\n"
    )
    parts.append(f"<!-- diagram:{DIAGRAM_NAME} -->\n")
    parts.append("```mermaid\n")
    parts.append(diagram_body(stages) + "\n")
    parts.append("```\n")
    parts.append(f"<!-- /diagram:{DIAGRAM_NAME} -->\n\n")
    parts.append("\n".join(status_table(stages)) + "\n\n")
    parts.append(
        "Stage states are derived from the criteria, never stored: all criteria done = "
        "`achieved`, some = `partial`, none = `missing`. Every node also carries its "
        "state as text, so the colours are never the only signal.\n\n"
    )
    parts.append("## What stays with the human, per stage\n\n")
    parts.append("| Stage | What stays with the human | Aspirational autonomy |\n")
    parts.append("| --- | --- | --- |\n")
    for stage in stages:
        human, autonomy = HUMAN_STAGE[stage.id]
        parts.append(f"| {stage.title} | {human} | {autonomy} |\n")
    parts.append("\n")
    parts.append(AUTONOMY_SECTION)
    parts.append("\n## Criteria, evidence and closing tasks\n\n")
    parts.append("\n".join(criteria_table(stages)) + "\n\n")
    parts.append(SOURCES_SECTION)
    return "".join(parts)


def replace_block(text: str, start: str, end: str, body: str) -> str:
    """Replace what is between the two marker lines; MarkerError otherwise.

    Each marker must occur exactly once, as a whole line, start before end. Everything
    up to and including the start-marker line and from the end-marker line on is kept
    byte-identical (line endings included); the body uses the start marker's line ending.
    """
    lines = text.splitlines(keepends=True)

    def eol(line: str) -> str:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith(("\n", "\r")):
            return line[-1]
        return ""

    def content(line: str) -> str:
        return line[: len(line) - len(eol(line))] if eol(line) else line

    start_idx = [i for i, line in enumerate(lines) if content(line) == start]
    end_idx = [i for i, line in enumerate(lines) if content(line) == end]
    if len(start_idx) != 1:
        raise MarkerError(
            f"marker {start!r} must occur exactly once on its own line "
            f"(found {len(start_idx)})"
        )
    if len(end_idx) != 1:
        raise MarkerError(
            f"marker {end!r} must occur exactly once on its own line "
            f"(found {len(end_idx)})"
        )
    first, last = start_idx[0], end_idx[0]
    if first >= last:
        raise MarkerError(f"marker {start!r} must come before {end!r}")
    body_text = body if body.endswith("\n") else body + "\n"
    body_text = body_text.replace("\r\n", "\n").replace("\r", "\n")
    if eol(lines[first]) == "\r\n":
        body_text = body_text.replace("\n", "\r\n")
    return "".join(lines[: first + 1]) + body_text + "".join(lines[last:])


def expected_readme(repo: Path, stages: list[Stage]) -> str:
    """The README text with the current generated block."""
    readme = repo / README_RELPATH
    if not readme.is_file():
        raise StatusError(f"{README_RELPATH} not found under {repo}")
    try:
        return replace_block(
            read_text(readme), README_START, README_END, readme_block_body(stages)
        )
    except MarkerError as exc:
        raise StatusError(
            f"{README_RELPATH}: {exc}; add these two marker lines on their own lines "
            f"where the block should go:\n{README_START}\n{README_END}"
        ) from exc


# ---------------------------------------------------------------------------- commands


def _index(stages: list[Stage]) -> dict[str, Criterion]:
    return {f"{s.id}/{c.id}": c for s in stages for c in s.criteria}


def _resolve_refs(stages: list[Stage], refs: list[str], origin: str) -> list[str]:
    """Validate the ref format and existence; return the deduplicated refs."""
    for ref in refs:
        if not REF_RE.match(ref):
            raise StatusError(
                f"{origin}: malformed criterion id {ref!r}: expected "
                "<stage>/<criterion> like plan/plan-gate (comma-separated for several)"
            )
    unique = list(dict.fromkeys(refs))
    index = _index(stages)
    unknown = [ref for ref in unique if ref not in index]
    if unknown:
        raise StatusError(
            f"{origin}: unknown criterion id(s): {', '.join(unknown)}; "
            f"known: {', '.join(sorted(index))}"
        )
    return unique


def _require_evidence(stages: list[Stage], refs: list[str], origin: str) -> None:
    index = _index(stages)
    for ref in refs:
        crit = index[ref]
        if crit.state != "done" and not crit.evidence:
            raise StatusError(
                f"{origin}: {ref} has no evidence; add an existing repository-relative "
                f"evidence path to {SOURCE_RELPATH} before marking it done"
            )


def cmd_check(repo: Path) -> int:
    source = repo / SOURCE_RELPATH
    if not source.is_file():
        print(
            f"sdlc_status: missing source {SOURCE_RELPATH} under {repo}; "
            "run render in a repository that has one",
            file=sys.stderr,
        )
        return 1
    try:
        stages = load_source(source, repo)
    except StatusError as exc:
        for line in str(exc).splitlines():
            print(f"sdlc_status: {line}", file=sys.stderr)
        return 1
    problems: list[str] = []
    readme = repo / README_RELPATH
    if not readme.is_file():
        problems.append(f"{README_RELPATH} is missing")
    else:
        text = read_text(readme)
        try:
            expected = replace_block(
                text, README_START, README_END, readme_block_body(stages)
            )
        except MarkerError as exc:
            problems.append(f"{README_RELPATH}: {exc}")
        else:
            if expected != text:
                problems.append(
                    f"{README_RELPATH}: the sdlc:status block is stale; run "
                    "`uv run --script plugins/task-flow/scripts/sdlc_status.py render`"
                )
    page = repo / PAGE_RELPATH
    if not page.is_file():
        problems.append(f"{PAGE_RELPATH} is missing; run render")
    elif read_text(page) != page_text(stages):
        problems.append(
            f"{PAGE_RELPATH} is stale; run "
            "`uv run --script plugins/task-flow/scripts/sdlc_status.py render`"
        )
    if problems:
        for problem in problems:
            print(f"sdlc_status: {problem}", file=sys.stderr)
        return 1
    total = sum(len(s.criteria) for s in stages)
    done = sum(1 for s in stages for c in s.criteria if c.state == "done")
    print(
        f"ok: {len(stages)} stages, {total} criteria ({done} done); "
        "the generated README block and page are current"
    )
    return 0


def cmd_render(repo: Path) -> int:
    stages = load_source(repo / SOURCE_RELPATH, repo)
    new_readme = expected_readme(repo, stages)
    new_page = page_text(stages)
    changed_readme = write_if_changed(repo / README_RELPATH, new_readme)
    changed_page = write_if_changed(repo / PAGE_RELPATH, new_page)
    print(
        f"rendered: {README_RELPATH} ({'changed' if changed_readme else 'unchanged'}), "
        f"{PAGE_RELPATH} ({'changed' if changed_page else 'unchanged'})"
    )
    return 0


def cmd_done(repo: Path, refs: list[str]) -> int:
    stages = load_source(repo / SOURCE_RELPATH, repo)
    unique = _resolve_refs(stages, refs, "done")
    _require_evidence(stages, unique, "done")
    index = _index(stages)
    for ref in unique:
        index[ref].state = "done"
    validate_stages(stages, repo)
    changed = write_if_changed(repo / SOURCE_RELPATH, dump_source(stages))
    for ref in unique:
        print(f"done: {ref}")
    print(f"{SOURCE_RELPATH} ({'changed' if changed else 'unchanged'})")
    return 0


def cmd_finish(repo: Path, task_file: Path) -> int:
    source = repo / SOURCE_RELPATH
    if not source.is_file():
        print(f"no-op: no {SOURCE_RELPATH} under {repo}; nothing to do")
        return 0
    if not task_file.is_file():
        raise StatusError(f"task file not found: {task_file}")
    declarations = [
        m
        for line in read_text(task_file).splitlines()
        if (m := DECLARATION_RE.match(line))
    ]
    if len(declarations) > 1:
        raise StatusError(
            f"{task_file}: more than one 'SDLC criteria closed by this task:' declaration"
        )
    if not declarations:
        print(
            f"no-op: {task_file} has no 'SDLC criteria closed by this task:' declaration"
        )
        return 0
    raw = declarations[0].group(1).strip()
    refs = [part.strip() for part in raw.split(",") if part.strip()]
    if not refs:
        print("no-op: the declaration lists no criteria")
        return 0
    stages = load_source(source, repo)
    unique = _resolve_refs(stages, refs, str(task_file))
    _require_evidence(stages, unique, str(task_file))
    index = _index(stages)
    for ref in unique:
        index[ref].state = "done"
    validate_stages(stages, repo)
    new_source = dump_source(stages)
    new_readme = expected_readme(repo, stages)
    new_page = page_text(stages)
    # every check passed; write everything
    write_if_changed(source, new_source)
    write_if_changed(repo / README_RELPATH, new_readme)
    write_if_changed(repo / PAGE_RELPATH, new_page)
    for ref in unique:
        print(f"done: {ref}")
    print(
        f"rendered: {SOURCE_RELPATH}, {README_RELPATH}, {PAGE_RELPATH}; "
        "check them with `sdlc_status.py check`"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate, render and update the AI-native SDLC status."
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="repository root that holds docs/task-flow/sdlc-status.toml "
        "(default: the current directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "check", help="validate the source and both generated files; never writes"
    )
    sub.add_parser(
        "render", help="rewrite the README sdlc:status block and the generated page"
    )
    done = sub.add_parser("done", help="mark criteria done in the source")
    done.add_argument("refs", nargs="+", metavar="stage/criterion")
    finish = sub.add_parser(
        "finish", help="mark the task's declared criteria done and re-render"
    )
    finish.add_argument(
        "--task-file",
        required=True,
        help="the task.md that may declare 'SDLC criteria closed by this task:'",
    )
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    repo = Path(args.repo)
    if not repo.is_dir():
        print(f"sdlc_status: not a directory: {repo}", file=sys.stderr)
        return 2
    repo = repo.resolve()
    try:
        if args.command == "check":
            return cmd_check(repo)
        if args.command == "render":
            return cmd_render(repo)
        if args.command == "done":
            return cmd_done(repo, args.refs)
        return cmd_finish(repo, Path(args.task_file))
    except StatusError as exc:
        for line in str(exc).splitlines():
            print(f"sdlc_status: {line}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
