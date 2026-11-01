# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Helper for /task-flow:task-autopilot: repository check, token estimate, circuit breaker.

Subcommands (exit 2 with a message on stderr and nothing on stdout when refusing):

- `inspect <repo-root>`: four lines — `commits: yes|no`, `tracked: <n> files outside .work/`,
  `claude-md: absent | <n> non-blank lines`, `thin: yes|no`. A repository is *thin* when it
  has no commit, or when every tracked file (`git ls-files`, ignoring `.work/`) is in the
  meta set (CLAUDE.md, AGENTS.md, anything under .claude/, README[.*], LICENSE[.*],
  .gitignore, .gitattributes, .editorconfig; top level only except .claude/) and
  CLAUDE.md is absent or has at most 40 non-blank lines.
- `stack <repo-root> <ids> [--implementer-kind {claude,profile}]`: validates a
  comma-separated list of existing `.work/<id>/` tasks and prints the `## Tasks` table
  for `run.md` (header, separator, one row per id, `Depends on` the previous id or `-`
  for row 1, `Tokens used` `0`, plus `External USD` `0` with `profile`). Accepted phases
  are exactly `new`, `planned`, `approved`; refused are an empty or malformed list, a
  duplicate id, an unknown id, a `done` task, an `escalated`/`blocked` task, a task in
  progress, an unrecognised phase, a `planned`/`approved` task without `plan.md`, a task
  with a worktree, a task whose branch `task/<id>` exists, and a repo root that is not
  the top level of a git work tree.
- `estimate <run-dir>`: a Markdown table of coarse token estimates per task plus the
  spent and budget lines. Per task (constants below are coarse defaults, not measured):
  CHECK = 15000 + 3000 * commands, IMPLEMENT = 30000 + 12000 * steps,
  REVIEW = 30000 + 6000 * criteria, FINISH = 10000, ROUND = IMPLEMENT + CHECK + REVIEW,
  one round = CHECK + ROUND + FINISH, max = CHECK + 3 * ROUND + FINISH. Tasks in phase
  `new` or `planned` count as pending, because the overview runs before the start gate,
  when every task is still `planned`. Pending sums leave out tasks `next` would skip
  (dependents, direct or transitive, of a failed task). `estimate` and `next` take
  `--implementer-kind {claude,profile}` (default `claude`): with `profile`,
  IMPLEMENT = 0 — an external implementer spends no Claude tokens, its USD cost is
  measured per round (`impl-<n>.json`), not estimated — and `estimate` additionally
  prints an `implementer: external profile; ...` line. The `## Tasks` table may carry an
  optional fifth column `External USD` (a non-negative decimal, scientific notation
  accepted, or `unknown` per row); when present, `estimate` ends with an
  `external spent: <sum> USD` line (`at least` when a row is `unknown`), and the
  column never influences `next`. The `spent:` and `budget:` lines are about Claude
  tokens only.
- `next <run-dir>`: one line, `run <id>`, `wait <id>: <phase>`, `stop: <reason>` or `done`.
  The circuit breaker. It is evaluated between tasks only: a task already in progress
  always gives `run <id>`. Triggers, first match wins: a task in progress; wrong direction
  (the last two finished tasks both failed); semi mode and a failed task not in
  continue-past; nothing runnable left (cannot finish, or done); burn rate (the last
  finished task used more than 2 x its max estimate); budget (spent + the next task's max
  estimate exceeds the budget). All are counts and sums read from files, and all count
  Claude tokens only: `--implementer-kind` is as in `estimate`, and under `profile` the
  burn-rate and budget triggers compare Claude tokens with a Claude-only estimate
  (IMPLEMENT = 0).
- `base <run-dir> <task-id>`: one line — `task/<id>` of the last `done` row of the
  `## Tasks` table in table order, or the run's `base:` header when no row is `done`.
  Refused when the id is not a row, when its phase is not `new`, `planned` or
  `approved`, when `.work/<id>/worktree` exists, or when `run.md` has no `base:`.

