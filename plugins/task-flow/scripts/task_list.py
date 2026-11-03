# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""List task-flow tasks under .work/ as a Markdown table, sorted by what to do next.

Without arguments only tasks whose phase is not `done` are listed; `--all` lists every
task. `--epic <id>` lists only the member tasks of that epic (`.work/_epics/<id>/epic.md`);
an unknown epic id exits 2. Rows are sorted by (phase group, oldest last History
timestamp first, task id):

- group 0, waiting on the user: planned, blocked, escalated
- group 1, in progress: approved, baseline, implementing, checking, reviewing,
  review-approved
- group 2, not started: new
- group 3, unrecognised: missing or unknown phase
- group 4, finished: done

The Epic column shows the epic a task belongs to (`-` when none). A missing or
unparsable History timestamp sorts after every dated row of its group.
Stdlib only, started by `uv run --script`, so it runs on macOS, Linux and Windows.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import epics

GROUPS = {
    "planned": 0,
    "blocked": 0,
    "escalated": 0,
    "approved": 1,
    "baseline": 1,
    "implementing": 1,
    "checking": 1,
    "reviewing": 1,
    "review-approved": 1,
    "new": 2,
    "done": 4,
}
UNRECOGNISED = 3
FAR_FUTURE = datetime.max.replace(tzinfo=UTC)


def parse_status(text: str) -> dict:
    """Pull the header fields and the last History bullet out of a status.md."""
    fields: dict[str, str] = {}
    last_bullet: str | None = None
    section = "header"
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        if section == "header" and line.startswith("- "):
            key, sep, value = line[2:].partition(":")
            if sep:
                fields.setdefault(key.strip(), value.strip())
        elif section == "history" and line.startswith("- "):
            last_bullet = line[2:].strip()
    return {"fields": fields, "history": last_bullet}


def parse_time(bullet: str | None) -> datetime | None:
    if not bullet:
        return None
    words = bullet.split()
    token = words[0] if words else ""
    try:
        moment = datetime.fromisoformat(token)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def load_task(path: Path, epic_of: dict[str, str]) -> dict:
    task_id = path.parent.name
    try:
        parsed = parse_status(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        parsed = {"fields": {}, "history": None}
    fields = parsed["fields"]
    phase = fields.get("phase") or "?"
    moment = parse_time(parsed["history"])
    return {
        "id": task_id,
        "epic": epic_of.get(task_id, "-"),
        "phase": phase,
        "round": fields.get("round") or "?",
        "verdict": fields.get("last-verdict") or "?",
        "history": parsed["history"] or "?",
        "group": GROUPS.get(phase, UNRECOGNISED),
        "dated": moment is not None,
        "time": moment or FAR_FUTURE,
    }


def cell(value: str) -> str:
    return value.replace("|", "\\|")


def epic_members(root: Path) -> dict[str, str]:
    """{task id: epic id} over all parseable epics; an unreadable epic is skipped here.

    A task in several epics is reported by the same epic `epics.epic_for_task` picks:
    an open one wins over completed ones.
    """
    candidates: dict[str, list[epics.Epic]] = {}
    try:
        ids = epics.list_epic_ids(root)
    except epics.EpicError:
        return {}
    for epic_id in ids:
        try:
            epic = epics.load_epic(root, epic_id, check_tasks=False)
        except epics.EpicError:
            continue
        for row in epic.tasks:
            candidates.setdefault(row.task_id, []).append(epic)
    members: dict[str, str] = {}
    for task_id, found in candidates.items():
        chosen = epics.choose_epic(root, found)
        if chosen is not None:
            members[task_id] = chosen.epic_id
    return members


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="include done tasks")
    parser.add_argument(
        "--epic", default=None, help="list only this epic's member tasks"
    )
    parser.add_argument(
        "--root", default=".", help="directory that contains .work/ (default: .)"
    )
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    root = Path(args.root)
    if args.epic is not None:
        try:
            known = epics.epic_path(root, args.epic).is_file()
        except epics.EpicError as error:
            print(f"task_list.py: {error}", file=sys.stderr)
            return 2
        if not known:
            print(
                f"task_list.py: no epic {args.epic!r} under {root}/.work/_epics",
                file=sys.stderr,
            )
            return 2
    work = root / ".work"
    paths = sorted(work.glob("*/status.md")) if work.is_dir() else []
    members = epic_members(root)
    tasks = [load_task(p, members) for p in paths]
    if args.epic is not None:
        tasks = [t for t in tasks if t["epic"] == args.epic]
    hidden = 0
    if not args.all:
        hidden = sum(1 for t in tasks if t["phase"] == "done")
        tasks = [t for t in tasks if t["phase"] != "done"]
    tasks.sort(key=lambda t: (t["group"], not t["dated"], t["time"], t["id"]))

    if tasks:
        print("| id | epic | phase | round | last verdict | last History |")
        print("| --- | --- | --- | --- | --- | --- |")
        for t in tasks:
            cells = (
                t["id"],
                t["epic"],
                t["phase"],
                t["round"],
                t["verdict"],
                t["history"],
            )
            print("| " + " | ".join(cell(c) for c in cells) + " |")
    else:
        print("No tasks under .work/." if args.all else "No open tasks under .work/.")
    if hidden:
        print(f"{hidden} done task(s) hidden; --all lists them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
