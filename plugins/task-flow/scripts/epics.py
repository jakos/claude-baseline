# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Persistent epics: one id for a group of tasks, created before execution.

An epic lives in `.work/_epics/<epic-id>/epic.md` — outside the task namespace: an epic
never has a `status.md` and is never listed or picked as a task by `task_list.py`.
`.work/_epics/<epic-id>/` also holds `report.md` (completed epics only) and
`attempts/<task-id>/attempt-<k>/` (archived round evidence of retried tasks, which task
pruning can never delete because it only prunes `.work/<task-id>/`).

The strict `epic.md` contract (UTF-8, LF, atomic replacement; refusals leave the existing
bytes unchanged):

```markdown
# Epic: <epic-id>
- title: <one line>
- goal: <one line>
- base: <branch the epic's root tasks start from>
- state: pending | active | paused | completed
- delivery: per-task | epic
- active-run: none | <autopilot run-id>
## Tasks
| # | Task | Depends on |
| --- | --- | --- |
| 1 | first-task | - |
| 2 | second-task | first-task |
## History
- <ISO timestamp> <event> — <one line>
```

Exactly those six header fields, in that order; exactly the `## Tasks` and `## History`
sections, in that order; task rows numbered from 1; `Depends on` is `-` or the id of one
earlier row (at most one parent — joining branches needs a separate integration design).
Dependencies reference earlier rows only, so forward, dangling and cyclic dependencies
are all refused. `state` is derived: `completed` when every member task is `done`,
`active` while the linked run is running, `paused` when the linked run is paused or
stopped or a member failed, otherwise `pending`; adding a task to a completed epic
reopens it as `pending`.

Subcommands (exit 2 with `epics.py: <reason>` on stderr and nothing on stdout when
refusing; no refusal ever changes an existing file):

- `new <root> <epic-id> --title T --goal G --base B [--delivery per-task|epic]
  [--tasks <id>[,<id>...]]`: create the epic. With `--tasks`, the existing eligible
  tasks are grouped in the given order as a linear chain (row n depends on row n-1).
  Without `--tasks`, the epic starts empty (goal-only creation): the orchestrator stages
  the decomposition in an ordinary autopilot planning folder, shows it for approval, and
  attaches the created tasks with `add`. No creation path starts implementation.
- `add <root> <epic-id> --tasks <id>[,<id>...] [--depends-on <row-id>]`: append existing
  eligible tasks (default parent: the current last row, or `-` for an empty table).
  Refused while the epic is active; a task that is a member of another non-completed
  epic, already a row, or not eligible is refused.
- `remove <root> <epic-id> --tasks <id>[,<id>...]`: remove rows. Refused while the epic
  is active, when a task has started, or when a remaining row would depend on a removed
  one (dangling edge).
- `sync-run <root> <epic-id>`: regenerate the linked run's `run.md` `## Tasks` table
  from the epic graph (after a hand edit of `epic.md`). `add`/`remove` do the same
  automatically while a run is linked: existing rows keep their `Tokens used` /
  `External USD` cells and every header field (including `planning-tokens`), new rows
  start at `0`, one History line is appended, and the run state returns to `planning`
  so the estimate and the start gate run again on resume.
- `link <root> <epic-id> <run-id>` / `unlink <root> <epic-id>`: set `active-run` to a run
  whose `run.md` declares `source: epic` and `epic: <epic-id>` (or clear it). Linking sets
  state `active`.
- `validate <root> <epic-id>`: re-parse, check every member task still exists, the
  stored state matches the derived one, and print `ok: ...`.
- `epic-of <root> <task-id>`: print the epic id that reports the task (the open epic, or
  the most recently completed one), or `none`.
- `is-epic <root> <id>`: print `yes` when `<id>` names an epic, not a task.
- `list <root>`: print the epic ids, one per line.
- `status <root> <epic-id>`: deterministic status report (tasks with phase, rounds,
  verdict, risk, tokens/USD, blocker chains, next human actions, and an escaped,
  state-coloured Mermaid diagram).
- `report <root> <epic-id>`: write `report.md` — only when the epic is completed;
  partial completion is never reported as success.
- `retry <root> <epic-id> <task-id> --authorized`: archive the task's current round
  artifacts (`checks-*.md`, `review-*.md`, `impl-*.json`, `summary.md`) under
  `attempts/<task-id>/attempt-<k>/` before round filenames are reused, plus a
  `spend.md` snapshot of the task's spend at that moment, and record the attempt in
  History. With `--authorized` (the explicit user authorization of a round reset; never
  passed by `--auto`) the command also resets the task's `status.md` in place — phase
  back to `approved`, round 0, one History line — so `/task-flow:task-run <task-id>`
  starts a new attempt at round 1; the previous round count is recorded in the epic's
  History, and the 3-round cap and no-progress rule apply per authorized attempt.
  Without `--authorized` the command refuses and changes nothing. Re-running with
  nothing new is an idempotent no-op.

Spend accounting in `status`/`report` is cumulative and never double counts: the linked
run's `planning-tokens` plus its per-task `Tokens used` rows (the rows are cumulative
per task, so they already include every archived attempt), or — for a task with no row
in the linked run, or when no run is linked — the archived `attempts/<task-id>/attempt-*/spend.md`
snapshots. `unknown` amounts stay sticky and are reported as `at least <known sum>`; a
run without the `External USD` column counts as `0`, like `autopilot.py`.

Stdlib only, started by `uv run --script`, so it runs on macOS, Linux and Windows.
"""

import argparse
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import role_runtime

EPICS_DIRNAME = "_epics"
EPIC_FILENAME = "epic.md"
REPORT_FILENAME = "report.md"
ATTEMPTS_DIRNAME = "attempts"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
HEADER_KEYS = ("title", "goal", "base", "state", "delivery", "active-run")
STATES = ("pending", "active", "paused", "completed")
DELIVERIES = ("per-task", "epic")
TASKS_HEADER = ("#", "Task", "Depends on")
FIELD_RE = re.compile(r"^- ([a-z-]+): (.*)$")
HISTORY_RE = re.compile(r"^- (\S+) (.+?) — (.+)$")
MULTIPLE_PARENTS_HINT = (
    "joining branches needs a separate integration design; "
    "an epic task has at most one parent"
)
ELIGIBLE_PHASES = ("new", "planned", "approved")
MEMBER_PHASES = ELIGIBLE_PHASES + (
    "done",
)  # a done task may be a member; it never reruns
FAILED_PHASES = ("escalated", "blocked")
IN_PROGRESS_PHASES = (
    "baseline",
    "implementing",
    "checking",
    "reviewing",
    "review-approved",
)
ARCHIVE_GLOBS = ("checks-*.md", "review-*.md", "impl-*.json", "summary.md")
SPEND_FILENAME = "spend.md"
RESET_PHASE = "approved"  # the resumable phase a new authorized attempt starts from
RISK_TEXT = "unavailable (no merged risk schema)"


class EpicError(Exception):
    """A refusal: the message goes to stderr, no file is changed."""


@dataclass
class EpicTask:
    number: int
    task_id: str
    depends: str | None  # None for a root


@dataclass
class Epic:
    epic_id: str
    title: str
    goal: str
    base: str
    state: str
    delivery: str
    active_run: str  # "none" or a run id
    tasks: list[EpicTask] = field(default_factory=list)
    history: list[str] = field(default_factory=list)
    path: Path | None = None

    def row_of(self, task_id: str) -> EpicTask | None:
        return next((t for t in self.tasks if t.task_id == task_id), None)

    def task_ids(self) -> list[str]:
        return [t.task_id for t in self.tasks]


# ------------------------------------------------------------------ paths and io


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def work_dir(root: Path) -> Path:
    if not root.is_dir():
        raise EpicError(f"not a directory: {root}")
    return root / ".work"


def epic_dir(root: Path, epic_id: str) -> Path:
    """<root>/.work/_epics/<epic-id>, refusing traversal and symlink escapes."""
    if not ID_RE.match(epic_id):
        raise EpicError(f"invalid epic id {epic_id!r} (expected [a-z0-9][a-z0-9-]*)")
    base = work_dir(root).resolve()
    current = base
    for part in (EPICS_DIRNAME, epic_id):
        current = current / part
        if is_link(current):
            raise EpicError(f"unsafe path: {part} is a symlink")
        if current.exists() and part == EPICS_DIRNAME and not current.is_dir():
            raise EpicError(f"unsafe path: {EPICS_DIRNAME} is not a directory")
    resolved = current.resolve()
    try:
        resolved.relative_to(base / EPICS_DIRNAME)
    except ValueError:
        raise EpicError("unsafe path: the epic resolves outside .work/_epics") from None
    return current


def epic_path(root: Path, epic_id: str) -> Path:
    path = epic_dir(root, epic_id) / EPIC_FILENAME
    if is_link(path):
        raise EpicError("unsafe path: epic.md is a symlink")
    return path


def decode_strict(data: bytes, what: str) -> str:
    """Decode epic.md bytes: strict UTF-8 and LF-only (epic files are never CRLF)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EpicError(f"{what}: cannot read as UTF-8: {exc}") from exc
    if "\r" in text:
        raise EpicError(f"{what}: CR found; epic files are LF-only")
    return text