`<run-dir>` is `.work/_autopilot/<run-id>/` with a `run.md` written by the orchestrator;
this script only reads. The optional header line `source: backlog | tasks` (absent means
`backlog`) marks a run made from an explicit task list: `tasks` requires `bootstrap:
none`, row 1 depending on `-`, and every later row depending on exactly the previous
row, and `estimate` appends a `warning:` line when such a run has more than 10 rows.
Stdlib only, started by `uv run --script`.
"""

import argparse
import fnmatch
import itertools
import math
import os
import re
import subprocess
import sys
from pathlib import Path

# Coarse default estimate constants (tokens). Invented, not measured.
CHECK_BASE, CHECK_PER_COMMAND = 15000, 3000
IMPLEMENT_BASE, IMPLEMENT_PER_STEP = 30000, 12000
REVIEW_BASE, REVIEW_PER_CRITERION = 30000, 6000
FINISH = 10000
BURN_FACTOR = 2
THIN_CLAUDE_MD_LINES = 40
GIT_TIMEOUT_SECONDS = 30

META_NAMES = ("CLAUDE.md", "AGENTS.md", "README", "README.*", "LICENSE", "LICENSE.*")
META_DOTFILES = (".gitignore", ".gitattributes", ".editorconfig")

FINISHED_OK = {"done"}
FAILED = {"escalated", "blocked"}
IN_PROGRESS = {"baseline", "implementing", "checking", "reviewing", "review-approved"}
PENDING = {"approved"}
UNAPPROVED = {"new", "planned"}


class Refusal(Exception):
    pass


# --- inspect -------------------------------------------------------------------------


def git(root: str, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Refusal(f"cannot run git: {error}") from error


def is_meta(path: str) -> bool:
    if path.startswith(".claude/"):
        return True
    if "/" in path:
        return False
    if path in META_DOTFILES:
        return True
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in META_NAMES)


def require_toplevel(root: str) -> None:
    """Refuse unless root is the top level of a git work tree."""
    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise Refusal(f"{root} is not inside a git work tree")
    toplevel = top.stdout.decode("utf-8", "replace").strip()
    try:
        same = os.path.samefile(toplevel, root)
    except OSError:
        same = False
    if not same:
        raise Refusal(f"{root} is not the top level of a git work tree ({toplevel})")


def inspect(root: str) -> str:
    require_toplevel(root)

    has_commit = git(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode == 0
    listed = git(root, "ls-files", "-z")
    if listed.returncode != 0:
        raise Refusal("git ls-files failed")
    tracked = [
        p
        for p in listed.stdout.decode("utf-8", "replace").split("\0")
        if p and not p.startswith(".work/")
    ]

    claude = Path(root) / "CLAUDE.md"
    lines: int | None = None
    if claude.is_file():
        try:
            text = claude.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise Refusal(f"cannot read CLAUDE.md: {error}") from error
        lines = sum(1 for line in text.splitlines() if line.strip())

    thin = (not has_commit) or (
        all(is_meta(p) for p in tracked)
        and (lines is None or lines <= THIN_CLAUDE_MD_LINES)
    )
    claude_md = "absent" if lines is None else f"{lines} non-blank lines"
    return "\n".join(
        [
            f"commits: {'yes' if has_commit else 'no'}",
            f"tracked: {len(tracked)} files outside .work/",
            f"claude-md: {claude_md}",
            f"thin: {'yes' if thin else 'no'}",
        ]
    )


# --- run.md and plans ----------------------------------------------------------------


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise Refusal(f"cannot read {path}: {error}") from error


def fence_of(line: str) -> tuple[str, int, str] | None:
    """(character, length, rest) when the line is a ``` or ~~~ fence line, else None."""
    match = re.match(r"\s*(`{3,}|~{3,})(.*)", line)
    return (match.group(1)[0], len(match.group(1)), match.group(2)) if match else None


class FenceTracker:
    """CommonMark fence rule: a fence closes on the same character, at least as long."""

    def __init__(self) -> None:
        self.opening: tuple[str, int] | None = None

    def is_fence(self, line: str) -> bool:
        fence = fence_of(line)
        if fence is None:
            return False
        char, length, rest = fence
        if self.opening is None:
            if char == "`" and "`" in rest:
                return False  # an inline code span, not a fence
            self.opening = (char, length)
            return True
        if char == self.opening[0] and length >= self.opening[1] and not rest.strip():
            self.opening = None
            return True
        return False


def split_sections(
    lines: list[str], use_fences: bool
) -> tuple[dict[str, list[str]], bool]:
    result: dict[str, list[str]] = {"": []}
    current = ""
    tracker = FenceTracker()
    for line in lines:
        if use_fences and tracker.is_fence(line):
            pass
        elif tracker.opening is None and line.startswith("## "):
            current = line[3:].strip().lower()
            result.setdefault(current, [])
            continue
        result[current].append(line)
    return result, tracker.opening is None


