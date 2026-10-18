# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""List task-flow tasks under .work/ as a Markdown table, sorted by what to do next.

Without arguments only tasks whose phase is not `done` are listed; `--all` lists every
task. Rows are sorted by (phase group, oldest last History timestamp first, task id):

- group 0, waiting on the user: planned, blocked, escalated
- group 1, in progress: approved, baseline, implementing, checking, reviewing,
  review-approved
- group 2, not started: new
- group 3, unrecognised: missing or unknown phase
- group 4, finished: done

A missing or unparsable History timestamp sorts after every dated row of its group.
Stdlib only, started by `uv run --script`, so it runs on macOS, Linux and Windows.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

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


def load_task(path: Path) -> dict:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="include done tasks")
    parser.add_argument(
        "--root", default=".", help="directory that contains .work/ (default: .)"
    )
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    work = Path(args.root) / ".work"
    paths = sorted(work.glob("*/status.md")) if work.is_dir() else []
    tasks = [load_task(p) for p in paths]
    hidden = 0
    if not args.all:
        hidden = sum(1 for t in tasks if t["phase"] == "done")
        tasks = [t for t in tasks if t["phase"] != "done"]
    tasks.sort(key=lambda t: (t["group"], not t["dated"], t["time"], t["id"]))

    if tasks:
        print("| id | phase | round | last verdict | last History |")
        print("| --- | --- | --- | --- | --- |")
        for t in tasks:
            cells = (t["id"], t["phase"], t["round"], t["verdict"], t["history"])
            print("| " + " | ".join(cell(c) for c in cells) + " |")
    else:
        print("No tasks under .work/." if args.all else "No open tasks under .work/.")
    if hidden:
        print(f"{hidden} done task(s) hidden; --all lists them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