def decode_utf8(data: bytes, what: str) -> str:
    """Lenient UTF-8 decode for neighbouring artifacts (task status.md, run.md)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EpicError(f"{what}: cannot read as UTF-8: {exc}") from exc


def read_epic_text(path: Path) -> str:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise EpicError(f"missing epic file: {path}") from None
    except OSError as exc:
        raise EpicError(f"cannot read {path}: {exc}") from exc
    return decode_strict(data, str(path))


def atomic_write(path: Path, text: str) -> None:
    """UTF-8/LF atomic replacement: a temp file in the same directory, then os.replace."""
    data = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def now_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ------------------------------------------------------------------- parsing


def _table_cells(line: str) -> list[str] | None:
    if not line.startswith("|"):
        return None
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_epic(text: str, epic_id: str, root: Path | None = None) -> Epic:
    """Strictly parse epic.md text; with `root`, also check member tasks exist."""
    if not text.endswith("\n"):
        raise EpicError("epic.md must end with a newline")
    lines = text.split("\n")[:-1]
    if not lines or lines[0] != f"# Epic: {epic_id}":
        raise EpicError(f"the first line must be '# Epic: {epic_id}'")
    if not ID_RE.match(epic_id):
        raise EpicError(f"invalid epic id {epic_id!r} (expected [a-z0-9][a-z0-9-]*)")

    fields: dict[str, str] = {}
    index = 1
    while index < len(lines) and lines[index].startswith("- "):
        match = FIELD_RE.match(lines[index])
        if not match:
            raise EpicError(f"malformed header line: {lines[index]!r}")
        key, value = match.group(1), match.group(2)
        if key in fields:
            raise EpicError(f"duplicate header field {key!r}")
        if key not in HEADER_KEYS:
            raise EpicError(
                f"unknown header field {key!r} (allowed: {', '.join(HEADER_KEYS)})"
            )
        if not value:
            raise EpicError(f"header field {key!r} is empty")
        fields[key] = value
        index += 1
    if tuple(fields) != HEADER_KEYS:
        missing = [k for k in HEADER_KEYS if k not in fields]
        extra_note = "" if not missing else f" (missing: {', '.join(missing)})"
        raise EpicError(
            f"the header must hold exactly {', '.join(HEADER_KEYS)}, in order{extra_note}"
        )
    state = fields["state"]
    if state not in STATES:
        raise EpicError(f"state must be one of {', '.join(STATES)}, got {state!r}")
    delivery = fields["delivery"]
    if delivery not in DELIVERIES:
        raise EpicError(
            f"delivery must be one of {', '.join(DELIVERIES)}, got {delivery!r}"
        )
    active_run = fields["active-run"]
    if active_run != "none" and not ID_RE.match(active_run):
        raise EpicError(f"active-run must be 'none' or a run id, got {active_run!r}")

    sections: list[tuple[str, list[str]]] = []
    while index < len(lines):
        line = lines[index]
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
            index += 1
            continue
        if sections:
            sections[-1][1].append(line)
        elif line.strip():
            raise EpicError(f"content before the first section: {line!r}")
        index += 1
    names = [name for name, _ in sections]
    if names != ["Tasks", "History"]:
        raise EpicError(
            f"epic.md must hold exactly '## Tasks' then '## History', got {names}"
        )

    tasks = _parse_tasks(sections[0][1], epic_id)
    history = _parse_history(sections[1][1])
    epic = Epic(
        epic_id=epic_id,
        title=fields["title"],
        goal=fields["goal"],
        base=fields["base"],
        state=state,
        delivery=delivery,
        active_run=active_run,
        tasks=tasks,
        history=history,
    )
    if root is not None:
        for row in tasks:
            require_member_task(root, row.task_id)
    return epic


def _parse_tasks(body: list[str], epic_id: str) -> list[EpicTask]:
    rows: list[str] = []
    header_seen = False
    for line in body:
        if not line.strip():
            continue
        cells = _table_cells(line)
        if cells is None:
            raise EpicError(f"malformed Tasks row (not a table line): {line!r}")
        if not header_seen:
            if cells != list(TASKS_HEADER):
                raise EpicError(
                    f"the Tasks header must be '| {' | '.join(TASKS_HEADER)} |', "
                    f"got {line!r}"
                )
            header_seen = True
            continue
        if set(cells[0]) <= set("-: ") and len(cells) == len(TASKS_HEADER):
            continue  # separator
        rows.append(line)
    tasks: list[EpicTask] = []
    seen: set[str] = set()
    for number, line in enumerate(rows, 1):
        cells = _table_cells(line)
        assert cells is not None
        if len(cells) != len(TASKS_HEADER):
            raise EpicError(f"malformed Tasks row (needs 3 cells): {line!r}")
        cell_number, task_id, deps_text = cells
        if cell_number != str(number):
            raise EpicError(
                f"Tasks rows are numbered from 1 in order; expected {number}, "
                f"got {cell_number!r} in {line!r}"
            )
        if not ID_RE.match(task_id):
            raise EpicError(f"invalid task id in Tasks row: {task_id!r}")
        if task_id in seen:
            raise EpicError(f"task id repeats in the Tasks table: {task_id}")
        seen.add(task_id)
        if deps_text == "-":
            depends = None
        else:
            parents = [d.strip() for d in deps_text.split(",")]
            if len(parents) > 1 or any(not p for p in parents):
                raise EpicError(
                    f"{task_id} lists {deps_text!r}: an epic task has at most one "
                    f"parent ({MULTIPLE_PARENTS_HINT})"
                )
            parent = parents[0]
            if parent not in seen:
                raise EpicError(
                    f"{task_id} depends on {parent!r}, which is not an earlier row "
                    "(forward, dangling and cyclic dependencies are refused)"
                )
            depends = parent
        tasks.append(EpicTask(number=number, task_id=task_id, depends=depends))
    if not header_seen:
        raise EpicError("the '## Tasks' section has no header row")
    _ = epic_id
    return tasks


def _parse_history(body: list[str]) -> list[str]:
    entries: list[str] = []
    for line in body:
        if not line.strip():
            continue
        match = HISTORY_RE.match(line)
        if not match:
            raise EpicError(f"malformed History line: {line!r}")
        try:
            moment = datetime.fromisoformat(match.group(1))
        except ValueError:
            raise EpicError(f"History timestamp is not ISO: {line!r}") from None
        if moment.tzinfo is None:
            raise EpicError(f"History timestamp needs a zone: {line!r}")
        entries.append(line)
    if not entries:
        raise EpicError("the '## History' section must hold at least one entry")
    return entries


def render_epic(epic: Epic) -> str:
    lines = [f"# Epic: {epic.epic_id}"]
    lines += [
        f"- title: {epic.title}",
        f"- goal: {epic.goal}",
        f"- base: {epic.base}",
        f"- state: {epic.state}",
        f"- delivery: {epic.delivery}",
        f"- active-run: {epic.active_run}",
        "## Tasks",
        f"| {' | '.join(TASKS_HEADER)} |",
        "| --- | --- | --- |",
    ]
    for row in epic.tasks:
        lines.append(
            f"| {row.number} | {row.task_id} | {row.depends if row.depends else '-'} |"
        )
    lines += ["## History", *epic.history, ""]
    return "\n".join(lines)


def load_epic(root: Path, epic_id: str, check_tasks: bool = True) -> Epic:
    path = epic_path(root, epic_id)
    if not path.is_file():
        raise EpicError(f"no epic {epic_id!r} under {work_dir(root) / EPICS_DIRNAME}")
    epic = parse_epic(read_epic_text(path), epic_id, root if check_tasks else None)
    epic.path = path
    return epic


def list_epic_ids(root: Path) -> list[str]:
    base = work_dir(root) / EPICS_DIRNAME
    if not base.is_dir():
        return []
    ids = []
    for entry in sorted(base.iterdir()):
        if is_link(entry) or not entry.is_dir():
            continue
        if (entry / EPIC_FILENAME).is_file():
            ids.append(entry.name)
    return ids


def iter_epics(root: Path, check_tasks: bool = False) -> list[Epic]:
    return [
        load_epic(root, eid, check_tasks=check_tasks) for eid in list_epic_ids(root)
    ]


# ------------------------------------------------------------- task namespace


def status_phase(root: Path, task_id: str) -> str:
    status = work_dir(root) / task_id / "status.md"
    if not status.is_file():
        raise EpicError(f"unknown task {task_id!r}: no .work/{task_id}/status.md")
    text = decode_utf8(status.read_bytes(), str(status))
    for line in text.splitlines():
        if line.startswith("- phase:"):
            return line.partition(":")[2].strip()
    raise EpicError(f"{task_id}: status.md has no '- phase:' line")


def require_member_task(root: Path, task_id: str) -> None:
    """A Tasks row must name an existing ordinary task, never an epic."""
    if (work_dir(root) / EPICS_DIRNAME / task_id).exists():
        raise EpicError(f"{task_id!r} is an epic id, not a task")
    status_phase(root, task_id)


def is_epic(root: Path, name: str) -> bool:
    return (work_dir(root) / EPICS_DIRNAME / name / EPIC_FILENAME).is_file()


def check_epic_id_free(root: Path, epic_id: str) -> None:
    if not ID_RE.match(epic_id):
        raise EpicError(f"invalid epic id {epic_id!r} (expected [a-z0-9][a-z0-9-]*)")
    if (work_dir(root) / epic_id).exists():
        raise EpicError(
            f"epic id {epic_id!r} collides with the existing task folder .work/{epic_id}"
        )
    if epic_path(root, epic_id).exists():
        raise EpicError(f"epic {epic_id!r} already exists")


def check_task_id_free(root: Path, task_id: str) -> None:
    """The other creation direction: a task id must not collide with an epic."""
    if (work_dir(root) / EPICS_DIRNAME / task_id).exists():
        raise EpicError(
            f"task id {task_id!r} collides with the existing epic {task_id}"
        )


def task_started(root: Path, task_id: str) -> bool:
    phase = status_phase(root, task_id)
    return (
        phase not in MEMBER_PHASES or (work_dir(root) / task_id / "worktree").exists()
    )


def require_eligible(root: Path, task_id: str) -> None:
    """Membership eligibility, mirroring `autopilot.py stack` without the git checks."""
    if is_epic(root, task_id):
        raise EpicError(f"{task_id!r} is an epic id, not a task")
    phase = status_phase(root, task_id)
    if phase in FAILED_PHASES:
        raise EpicError(
            f"{task_id} failed (phase {phase}); resolve it with /task-flow:task-run first"
        )
    if phase in IN_PROGRESS_PHASES:
        raise EpicError(f"{task_id} has already started (phase {phase})")
    if phase not in MEMBER_PHASES:
        raise EpicError(f"{task_id} has unrecognised phase {phase!r}")
    if (
        phase in ("planned", "approved")
        and not (work_dir(root) / task_id / "plan.md").is_file()
    ):
        raise EpicError(f"{task_id} is {phase} but has no plan.md")
    if (work_dir(root) / task_id / "worktree").exists():
        raise EpicError(f"{task_id} already has a worktree")


def epics_containing(root: Path, task_id: str, check_tasks: bool = False) -> list[Epic]:
    found = []
    for epic in iter_epics(root, check_tasks=check_tasks):
        if epic.row_of(task_id) is not None:
            found.append(epic)
    return found


def require_open_membership_free(
    root: Path, task_id: str, own: str | None = None
) -> None:
    for epic in epics_containing(root, task_id):
        if epic.epic_id == own:
            continue
        if derive_state(root, epic) != "completed":
            raise EpicError(
                f"{task_id} is already a member of the open epic {epic.epic_id!r}; "
                "a task belongs to at most one non-completed epic"
            )


def epic_for_task(root: Path, task_id: str) -> str | None:
    """The epic a finish-time trailer reports: the open one, else the latest completed.

    Unparseable epics are skipped — a malformed unrelated `epic.md` must not break
    every task's finish; `broken_epic_ids` names them for the caller.
    """
    containing: list[Epic] = []
    for epic_id in list_epic_ids(root):
        try:
            epic = load_epic(root, epic_id, check_tasks=False)
        except EpicError:
            continue
        if epic.row_of(task_id) is not None:
            containing.append(epic)
    chosen = choose_epic(root, containing)
    return chosen.epic_id if chosen else None


def broken_epic_ids(root: Path) -> list[str]:
    """Epic ids whose epic.md cannot be parsed at all (named, never fatal here)."""
    broken: list[str] = []
    for epic_id in list_epic_ids(root):
        try:
            load_epic(root, epic_id, check_tasks=False)
        except EpicError:
            broken.append(epic_id)
    return broken


def choose_epic(root: Path, candidates: list[Epic]) -> Epic | None:
    """The epic that reports a task: an open one, else the latest completed."""
    if not candidates:
        return None
    open_epics: list[Epic] = []
    for epic in candidates:
        try:
            state = derive_state(root, epic)
        except EpicError:
            state = "open"  # an underivable state is not safely completed
        if state != "completed":
            open_epics.append(epic)
    if open_epics:
        return open_epics[0]
    return max(
        candidates,
        key=lambda e: (e.history[-1].split(" ", 1)[0] if e.history else "", e.epic_id),
    )


# ------------------------------------------------------------------- states


def run_state(root: Path, run_id: str) -> str:
    run_md = work_dir(root) / "_autopilot" / run_id / "run.md"
    if not run_md.is_file():
        return "missing"
    text = decode_utf8(run_md.read_bytes(), str(run_md))
    for line in text.splitlines():
        if line.startswith("- state:"):
            return line.partition(":")[2].strip()
    return "unknown"


def derive_state(root: Path, epic: Epic) -> str:
    if not epic.tasks:
        return "pending"
    phases = {row.task_id: status_phase(root, row.task_id) for row in epic.tasks}
    if all(phase == "done" for phase in phases.values()):
        return "completed"
    if epic.active_run != "none":
        state = run_state(root, epic.active_run)
        if state == "running":
            return "active"
        if state in ("paused", "stopped"):
            return "paused"
    if any(phase in FAILED_PHASES for phase in phases.values()):
        return "paused"
    return "pending"


def recompute(root: Path, epic: Epic) -> Epic:
    epic.state = derive_state(root, epic)
    return epic


# ---------------------------------------------------------------- run table


def run_rows(root: Path, run_id: str) -> tuple[str, dict[str, tuple[str, str | None]]]:
    """(run state, {task id: (Tokens used, External USD)}) of a run.md table.

    A 4-column table (no `External USD`) counts as `0`, exactly like `autopilot.py`;
    an `unknown` cell stays `unknown` (sticky).
    """
    run_md = work_dir(root) / "_autopilot" / run_id / "run.md"
    if not run_md.is_file():
        raise EpicError(f"no run .work/_autopilot/{run_id}/run.md")
    text = decode_utf8(run_md.read_bytes(), str(run_md))
    state = "unknown"
    rows: dict[str, tuple[str, str | None]] = {}
    in_tasks = False
    width = 0
    for line in text.splitlines():
        if line.startswith("## "):
            in_tasks = line[3:].strip().lower() == "tasks"
            continue
        if line.startswith("- state:"):
            state = line.partition(":")[2].strip()
        if not in_tasks or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == "#":
            width = len(cells)
            continue
        if cells and set(cells[0]) <= set("-: "):
            continue
        if width not in (4, 5) or len(cells) != width:
            raise EpicError(f"malformed run.md task row: {line!r}")
        rows[cells[1]] = (cells[3], cells[4] if width == 5 else "0")
    return state, rows


def run_planning_tokens(root: Path, run_id: str) -> int | None:
    """The run.md `planning-tokens` header; None when `unknown` (sticky)."""
    run_md = work_dir(root) / "_autopilot" / run_id / "run.md"
    if not run_md.is_file():
        raise EpicError(f"no run .work/_autopilot/{run_id}/run.md")
    text = decode_utf8(run_md.read_bytes(), str(run_md))
    for line in text.splitlines():
        if line.startswith("## "):
            break
        if line.startswith("- planning-tokens:"):
            value = line.partition(":")[2].strip()
            if value == "unknown":
                return None
            if re.fullmatch(r"\d+", value):
                return int(value)
            raise EpicError(f"malformed planning-tokens: {value!r}")
    raise EpicError(f"run.md of {run_id!r} has no '- planning-tokens:' line")


def attempt_spend(root: Path, epic: Epic) -> dict[str, list[tuple[str, str | None]]]:
    """{task id: [(tokens cell, usd cell)]} from every attempts/<task>/attempt-*/spend.md."""
    result: dict[str, list[tuple[str, str | None]]] = {}
    attempts_root = epic_dir(root, epic.epic_id) / ATTEMPTS_DIRNAME
    if not attempts_root.is_dir():
        return result
    for task_dir in sorted(p for p in attempts_root.iterdir() if p.is_dir()):
        cells: list[tuple[str, str | None]] = []
        for attempt in sorted(p for p in task_dir.glob("attempt-*") if p.is_dir()):
            spend = attempt / SPEND_FILENAME
            if not spend.is_file():
                continue
            text = decode_utf8(spend.read_bytes(), str(spend))
            tokens = "unknown"
            usd: str | None = None
            for line in text.splitlines():
                if line.startswith("- tokens:"):
                    tokens = line.partition(":")[2].strip()
                elif line.startswith("- external USD:"):
                    usd = line.partition(":")[2].strip() or None
            cells.append((tokens, usd))
        if cells:
            result[task_dir.name] = cells
    return result


def _sum_cells(cells: list[tuple[str, str | None]]) -> tuple[str, str | None]:
    """Sum spend cells; `unknown` stays sticky, absent USD counts as unknown."""
    tokens = [c[0] for c in cells]
    usds = [c[1] for c in cells]
    token_cell = (
        "unknown"
        if any(t == "unknown" for t in tokens)
        else str(sum(int(t) for t in tokens))
    )
    if any(u in (None, "unknown") for u in usds):
        usd_cell = "unknown"
    else:
        usd_cell = f"{sum(float(u) for u in usds):.6f}"
    return token_cell, usd_cell


def epic_spend(
    root: Path, epic: Epic
) -> tuple[int | None, dict[str, tuple[str, str | None]]]:
    """(planning tokens or None=unknown, {task id: (tokens cell, usd cell)}) of an epic.

    Cumulative without double counting: the linked run's rows are cumulative per task
    and already include every archived attempt, so an archived `spend.md` is only
    counted for a task that has no row in the linked run (or when no run is linked).
    """
    planning: int | None = None
    rows: dict[str, tuple[str, str | None]] = {}
    if epic.active_run != "none":
        planning = run_planning_tokens(root, epic.active_run)
        _state, rows = run_rows(root, epic.active_run)
    archived = attempt_spend(root, epic)
    per_task: dict[str, tuple[str, str | None]] = {}
    for row in epic.tasks:
        task_id = row.task_id
        if task_id in rows:
            per_task[task_id] = _invocation_row(work_dir(root) / task_id, rows[task_id])
        elif task_id in archived:
            per_task[task_id] = _sum_cells(archived[task_id])
        else:
            per_task[task_id] = ("unknown", None)
    return planning, per_task


def _invocation_row(
    task_dir: Path, cell: tuple[str, str | None]
) -> tuple[str, str | None]:
    """A run row's (tokens, USD) cells, with the USD recomputed from invocation ids.

    When the task folder carries invocation files (impl-<n>.json implementer
    rounds, role-*.json external producer invocations such as the planner), the
    USD cell is recomputed from those files — planner USD included — so a resume
    recomputes the same total instead of double counting; the cell is the
    fallback when no invocation file exists. Unknown (null) cost stays unknown.
    """
    if not task_dir.is_dir():
        return cell
    if not any(task_dir.glob("impl-*.json")) and not any(task_dir.glob("role-*.json")):
        return cell
    total, unknown = role_runtime.invocation_usd(task_dir)["total"]
    return cell[0], "unknown" if unknown else f"{total:.6f}"


# ---------------------------------------------------------------- mutations


def save(root: Path, epic: Epic) -> None:
    if epic.path is None:
        epic.path = epic_path(root, epic.epic_id)
    recompute(root, epic)
    atomic_write(epic.path, render_epic(epic))


def split_ids(text: str) -> list[str]:
    ids = [part.strip() for part in text.split(",")]
    if not text.strip() or any(not i for i in ids):
        raise EpicError(f"the task list {text!r} has an empty element")
    seen: set[str] = set()
    for task_id in ids:
        if not ID_RE.match(task_id):
            raise EpicError(f"invalid task id {task_id!r}")
        if task_id in seen:
            raise EpicError(f"task id repeats: {task_id}")
        seen.add(task_id)
    return ids


def cmd_new(args: argparse.Namespace) -> str:
    root = Path(args.root)
    work_dir(root)
    check_epic_id_free(root, args.epic_id)
    tasks: list[EpicTask] = []
    if args.tasks:
        ids = split_ids(args.tasks)
        for task_id in ids:
            check_task_id_free(root, task_id)
            require_eligible(root, task_id)
            require_open_membership_free(root, task_id)
        for number, task_id in enumerate(ids, 1):
            tasks.append(
                EpicTask(
                    number=number,
                    task_id=task_id,
                    depends=ids[number - 2] if number > 1 else None,
                )
            )
        desc = f"{len(ids)} existing task(s) chained"
    else:
        desc = "goal-only; decomposition pending"
    epic = Epic(
        epic_id=args.epic_id,
        title=args.title,
        goal=args.goal,
        base=args.base,
        state="pending",
        delivery=args.delivery,
        active_run="none",
        tasks=tasks,
        history=[f"- {now_stamp()} created — {desc}"],
    )
    save(root, epic)
    return f"created: {args.epic_id} ({len(tasks)} task(s))"


def cmd_add(args: argparse.Namespace) -> str:
    root = Path(args.root)
    epic = load_epic(root, args.epic_id)
    if epic.state == "active" or derive_state(root, epic) == "active":
        raise EpicError(
            f"epic {args.epic_id!r} is active (run {epic.active_run}); "
            "membership is frozen while execution runs"
        )
    ids = split_ids(args.tasks)
    existing = epic.task_ids()
    for task_id in ids:
        if task_id in existing:
            raise EpicError(f"{task_id} is already a row of epic {args.epic_id!r}")
        check_task_id_free(root, task_id)
        require_eligible(root, task_id)
        require_open_membership_free(root, task_id, own=args.epic_id)
    parent = args.depends_on
    if parent is None:
        parent = existing[-1] if existing else None
    elif parent != "-" and parent not in existing:
        raise EpicError(
            f"--depends-on {parent!r} is not an earlier row of epic {args.epic_id!r}"
        )
    elif parent == "-":
        parent = None
    for task_id in ids:
        epic.tasks.append(
            EpicTask(number=len(epic.tasks) + 1, task_id=task_id, depends=parent)
        )
    epic.history.append(f"- {now_stamp()} added — {', '.join(ids)}")
    note = commit_membership_change(root, epic)
    message = (
        f"added: {', '.join(ids)} (epic {args.epic_id} now {len(epic.tasks)} row(s))"
    )
    if note is not None:
        message += f"; {note}"
    return message


def cmd_remove(args: argparse.Namespace) -> str:
    root = Path(args.root)
    epic = load_epic(root, args.epic_id)
    if epic.state == "active" or derive_state(root, epic) == "active":
        raise EpicError(
            f"epic {args.epic_id!r} is active (run {epic.active_run}); "
            "membership is frozen while execution runs"
        )
    ids = split_ids(args.tasks)
    for task_id in ids:
        if epic.row_of(task_id) is None:
            raise EpicError(f"{task_id} is not a row of epic {args.epic_id!r}")
        if task_started(root, task_id):
            raise EpicError(
                f"{task_id} has started; a started task cannot be removed from an epic"
            )
    removed = set(ids)
    for row in epic.tasks:
        if row.depends in removed:
            raise EpicError(
                f"removing {', '.join(sorted(removed))} would leave {row.task_id} "
                "depending on a removed task (dangling dependency)"
            )
    epic.tasks = [row for row in epic.tasks if row.task_id not in removed]
    for number, row in enumerate(epic.tasks, 1):
        row.number = number
    epic.history.append(f"- {now_stamp()} removed — {', '.join(ids)}")
    note = commit_membership_change(root, epic)
    message = (
        f"removed: {', '.join(ids)} (epic {args.epic_id} now {len(epic.tasks)} row(s))"
    )
    if note is not None:
        message += f"; {note}"
    return message


def _run_md_source_epic(root: Path, run_id: str, epic_id: str) -> None:
    run_md = work_dir(root) / "_autopilot" / run_id / "run.md"
    if not ID_RE.match(run_id) or not run_md.is_file():
        raise EpicError(
            f"no autopilot run {run_id!r} (.work/_autopilot/{run_id}/run.md)"
        )
    text = decode_utf8(run_md.read_bytes(), str(run_md))
    fields = {}
    for line in text.splitlines():
        if line.startswith("## "):
            break
        if line.startswith("- "):
            key, sep, value = line[2:].partition(":")
            if sep:
                fields.setdefault(key.strip(), value.strip())
    if fields.get("source") != "epic":
        raise EpicError(f"run {run_id!r} does not declare 'source: epic'")
    if fields.get("epic") != epic_id:
        raise EpicError(f"run {run_id!r} is not linked to epic {epic_id!r}")


# ------------------------------------------------- linked run regeneration


def plan_run_sync(root: Path, epic: Epic) -> tuple[Path, str, str]:
    """Validate the linked run and build its regenerated `run.md` text.

    The `## Tasks` table is rebuilt from the epic graph: existing rows keep their
    `Tokens used` / `External USD` cells, new rows start at `0`; every header field
    (including `planning-tokens`) and the History section are kept, one History line
    is appended and the run state becomes `planning`, so the estimate and the start
    gate run again on resume. Returns (run.md path, new text, note); raises before
    anything is written when the run cannot be regenerated.
    """
    if epic.active_run == "none":
        raise EpicError(f"epic {epic.epic_id!r} has no linked run; nothing to sync")
    _run_md_source_epic(root, epic.active_run, epic.epic_id)
    run_md = work_dir(root) / "_autopilot" / epic.active_run / "run.md"
    text = decode_utf8(run_md.read_bytes(), str(run_md))
    newline = "\r\n" if "\r\n" in text else "\n"
    raw = [line.removesuffix("\r") for line in text.split("\n")]
    header_end = next((i for i, l in enumerate(raw) if l.startswith("## ")), len(raw))
    state_index = next(
        (i for i in range(header_end) if raw[i].startswith("- state:")), None
    )
    if state_index is None:
        raise EpicError(f"run.md of {epic.active_run!r} has no '- state:' line")
    tasks_index = next(
        (i for i in range(header_end, len(raw)) if raw[i] == "## Tasks"), None
    )
    if tasks_index is None:
        raise EpicError(f"run.md of {epic.active_run!r} has no '## Tasks' section")
    history_index = next(
        (i for i in range(tasks_index + 1, len(raw)) if raw[i].startswith("## ")), None
    )
    if history_index is None or raw[history_index] != "## History":
        raise EpicError(f"run.md of {epic.active_run!r} has no '## History' section")
    tasks_end = history_index
    history_end = next(
        (i for i in range(history_index + 1, len(raw)) if raw[i].startswith("## ")),
        len(raw),
    )
    width: int | None = None
    old: dict[str, list[str]] = {}
    table_end = tasks_index + 1
    for i in range(tasks_index + 1, tasks_end):
        line = raw[i]
        if not line.startswith("|"):
            continue
        table_end = i + 1
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == "#":
            width = len(cells)
            if width == 5 and cells[4] != "External USD":
                raise EpicError(
                    f"the 5th header cell of run.md must be 'External USD', "
                    f"got {cells[4]!r}"
                )
            continue
        if cells and set(cells[0]) <= set("-: "):
            continue
        if width not in (4, 5) or len(cells) != width:
            raise EpicError(f"malformed run.md task row: {line!r}")
        old[cells[1]] = cells
    if width not in (4, 5):
        raise EpicError(f"run.md of {epic.active_run!r} has no '## Tasks' header row")
    header = ["#", "Task", "Depends on", "Tokens used"]
    if width == 5:
        header.append("External USD")
    table = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    added: list[str] = []
    for row in epic.tasks:
        cells = old.get(row.task_id)
        tokens = cells[3] if cells is not None else "0"
        line = f"| {row.number} | {row.task_id} | {row.depends or '-'} | {tokens} |"
        if width == 5:
            line += f" {cells[4] if cells is not None else '0'} |"
        if cells is None:
            added.append(row.task_id)
        table.append(line)
    raw[state_index] = "- state: planning"
    pos = history_end
    while pos > history_index + 1 and not raw[pos - 1].strip():
        pos -= 1
    raw.insert(
        pos,
        f"- {now_stamp()} planning — membership changed; ## Tasks regenerated "
        f"from epic {epic.epic_id} (run state planning)",
    )
    new_lines = raw[: tasks_index + 1] + table + raw[table_end:]
    new_text = newline.join(new_lines)
    kept = len(epic.tasks) - len(added)
    note = (
        f"run {epic.active_run} table regenerated from epic {epic.epic_id}: "
        f"{len(epic.tasks)} row(s), {kept} kept with their totals"
    )
    if added:
        note += f", added {', '.join(added)}"
    note += "; run state planning"
    return run_md, new_text, note


def commit_membership_change(root: Path, epic: Epic) -> str | None:
    """Save the epic after an idle membership change, syncing the linked run first.

    Everything is validated before the first write, so a refusal changes no file:
    the linked run's table is regenerated (state `planning`) and then the epic is
    saved. Returns the sync note, or None when no run is linked.
    """
    note: str | None = None
    if epic.active_run != "none":
        run_md, new_text, note = plan_run_sync(root, epic)
        atomic_write(run_md, new_text)
    save(root, epic)
    return note


def cmd_sync_run(args: argparse.Namespace) -> str:
    root = Path(args.root)
    epic = load_epic(root, args.epic_id)
    if epic.active_run == "none":
        raise EpicError(f"epic {args.epic_id!r} has no linked run; nothing to sync")
    if epic.state == "active" or derive_state(root, epic) == "active":
        raise EpicError(
            f"epic {args.epic_id!r} is active (run {epic.active_run}); "
            "the linked run cannot be regenerated while execution runs"
        )
    if derive_state(root, epic) == "completed":
        raise EpicError(
            f"epic {args.epic_id!r} is completed; its membership is history"
        )
    epic.history.append(
        f"- {now_stamp()} synced — run {epic.active_run} ## Tasks regenerated "
        "from the epic; run state planning"
    )
    note = commit_membership_change(root, epic)
    return f"synced: {note}"


def cmd_link(args: argparse.Namespace) -> str:
    root = Path(args.root)
    epic = load_epic(root, args.epic_id)
    if epic.active_run == args.run_id:
        return (
            f"unchanged: {args.epic_id} is already linked to run {args.run_id}; "
            "resuming that run changes nothing"
        )
    if epic.active_run != "none":
        raise EpicError(
            f"epic {args.epic_id!r} is already linked to run {epic.active_run!r}"
        )
    _run_md_source_epic(root, args.run_id, args.epic_id)
    epic.active_run = args.run_id
    epic.history.append(f"- {now_stamp()} linked — run {args.run_id}")
    save(root, epic)
    return f"linked: {args.epic_id} -> run {args.run_id}"


def cmd_unlink(args: argparse.Namespace) -> str:
    root = Path(args.root)
    epic = load_epic(root, args.epic_id)
    if epic.active_run == "none":
        raise EpicError(f"epic {args.epic_id!r} has no active run")
    run = epic.active_run
    epic.active_run = "none"
    epic.history.append(f"- {now_stamp()} unlinked — run {run}")
    save(root, epic)
    return f"unlinked: {args.epic_id} (was run {run})"


def cmd_validate(args: argparse.Namespace) -> str:
    root = Path(args.root)
    epic = load_epic(root, args.epic_id)
    derived = derive_state(root, epic)
    if epic.state != derived:
        raise EpicError(
            f"epic.md state is {epic.state!r} but the derived state is {derived!r}; "
            "run a mutation command or fix the file"
        )
    return (
        f"ok: {args.epic_id} — state {epic.state}, {len(epic.tasks)} task(s), "
        f"active-run {epic.active_run}"
    )


def cmd_epic_of(args: argparse.Namespace) -> str:
    root = Path(args.root)
    epic_id = epic_for_task(root, args.task_id)
    return epic_id if epic_id else "none"


def cmd_is_epic(args: argparse.Namespace) -> str:
    return "yes" if is_epic(Path(args.root), args.name) else "no"


def cmd_list(args: argparse.Namespace) -> str:
    ids = list_epic_ids(Path(args.root))
    return "\n".join(ids) if ids else "none"


# ------------------------------------------------------- status and report


def _phase_class(phase: str) -> str:
    if phase == "done":
        return "done"
    if phase in FAILED_PHASES:
        return "failed"
    if phase in IN_PROGRESS_PHASES:
        return "active"
    return "open"


def _mermaid_escape(text: str) -> str:
    return text.replace('"', "#quot;")


def mermaid_diagram(root: Path, epic: Epic) -> str:
    phases = {row.task_id: status_phase(root, row.task_id) for row in epic.tasks}
    lines = ["flowchart TD"]
    classes: dict[str, list[str]] = {}
    for row in epic.tasks:
        node = f"t{row.number}"
        label = _mermaid_escape(f"{row.task_id} — {phases[row.task_id]}")
        lines.append(f'  {node}["{label}"]')
        classes.setdefault(_phase_class(phases[row.task_id]), []).append(node)
    for row in epic.tasks:
        if row.depends:
            parent = epic.row_of(row.depends)
            assert parent is not None
            lines.append(f"  t{parent.number} --> t{row.number}")
    lines += [
        "  classDef done fill:#d4f8d4,stroke:#1a7f37,color:#0b3d0b",
        "  classDef failed fill:#ffd7d5,stroke:#cf222e,color:#6e1119",
        "  classDef active fill:#fff3cd,stroke:#9a6700,color:#5c4400",
        "  classDef open fill:#e8e8e8,stroke:#666666,color:#000000",
    ]
    for cls in ("done", "failed", "active", "open"):
        if classes.get(cls):
            lines.append(f"  class {' '.join(classes[cls])} {cls}")
    return "\n".join(lines)


def blocker_chain(root: Path, epic: Epic, task_id: str) -> str:
    """The failed tasks this task transitively depends on, nearest first."""
    by_id = {row.task_id: row for row in epic.tasks}
    seen: set[str] = set()
    chain: list[str] = []

    def walk(current: str) -> None:
        row = by_id.get(current)
        if row is None or row.depends is None or row.depends in seen:
            return
        seen.add(row.depends)
        parent = row.depends
        if status_phase(root, parent) in FAILED_PHASES:
            chain.append(f"{parent} ({status_phase(root, parent)})")
        walk(parent)

    walk(task_id)
    return ", ".join(chain) if chain else "-"


def next_action(root: Path, epic: Epic, task_id: str) -> str:
    phase = status_phase(root, task_id)
    if phase == "done":
        return "none (done; never rerun)"
    if phase in FAILED_PHASES:
        return (
            f"retry: /task-flow:task-epic retry {epic.epic_id} {task_id} "
            "(needs your authorization), then /task-flow:task-run "
            f"{task_id}; declining keeps dependents blocked"
        )
    if phase in IN_PROGRESS_PHASES:
        return f"resume: /task-flow:task-run {task_id}"
    if epic.active_run != "none":
        return f"runs in run {epic.active_run} via /task-flow:task-autopilot {epic.active_run}"
    return f"starts when the epic runs: /task-flow:task-epic run {epic.epic_id}"


def status_report(root: Path, epic: Epic) -> str:
    phases = {row.task_id: status_phase(root, row.task_id) for row in epic.tasks}
    rounds = {
        row.task_id: _status_field(root, row.task_id, "round") for row in epic.tasks
    }
    verdicts = {
        row.task_id: _status_field(root, row.task_id, "last-verdict")
        for row in epic.tasks
    }
    planning, spend = epic_spend(root, epic)
    tokens = {task_id: spend[task_id][0] for task_id in epic.task_ids()}
    usd = {task_id: spend[task_id][1] for task_id in epic.task_ids()}
    done = sum(1 for p in phases.values() if p == "done")
    failed = sum(1 for p in phases.values() if p in FAILED_PHASES)
    lines = [
        f"# Epic: {epic.epic_id}",
        f"- title: {epic.title}",
        f"- goal: {epic.goal}",
        f"- state: {derive_state(root, epic)} (stored: {epic.state})",
        f"- base: {epic.base}",
        f"- delivery: {epic.delivery} (intent only; no push, PR or merge)",
        f"- active-run: {epic.active_run}",
        (
            f"- tasks: {len(epic.tasks)} (done {done}, failed {failed}, "
            f"open {len(epic.tasks) - done - failed})"
        ),
        "## Tasks",
        (
            "| # | Task | Depends on | Phase | Round | Verdict | Risk | Tokens used | "
            "External USD | Blocked by | Next action |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    known_tokens = planning if planning is not None else 0
    unknown_tokens = planning is None
    known_usd = 0.0
    unknown_usd = False
    for row in epic.tasks:
        task_id = row.task_id
        token_cell = tokens[task_id]
        if token_cell == "unknown":
            unknown_tokens = True
            token_show = "at least 0 (unknown)"
        else:
            known_tokens += int(token_cell)
            token_show = token_cell
        usd_cell = usd[task_id]
        if usd_cell in (None, "unknown"):
            unknown_usd = True
            usd_show = "at least 0 (unknown)"
        else:
            known_usd += float(usd_cell)
            usd_show = usd_cell
        cells = (
            str(row.number),
            task_id,
            row.depends if row.depends else "-",
            phases[task_id],
            rounds[task_id],
            verdicts[task_id],
            RISK_TEXT,
            token_show,
            usd_show,
            blocker_chain(root, epic, task_id),
            next_action(root, epic, task_id),
        )
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    total = f"at least {known_tokens}" if unknown_tokens else str(known_tokens)
    usd_total = f"at least {known_usd:.2f}" if unknown_usd else f"{known_usd:.2f}"
    if epic.active_run != "none":
        planning_note = "unknown" if planning is None else str(planning)
        run_note = (
            f"planning-tokens {planning_note} plus the task rows of run "
            f"{epic.active_run}; archived attempt spend is already inside those rows"
        )
    else:
        run_note = (
            "no run linked; only archived attempt spend is known and planning is "
            "unknown"
        )
    lines += [
        "## Spend",
        f"- tokens: {total} ({run_note})",
        f"- external USD: {usd_total}",
        "## Diagram",
        "```mermaid",
        mermaid_diagram(root, epic),
        "```",
    ]
    return "\n".join(lines) + "\n"


def _status_field(root: Path, task_id: str, key: str) -> str:
    status = work_dir(root) / task_id / "status.md"
    try:
        text = status.read_text(encoding="utf-8")
    except OSError:
        return "?"
    for line in text.splitlines():
        if line.startswith(f"- {key}:"):
            return line.partition(":")[2].strip()
    return "?"


def report_text(root: Path, epic: Epic) -> str:
    if derive_state(root, epic) != "completed":
        raise EpicError(
            f"epic {epic.epic_id} is not completed; "
            "partial completion is never reported as success"
        )
    lines = [
        f"# Epic report: {epic.epic_id}",
        "",
        f"- goal: {epic.goal}",
        f"- base: {epic.base}",
        (
            f"- delivery: {epic.delivery} (intent only — no push, PR, merge or integration "
            "commit was made)"
        ),
        "",
        "## Results",
        "",
        "| # | Task | Depends on | Phase | Round | Verdict | Tokens used | External USD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    planning, spend = epic_spend(root, epic)
    known_tokens = planning if planning is not None else 0
    unknown_tokens = planning is None
    known_usd = 0.0
    unknown_usd = False
    for row in epic.tasks:
        task_id = row.task_id
        token_cell, usd_cell = spend[task_id]
        if token_cell == "unknown":
            unknown_tokens = True
        else:
            known_tokens += int(token_cell)
        if usd_cell in (None, "unknown"):
            unknown_usd = True
            usd_show = "unknown"
        else:
            known_usd += float(usd_cell)
            usd_show = usd_cell
        lines.append(
            f"| {row.number} | {task_id} | {row.depends or '-'} | "
            f"{status_phase(root, task_id)} | {_status_field(root, task_id, 'round')} | "
            f"{_status_field(root, task_id, 'last-verdict')} | {token_cell} | {usd_show} |"
        )
    total = f"at least {known_tokens}" if unknown_tokens else str(known_tokens)
    usd_total = f"at least {known_usd:.2f}" if unknown_usd else f"{known_usd:.2f}"
    if epic.active_run != "none":
        planning_note = "unknown" if planning is None else str(planning)
        run_note = (
            f"planning-tokens {planning_note} plus the task rows of run "
            f"{epic.active_run}; archived attempt spend is already inside those rows"
        )
    else:
        run_note = "no run linked; only archived attempt spend is known"
    retries = [line for line in epic.history if " retry " in line]
    failures = [
        row.task_id
        for row in epic.tasks
        if status_phase(root, row.task_id) in FAILED_PHASES
    ]
    branches = [f"task/{row.task_id}" for row in epic.tasks]
    lines += [
        "",
        "## Cumulative spend",
        "",
        f"- tokens: {total} ({run_note})",
        f"- external USD: {usd_total}",
        "",
        "## Failures and retries",
        "",
    ]
    lines += [f"- {line}" for line in retries] or ["- none"]
    lines += [
        f"- failed tasks at report time: {', '.join(failures) if failures else 'none'}",
        "",
        "## Delivery branches",
        "",
        "All branches of the forest, not only the last one:",
        "",
    ]
    lines += [f"- {branch}" for branch in branches]
    lines += [
        "",
        "Every member task is done; partial completion is never reported as success.",
        "",
    ]
    return "\n".join(lines)


def cmd_status(args: argparse.Namespace) -> str:
    root = Path(args.root)
    epic = load_epic(root, args.epic_id)
    return status_report(root, epic).rstrip("\n")


def cmd_report(args: argparse.Namespace) -> str:
    root = Path(args.root)
    epic = load_epic(root, args.epic_id)
    text = report_text(root, epic)
    dest = epic_dir(root, args.epic_id) / REPORT_FILENAME
    atomic_write(dest, text)
    return f"wrote: {dest.relative_to(root)} ({len(epic.tasks)} task(s), all done)"


# --------------------------------------------------------------------- retry


def reset_task_status(root: Path, task_id: str, note: str) -> None:
    """The authorized round reset of a task's status.md.

    The header lines are edited in place (phase back to the resumable `approved`,
    round 0) and one line is appended at the end of `## History`, exactly as the
    orchestrator does after a phase; the rest of the file is kept byte for byte.
    """
    status = work_dir(root) / task_id / "status.md"
    text = decode_utf8(status.read_bytes(), str(status))
    crlf = "\r\n" in text
    suffix = "\r" if crlf else ""
    lines = text.split("\n")
    seen: set[str] = set()
    for index, line in enumerate(lines):
        core = line.removesuffix("\r")
        if core.startswith("- phase:"):
            lines[index] = f"- phase: {RESET_PHASE}" + suffix
            seen.add("phase")
        elif core.startswith("- round:"):
            lines[index] = "- round: 0" + suffix
            seen.add("round")
    if seen != {"phase", "round"}:
        raise EpicError(
            f"{task_id}: status.md has no phase/round header lines to reset"
        )
    entry = f"- {now_stamp()} {RESET_PHASE} — {note}" + suffix
    out: list[str] = []
    inserted = False
    for line in lines:
        core = line.removesuffix("\r")
        if not inserted and core.startswith("## Log"):
            out.append(entry)
            inserted = True
        out.append(line)
    if not inserted:
        while out and out[-1] == "":
            out.pop()
        out += [entry, ""]
    atomic_write(status, "\n".join(out))


def _attempt_spend_cells(root: Path, epic: Epic, task_id: str) -> tuple[str, str]:
    """The task's current (tokens, USD) cells of the linked run, else unknown."""
    if epic.active_run != "none":
        _state, rows = run_rows(root, epic.active_run)
        cell = rows.get(task_id)
        if cell is not None:
            return cell[0], cell[1] if cell[1] is not None else "unknown"
    return "unknown", "unknown"


