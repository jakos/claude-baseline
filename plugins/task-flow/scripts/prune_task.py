# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Prune a finished task folder .work/<id>/ down to its Markdown history.

Usage: prune_task.py [--dry-run] <task-dir>

Keep rule, top-level entries of <task-dir> only:

- kept: a regular file (not a symlink or junction) whose name ends in `.md`
  (case-insensitive), and the entry named exactly `worktree`
- deleted: everything else, including directories (and any `.md` inside them), non-`.md`
  files, symlinks and junctions of any name (unlinked, never followed), and a directory
  whose name ends in `.md`

`worktree/` is never touched. An entry that cannot be removed is reported on stderr as
`failed: <name>: <error>`, the rest is still pruned, and the exit code is 1. Refuses (exit 2, nothing deleted) unless <task-dir> exists,
its parent directory is named `.work`, and status.md has `- phase: done`.
Stdlib only, started by `uv run --script`, so it runs on macOS, Linux and Windows.
"""

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

KEEP_DIR = "worktree"


def is_kept(entry: Path) -> bool:
    if entry.name == KEEP_DIR:
        return True
    if entry.is_symlink() or entry.is_junction():
        return False
    return entry.is_file() and entry.name.lower().endswith(".md")


def refusal(task_dir: Path) -> str | None:
    if not task_dir.is_dir():
        return f"not a directory: {task_dir}"
    if task_dir.resolve().parent.name != ".work":
        return f"not inside a .work directory: {task_dir}"
    try:
        text = (task_dir / "status.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"cannot read status.md: {exc}"
    for line in text.splitlines():
        if line.startswith("- phase:"):
            phase = line.partition(":")[2].strip()
            if phase == "done":
                return None
            return f"phase is {phase!r}, not 'done'"
    return "status.md has no '- phase:' line"


def _retry_writable(func, path, exc) -> None:
    """rmtree error handler: clear the read-only bit and retry (Windows)."""
    if not os.path.islink(path):  # never change the mode of a link's target
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    func(path)


def remove(entry: Path) -> None:
    if entry.is_symlink() or entry.is_junction():
        try:
            entry.unlink()
        except OSError:
            entry.rmdir()  # directory junction on Windows
    elif entry.is_dir():
        shutil.rmtree(entry, onexc=_retry_writable)
    else:
        try:
            entry.unlink()
        except PermissionError:
            os.chmod(entry, stat.S_IWRITE | stat.S_IREAD)
            entry.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="delete nothing")
    parser.add_argument("task_dir", help="the .work/<id> directory to prune")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    task_dir = Path(args.task_dir)
    problem = refusal(task_dir)
    if problem:
        print(f"refusing to prune: {problem}", file=sys.stderr)
        return 2

    entries = sorted(task_dir.iterdir(), key=lambda p: p.name)
    kept = [e.name for e in entries if is_kept(e)]
    verb = "would remove" if args.dry_run else "removed"
    failed = 0
    for entry in entries:
        if is_kept(entry):
            continue
        if not args.dry_run:
            try:
                remove(entry)
            except OSError as exc:
                print(f"failed: {entry.name}: {exc}", file=sys.stderr)
                failed += 1
                continue
        print(f"{verb}: {entry.name}")
    print("kept: " + ", ".join(kept))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
