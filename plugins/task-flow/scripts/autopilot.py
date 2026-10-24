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
- `estimate <run-dir>`: a Markdown table of coarse token estimates per task plus the
  spent and budget lines. Per task (constants below are coarse defaults, not measured):
  CHECK = 15000 + 3000 * commands, IMPLEMENT = 30000 + 12000 * steps,
  REVIEW = 30000 + 6000 * criteria, FINISH = 10000, ROUND = IMPLEMENT + CHECK + REVIEW,
  one round = CHECK + ROUND + FINISH, max = CHECK + 3 * ROUND + FINISH. Tasks in phase
  `new` or `planned` count as pending, because the overview runs before the start gate,
  when every task is still `planned`. Pending sums leave out tasks `next` would skip
  (dependents, direct or transitive, of a failed task).
- `next <run-dir>`: one line, `run <id>`, `wait <id>: <phase>`, `stop: <reason>` or `done`.
  The circuit breaker. It is evaluated between tasks only: a task already in progress
  always gives `run <id>`. Triggers, first match wins: a task in progress; wrong direction
  (the last two finished tasks both failed); semi mode and a failed task not in
  continue-past; nothing runnable left (cannot finish, or done); burn rate (the last
  finished task used more than 2 x its max estimate); budget (spent + the next task's max
  estimate exceeds the budget). All are counts and sums read from files.

`<run-dir>` is `.work/_autopilot/<run-id>/` with a `run.md` written by the orchestrator;
this script only reads. Stdlib only, started by `uv run --script`.
"""

import argparse
import fnmatch
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


def inspect(root: str) -> str:
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


def estimates(steps: int, criteria: int, commands: int) -> tuple[int, int]:
    check = CHECK_BASE + CHECK_PER_COMMAND * commands
    implement = IMPLEMENT_BASE + IMPLEMENT_PER_STEP * steps
    review = REVIEW_BASE + REVIEW_PER_CRITERION * criteria
    one_round = implement + check + review
    return check + one_round + FINISH, check + 3 * one_round + FINISH


def load_run(run_dir_arg: str, for_next: bool) -> dict:
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

    tasks: list[dict] = []
    for line in parts.get("tasks", []):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and (cells[0] == "#" or set(cells[0]) <= set("-: ")):
            continue
        if len(cells) != 4:
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
        one_round, maximum = estimates(*counts)
        tasks.append(
            {
                "id": task_id,
                "deps": deps,
                "used": used,
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
    return {
        "mode": mode,
        "budget": budget,
        "planning": planning,
        "continue_past": continue_past,
        "tasks": tasks,
    }


def spent(run: dict) -> tuple[int, bool]:
    total = run["planning"] or 0
    unknown = run["planning"] is None
    for task in run["tasks"]:
        if task["used"] is None:
            unknown = True
        else:
            total += task["used"]
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
    sub.add_parser("estimate").add_argument("path")
    sub.add_parser("next").add_argument("path")
    args = parser.parse_args()

    try:
        if args.command == "inspect":
            output = inspect(args.path)
        else:
            run = load_run(args.path, for_next=args.command == "next")
            output = estimate(run) if args.command == "estimate" else next_step(run)
    except Refusal as error:
        print(f"autopilot.py: {error}", file=sys.stderr)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