def cmd_retry(args: argparse.Namespace) -> str:
    root = Path(args.root)
    epic = load_epic(root, args.epic_id)
    if epic.row_of(args.task_id) is None:
        raise EpicError(f"{args.task_id} is not a row of epic {args.epic_id!r}")
    if not args.authorized:
        raise EpicError(
            f"retrying {args.task_id} resets its round count; that needs your explicit "
            "authorization (--authorized). --auto does not authorize a reset. "
            "Nothing was changed."
        )
    source = work_dir(root) / args.task_id
    artifacts = sorted(
        p for pattern in ARCHIVE_GLOBS for p in source.glob(pattern) if p.is_file()
    )
    attempts_root = epic_dir(root, args.epic_id) / ATTEMPTS_DIRNAME / args.task_id
    existing = (
        sorted(p for p in attempts_root.glob("attempt-*") if p.is_dir())
        if attempts_root.is_dir()
        else []
    )
    if existing:
        newest = existing[-1]
        archived = sorted(
            p for p in newest.iterdir() if p.is_file() and p.name != SPEND_FILENAME
        )
        if [p.name for p in archived] == [p.name for p in artifacts] and all(
            a.read_bytes() == b.read_bytes() for a, b in zip(archived, artifacts)
        ):
            return (
                f"unchanged: attempt {len(existing)} of {args.task_id} is already "
                "archived; nothing was duplicated"
            )
    phase = status_phase(root, args.task_id)
    if phase not in FAILED_PHASES:
        raise EpicError(
            f"{args.task_id} has not failed (phase {phase!r}); there is nothing to retry"
        )
    previous_round = _status_field(root, args.task_id, "round")
    number = len(existing) + 1
    dest = attempts_root / f"attempt-{number}"
    dest.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        shutil.copy2(artifact, dest / artifact.name)
    spend_tokens, spend_usd = _attempt_spend_cells(root, epic, args.task_id)
    atomic_write(
        dest / SPEND_FILENAME,
        f"# Attempt {number} spend snapshot: {args.task_id}\n"
        f"- tokens: {spend_tokens}\n"
        f"- external USD: {spend_usd}\n",
    )
    reset_task_status(
        root,
        args.task_id,
        f"round reset to 0 for attempt {number} of epic {args.epic_id} "
        "(authorized by the user; --auto never authorizes)",
    )
    epic.history.append(
        f"- {now_stamp()} retry {args.task_id} — attempt {number} archived "
        f"({len(artifacts)} file(s) plus the spend snapshot); round {previous_round} "
        "reset to 0 for the new attempt (authorized by the user)"
    )
    save(root, epic)
    return (
        f"archived: attempt {number} of {args.task_id} "
        f"({len(artifacts)} file(s) under {dest.relative_to(root)}); "
        f"{args.task_id} status.md reset to phase {RESET_PHASE}, round 0 "
        f"(previous round {previous_round}) — /task-flow:task-run {args.task_id} "
        "starts the new authorized attempt at round 1"
    )