def sections(text: str) -> dict[str, list[str]]:
    """Map each `## ` heading (lower-case) to its lines; `""` holds the header.

    `## ` lines inside fenced blocks are content. When a fence is never closed, fences
    are ignored altogether, so an odd fence line cannot swallow the headings after it.
    """
    lines = text.splitlines()
    result, closed = split_sections(lines, use_fences=True)
    return result if closed else split_sections(lines, use_fences=False)[0]


def header_fields(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        if line.startswith("- "):
            key, sep, value = line[2:].partition(":")
            if sep:
                fields.setdefault(key.strip(), value.strip())
    return fields


def token_value(value: str, what: str) -> int | None:
    if value == "unknown":
        return None
    if re.fullmatch(r"\d+", value):
        return int(value)
    raise Refusal(f"{what} must be a non-negative integer or 'unknown', got {value!r}")


def usd_value(value: str, what: str) -> float | None:
    if value == "unknown":
        return None
    if re.fullmatch(r"\d+(\.\d+)?([eE][+-]?\d+)?", value):
        number = float(value)
        if math.isfinite(number):
            return number
    raise Refusal(f"{what} must be a non-negative number or 'unknown', got {value!r}")


def plan_counts(plan: str) -> tuple[int, int, int]:
    parts = sections(plan)
    steps = sum(1 for line in parts.get("steps", []) if re.match(r"\d+\.\s", line))
    criteria = {
        m.group(1)
        for line in parts.get("acceptance criteria", [])
        if (m := re.match(r"[\s\-*]*(AC\d+)", line))
    }
    commands = 0
    tracker = FenceTracker()
    seen_block = False
    for line in parts.get("verification commands", []):
        if tracker.is_fence(line):
            if tracker.opening is None:
                break
            seen_block = True
            continue
        if seen_block and line.strip() and not line.lstrip().startswith("#"):
            commands += 1
    return steps, len(criteria), commands


def estimates(
    steps: int, criteria: int, commands: int, external: bool = False
) -> tuple[int, int]:
    check = CHECK_BASE + CHECK_PER_COMMAND * commands
    implement = 0 if external else IMPLEMENT_BASE + IMPLEMENT_PER_STEP * steps
    review = REVIEW_BASE + REVIEW_PER_CRITERION * criteria
    one_round = implement + check + review
    return check + one_round + FINISH, check + 3 * one_round + FINISH


def load_run(run_dir_arg: str, for_next: bool, external: bool = False) -> dict:
    run_dir = Path(run_dir_arg).resolve()
    if not run_dir.is_dir():
        raise Refusal(f"{run_dir_arg} is not a directory")
    if run_dir.parent.name != "_autopilot" or run_dir.parent.parent.name != ".work":
        raise Refusal(f"{run_dir_arg} is not inside .work/_autopilot/")
    work = run_dir.parent.parent
    parts = sections(read_text(run_dir / "run.md"))
    fields = header_fields(parts[""])
    for key in ("mode", "budget", "planning-tokens", "continue-past", "bootstrap"):
        if key not in fields:
            raise Refusal(f"run.md header is missing '{key}'")
    mode = fields["mode"]
    if mode not in ("semi", "full"):
        raise Refusal(f"mode must be semi or full, got {mode!r}")
    budget: int | None = None
    if fields["budget"] != "none":
        if not re.fullmatch(r"[1-9]\d*", fields["budget"]):
            raise Refusal(
                f"budget must be a positive integer or none, got {fields['budget']!r}"
            )
        budget = int(fields["budget"])
    planning = token_value(fields["planning-tokens"], "planning-tokens")
    cont = fields["continue-past"]
    continue_past = (
        set() if cont == "none" else {c.strip() for c in cont.split(",") if c.strip()}
    )
    bootstrap = fields["bootstrap"]
    source = fields.get("source", "backlog")
    if source not in ("backlog", "tasks"):
        raise Refusal(f"source must be backlog or tasks, got {source!r}")

    tasks: list[dict] = []
    width = 4
    for line in parts.get("tasks", []):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == "#":
            width = len(cells)
            if width == 5 and cells[4] != "External USD":
                raise Refusal(
                    f"the 5th header cell must be 'External USD', got {cells[4]!r}"
                )
            if width not in (4, 5):
                raise Refusal(f"the header row must have 4 or 5 cells, got {width}")
            continue
        if cells and set(cells[0]) <= set("-: "):
            continue
        if len(cells) != width:
            raise Refusal(f"malformed task row: {line!r}")
        task_id, deps_text, tokens_text = cells[1], cells[2], cells[3]
        if any(t["id"] == task_id for t in tasks):
            raise Refusal(f"task id repeats: {task_id}")
        deps = [] if deps_text == "-" else [d.strip() for d in deps_text.split(",")]
        earlier = {t["id"] for t in tasks}
        for dep in deps:
            if dep not in earlier:
                raise Refusal(
                    f"{task_id} depends on {dep!r}, which is not an earlier row"
                )
        used = token_value(tokens_text, f"Tokens used of {task_id}")
        usd = usd_value(cells[4], f"External USD of {task_id}") if width == 5 else 0.0
        task_dir = work / task_id
        status = task_dir / "status.md"
        plan = task_dir / "plan.md"
        if not status.is_file():
            raise Refusal(f"{task_id} has no status.md")
        if not plan.is_file():
            raise Refusal(f"{task_id} has no plan.md")
        phase = header_fields(sections(read_text(status))[""]).get("phase", "")
        if phase not in FINISHED_OK | FAILED | IN_PROGRESS | PENDING | UNAPPROVED:
            raise Refusal(f"{task_id} has unrecognised phase {phase!r}")
        if for_next and phase in UNAPPROVED:
            raise Refusal(f"{task_id} is not approved (phase {phase})")
        counts = plan_counts(read_text(plan))
        one_round, maximum = estimates(*counts, external=external)
        tasks.append(
            {
                "id": task_id,
                "deps": deps,
                "used": used,
                "usd": usd,
                "phase": phase,
                "counts": counts,
                "one_round": one_round,
                "max": maximum,
            }
        )
    if bootstrap != "none":
        if not tasks or tasks[0]["id"] != bootstrap:
            raise Refusal(f"bootstrap task {bootstrap!r} is not row 1")
        for task in tasks[1:]:
            if bootstrap not in task["deps"]:
                raise Refusal(
                    f"{task['id']} does not depend on bootstrap task {bootstrap}"
                )
    if source == "tasks":
        if bootstrap != "none":
            raise Refusal("a tasks run must have bootstrap: none")
        if tasks and tasks[0]["deps"]:
            raise Refusal(
                f"row 1 of a tasks run must depend on '-', got {tasks[0]['id']}"
            )
        for previous, task in itertools.pairwise(tasks):
            if task["deps"] != [previous["id"]]:
                raise Refusal(
                    f"{task['id']} of a tasks run must depend on exactly "
                    f"{previous['id']}, the previous row"
                )
    return {
        "mode": mode,
        "budget": budget,
        "planning": planning,
        "continue_past": continue_past,
        "tasks": tasks,
        "external": external,
        "has_usd": width == 5,
        "source": source,
        "base": fields.get("base"),
    }


# --- stack and base ------------------------------------------------------------------


def plain_folder_name(task_id: str) -> bool:
    """True when task_id can name a `.work/<id>/` folder (no path, not dot/underscore)."""
    if task_id in (".", "..") or task_id.startswith(("_", ".")):
        return False
    return "/" not in task_id and "\\" not in task_id


def status_phase(work: Path, task_id: str) -> str:
    status = work / task_id / "status.md"
    if not status.is_file():
        raise Refusal(f"unknown task {task_id}: no .work/{task_id}/status.md")
    return header_fields(sections(read_text(status))[""]).get("phase", "")


def stack(root: str, ids_arg: str, external: bool) -> str:
    """Validate the comma-separated task list and print the `## Tasks` table for run.md."""
    ids = [part.strip() for part in ids_arg.split(",")]
    if not ids_arg.strip():
        raise Refusal("the task list is empty")
    if any(not task_id for task_id in ids):
        raise Refusal("the task list has an empty element")
    for task_id in ids:
        if not plain_folder_name(task_id):
            raise Refusal(f"{task_id} is not a plain folder name")
    seen: set[str] = set()
    for task_id in ids:
        if task_id in seen:
            raise Refusal(f"task id repeats: {task_id}")
        seen.add(task_id)
    require_toplevel(root)
    work = Path(root) / ".work"
    for task_id in ids:
        phase = status_phase(work, task_id)
        if phase == "done":
            raise Refusal(f"{task_id} is already finished (phase done)")
        if phase in FAILED:
            raise Refusal(
                f"{task_id} failed (phase {phase}); "
                "resolve it with /task-flow:task-run first"
            )
        if phase in IN_PROGRESS:
            raise Refusal(f"{task_id} has already started (phase {phase})")
        if phase not in UNAPPROVED | PENDING:
            raise Refusal(f"{task_id} has unrecognised phase {phase!r}")
        plan = work / task_id / "plan.md"
        if phase in ("planned", "approved") and not plan.is_file():
            raise Refusal(f"{task_id} is {phase} but has no plan.md")
        if (work / task_id / "worktree").exists():
            raise Refusal(f"{task_id} already has a worktree")
        branch = git(
            root, "rev-parse", "--verify", "--quiet", f"refs/heads/task/{task_id}"
        )
        if branch.returncode == 0:
            raise Refusal(f"{task_id} already has branch task/{task_id}")
    header = "| # | Task | Depends on | Tokens used |"
    separator = "| --- | --- | --- | --- |"
    if external:
        header += " External USD |"
        separator += " --- |"
    rows = [header, separator]
    for number, task_id in enumerate(ids, 1):
        depends = "-" if number == 1 else ids[number - 2]
        row = f"| {number} | {task_id} | {depends} | 0 |"
        if external:
            row += " 0 |"
        rows.append(row)
    return "\n".join(rows)


def base_line(run_dir_arg: str, task_id: str) -> str:
    """`task/<id>` of the last done row, or the run's base when nothing is done."""
    run = load_run(run_dir_arg, for_next=False)
    task = next((t for t in run["tasks"] if t["id"] == task_id), None)
    if task is None:
        raise Refusal(f"{task_id} is not a task of this run")
    if task["phase"] not in UNAPPROVED | PENDING:
        raise Refusal(f"{task_id} has already started (phase {task['phase']})")
    work = Path(run_dir_arg).resolve().parent.parent
    if (work / task_id / "worktree").exists():
        raise Refusal(f"{task_id} already has a worktree")
    if run["base"] is None:
        raise Refusal("run.md has no base: line")
    last_done = None
    for row in run["tasks"]:
        if row["phase"] in FINISHED_OK:
            last_done = row["id"]
    return f"task/{last_done}" if last_done is not None else run["base"]


def spent(run: dict) -> tuple[int, bool]:
    total = run["planning"] or 0
    unknown = run["planning"] is None
    for task in run["tasks"]:
        if task["used"] is None:
            unknown = True
        else:
            total += task["used"]
    return total, unknown


def external_spent(run: dict) -> tuple[float, bool]:
    """Sum of the known `External USD` values, and whether any row is unknown."""
    total = 0.0
    unknown = False
    for task in run["tasks"]:
        if task["usd"] is None:
            unknown = True
        else:
            total += task["usd"]
    return total, unknown


# --- estimate ------------------------------------------------------------------------


def skipped_ids(tasks: list[dict]) -> set[str]:
    """Pending tasks that depend, directly or not, on a failed or skipped task."""
    bad = {t["id"] for t in tasks if t["phase"] in FAILED}
    skipped: set[str] = set()
    for task in tasks:
        if task["phase"] in PENDING | UNAPPROVED and any(
            d in bad for d in task["deps"]
        ):
            bad.add(task["id"])
            skipped.add(task["id"])
    return skipped


def estimate(run: dict) -> str:
    rows = [
        "| # | task | steps | criteria | commands | 1 round | 3 rounds |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for number, task in enumerate(run["tasks"], 1):
        steps, criteria, commands = task["counts"]
        rows.append(
            f"| {number} | {task['id']} | {steps} | {criteria} | {commands} "
            f"| {task['one_round']} | {task['max']} |"
        )
    total_one = sum(t["one_round"] for t in run["tasks"])
    total_max = sum(t["max"] for t in run["tasks"])
    rows.append(f"| total | | | | | {total_one} | {total_max} |")
    total, unknown = spent(run)
    rows.append(f"spent: {'at least ' if unknown else ''}{total} tokens")
    budget = run["budget"]
    if budget is None:
        rows.append(
            "budget: none set; compare the totals with your plan's usage limit (/usage)"
        )
    else:
        skipped = skipped_ids(run["tasks"])
        open_tasks = [
            t
            for t in run["tasks"]
            if t["phase"] not in FINISHED_OK | FAILED and t["id"] not in skipped
        ]
        fits3 = total + sum(t["max"] for t in open_tasks) <= budget
        fits1 = total + sum(t["one_round"] for t in open_tasks) <= budget
        word = {True: "fits", False: "does not fit"}
        rows.append(
            f"budget: {budget} tokens; pending at 3 rounds each: {word[fits3]}; "
            f"pending at 1 round each: {word[fits1]}"
        )
    if run["external"]:
        rows.append(
            "implementer: external profile; IMPLEMENT counts 0 Claude tokens; "
            "its cost is measured in USD per round (impl-<n>.json), not estimated"
        )
    if run["has_usd"]:
        usd_total, usd_unknown = external_spent(run)
        rows.append(
            f"external spent: {'at least ' if usd_unknown else ''}{usd_total:.2f} USD"
        )
    if run["source"] == "tasks" and len(run["tasks"]) > 10:
        count = len(run["tasks"])
        rows.append(
            f"warning: {count} tasks; more than the 10 a decomposed backlog allows — "
            "check the totals against your usage limit (/usage)"
        )
    return "\n".join(rows)


# --- next ----------------------------------------------------------------------------


def next_step(run: dict) -> str:
    tasks = run["tasks"]
    for task in tasks:
        if task["phase"] in IN_PROGRESS:
            return f"run {task['id']}"

    finished = [t for t in tasks if t["phase"] in FINISHED_OK | FAILED]
    if len(finished) >= 2 and all(t["phase"] in FAILED for t in finished[-2:]):
        a, b = finished[-2]["id"], finished[-1]["id"]
        return f"stop: wrong direction: {a}, {b} failed in a row"

    if run["mode"] == "semi":
        for task in tasks:
            if task["phase"] in FAILED and task["id"] not in run["continue_past"]:
                return f"wait {task['id']}: {task['phase']}"

    bad = {t["id"] for t in tasks if t["phase"] in FAILED}
    skipped: list[str] = []
    runnable: list[dict] = []
    for task in tasks:
        if task["phase"] not in PENDING:
            continue
        if any(dep in bad for dep in task["deps"]):
            bad.add(task["id"])
            skipped.append(task["id"])
        else:
            runnable.append(task)
    if not runnable:
        if skipped:
            failed = [t["id"] for t in tasks if t["phase"] in FAILED]
            return (
                f"stop: cannot finish: {', '.join(skipped)} "
                f"depend on failed {', '.join(failed)}"
            )
        return "done"

    if finished:
        last = finished[-1]
        if last["used"] is not None and last["used"] > BURN_FACTOR * last["max"]:
            return (
                f"stop: burn rate: {last['id']} used {last['used']}, "
                f"more than {BURN_FACTOR} x its estimate {last['max']}"
            )

    candidate = runnable[0]
    total, _ = spent(run)
    budget = run["budget"]
    if budget is not None and total + candidate["max"] > budget:
        return (
            f"stop: budget: spent {total} + next worst case {candidate['max']} "
            f"> budget {budget}"
        )
    return f"run {candidate['id']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect").add_argument("path")
    stack_parser = sub.add_parser("stack")
    stack_parser.add_argument("path")
    stack_parser.add_argument("ids")
    stack_parser.add_argument(
        "--implementer-kind", choices=("claude", "profile"), default="claude"
    )
    base_parser = sub.add_parser("base")
    base_parser.add_argument("path")
    base_parser.add_argument("task_id")
    estimate_parser = sub.add_parser("estimate")
    estimate_parser.add_argument("path")
    estimate_parser.add_argument(
        "--implementer-kind", choices=("claude", "profile"), default="claude"
    )
    next_parser = sub.add_parser("next")
    next_parser.add_argument("path")
    next_parser.add_argument(
        "--implementer-kind", choices=("claude", "profile"), default="claude"
    )
    args = parser.parse_args()

    try:
        if args.command == "inspect":
            output = inspect(args.path)
        elif args.command == "stack":
            output = stack(
                args.path, args.ids, external=args.implementer_kind == "profile"
            )
        elif args.command == "base":
            output = base_line(args.path, args.task_id)
        else:
            run = load_run(
                args.path,
                for_next=args.command == "next",
                external=args.implementer_kind == "profile",
            )
            output = estimate(run) if args.command == "estimate" else next_step(run)
    except Refusal as error:
        print(f"autopilot.py: {error}", file=sys.stderr)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