# ----------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_text):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("root", help="the repository root that holds .work/")
        return command

    new = add("new", "create an epic (optionally grouping existing tasks)")
    new.add_argument("epic_id")
    new.add_argument("--title", required=True)
    new.add_argument("--goal", required=True)
    new.add_argument("--base", required=True)
    new.add_argument("--delivery", choices=DELIVERIES, default="per-task")
    new.add_argument("--tasks", default=None, help="comma-separated existing task ids")
    new.set_defaults(func=cmd_new)

    add_task = add("add", "append existing tasks to an idle epic")
    add_task.add_argument("epic_id")
    add_task.add_argument("--tasks", required=True)
    add_task.add_argument("--depends-on", default=None)
    add_task.set_defaults(func=cmd_add)

    remove = add("remove", "remove rows from an idle epic")
    remove.add_argument("epic_id")
    remove.add_argument("--tasks", required=True)
    remove.set_defaults(func=cmd_remove)

    link = add("link", "set active-run to a run whose run.md declares source: epic")
    link.add_argument("epic_id")
    link.add_argument("run_id")
    link.set_defaults(func=cmd_link)

    unlink = add("unlink", "clear active-run")
    unlink.add_argument("epic_id")
    unlink.set_defaults(func=cmd_unlink)

    validate = add("validate", "re-parse and check the epic against the repository")
    validate.add_argument("epic_id")
    validate.set_defaults(func=cmd_validate)

    epic_of = add("epic-of", "print the epic id that reports a task, or 'none'")
    epic_of.add_argument("task_id")
    epic_of.set_defaults(func=cmd_epic_of)

    is_epic_cmd = add("is-epic", "print 'yes' when the id names an epic")
    is_epic_cmd.add_argument("name")
    is_epic_cmd.set_defaults(func=cmd_is_epic)

    listing = add("list", "print the epic ids")
    listing.set_defaults(func=cmd_list)

    status = add("status", "deterministic status report of one epic")
    status.add_argument("epic_id")
    status.set_defaults(func=cmd_status)

    report = add("report", "write report.md of a completed epic")
    report.add_argument("epic_id")
    report.set_defaults(func=cmd_report)

    retry = add("retry", "archive a failed task's round evidence before a reset")
    retry.add_argument("epic_id")
    retry.add_argument("task_id")
    retry.add_argument(
        "--authorized",
        action="store_true",
        help="explicit user authorization of the round reset (--auto never passes it)",
    )
    retry.set_defaults(func=cmd_retry)

    sync_run = add(
        "sync-run", "regenerate the linked run's run.md table from the epic graph"
    )
    sync_run.add_argument("epic_id")
    sync_run.set_defaults(func=cmd_sync_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.root = str(Path(args.root).resolve())
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        output = args.func(args)
    except EpicError as exc:
        for line in str(exc).splitlines():
            print(f"epics.py: {line}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
