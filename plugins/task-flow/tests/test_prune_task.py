# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/prune_task.py: runs it against temp .work/<id>/ fixtures.

Run: uv run --script plugins/task-flow/tests/test_prune_task.py
"""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "prune_task.py"
MD_FILES = {
    "task.md": "# Task\n",
    "plan.md": "# Plan\n",
    "summary.md": "# Summary\n",
    "checks-0.md": "# Checks 0\n",
    "checks-1.md": "# Checks 1\n",
    "review-1.md": "# Review 1\n",
    "notes.MD": "# Notes\n",
}
STATUS_DONE = "# Status: x\n- phase: done\n- round: 1\n## History\n## Log\n"


class PruneTaskTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.task = self.root / ".work" / "t1"
        self.task.mkdir(parents=True)

    def populate(self, phase_text=STATUS_DONE):
        for name, text in MD_FILES.items():
            (self.task / name).write_text(text, encoding="utf-8")
        (self.task / "status.md").write_text(phase_text, encoding="utf-8")

    def scratch(self):
        (self.task / "base" / "fx").mkdir(parents=True)
        (self.task / "base" / "edge.py").write_text("x = 1\n", encoding="utf-8")
        (self.task / ".ruff_cache").mkdir()
        (self.task / ".ruff_cache" / "x").write_text("c", encoding="utf-8")
        (self.task / "scratch.py").write_text("pass\n", encoding="utf-8")
        (self.task / "out.txt").write_text("o", encoding="utf-8")
        (self.task / "tmp.json").write_text("{}", encoding="utf-8")
        (self.task / "dir.md").mkdir()
        (self.task / "dir.md" / "inner.txt").write_text("i", encoding="utf-8")

    def run_script(self, *args, target=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args, str(target or self.task)],
            capture_output=True,
            check=False,
        )

    def names(self):
        return sorted(p.name for p in self.task.iterdir())

    def test_keeps_all_markdown_files(self):
        self.populate()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names(), sorted([*MD_FILES, "status.md"]))
        for name, text in MD_FILES.items():
            self.assertEqual((self.task / name).read_text(encoding="utf-8"), text)
        self.assertEqual(
            (self.task / "status.md").read_text(encoding="utf-8"), STATUS_DONE
        )

    def test_removes_non_markdown_and_dirs(self):
        self.populate()
        self.scratch()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names(), sorted([*MD_FILES, "status.md"]))
        lines = result.stdout.decode("utf-8").splitlines()
        removed = [
            ".ruff_cache",
            "base",
            "dir.md",
            "out.txt",
            "scratch.py",
            "tmp.json",
        ]
        self.assertEqual(lines[:-1], [f"removed: {n}" for n in removed])
        self.assertTrue(lines[-1].startswith("kept: "))
        self.assertIn("plan.md", lines[-1])

    def test_removes_markdown_inside_scratch_dirs(self):
        self.populate()
        (self.task / "base" / "sub").mkdir(parents=True)
        (self.task / "base" / "notes.md").write_text("n", encoding="utf-8")
        (self.task / "base" / "sub" / "readme.md").write_text("r", encoding="utf-8")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.task / "base").exists())

    def test_never_touches_worktree(self):
        self.populate()
        self.scratch()
        wt = self.task / "worktree"
        (wt / "src" / "deep").mkdir(parents=True)
        files = {
            ".git": "gitdir: elsewhere\n",
            "a.py": "a\n",
            "README.md": "r\n",
            "src/deep/b.txt": "b\n",
        }
        for rel, text in files.items():
            (wt / rel).write_text(text, encoding="utf-8")
        before = sorted(str(p.relative_to(wt)) for p in wt.rglob("*"))
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        after = sorted(str(p.relative_to(wt)) for p in wt.rglob("*"))
        self.assertEqual(before, after)
        for rel, text in files.items():
            self.assertEqual((wt / rel).read_text(encoding="utf-8"), text)
        kept = result.stdout.decode("utf-8").splitlines()[-1]
        self.assertIn("worktree", kept)

    def test_refuses_when_not_done(self):
        self.populate(STATUS_DONE.replace("done", "implementing"))
        self.scratch()
        before = self.names()
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr)
        self.assertEqual(self.names(), before)

    def test_refuses_outside_work_dir(self):
        other = self.root / "elsewhere" / "t1"
        other.mkdir(parents=True)
        (other / "status.md").write_text(STATUS_DONE, encoding="utf-8")
        (other / "junk.txt").write_text("j", encoding="utf-8")
        result = self.run_script(target=other)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr)
        self.assertTrue((other / "junk.txt").exists())

    def test_refuses_missing_dir(self):
        result = self.run_script(target=self.root / ".work" / "nope")
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr)

    def test_refuses_missing_status(self):
        (self.task / "junk.txt").write_text("j", encoding="utf-8")
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"refusing to prune", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertTrue((self.task / "junk.txt").exists())

    def test_refuses_undecodable_status(self):
        (self.task / "status.md").write_bytes(b"\xff\xfe- phase: done\n")
        (self.task / "junk.txt").write_text("j", encoding="utf-8")
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"refusing to prune", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertTrue((self.task / "junk.txt").exists())

    def test_refuses_status_without_phase(self):
        (self.task / "status.md").write_text(
            "# Status: x\n- round: 1\n", encoding="utf-8"
        )
        (self.task / "junk.txt").write_text("j", encoding="utf-8")
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"refusing to prune", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertTrue((self.task / "junk.txt").exists())

    def test_dry_run_deletes_nothing(self):
        self.populate()
        self.scratch()
        before = self.names()
        result = self.run_script("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names(), before)
        lines = result.stdout.decode("utf-8").splitlines()
        self.assertIn("would remove: base", lines)
        self.assertIn("would remove: dir.md", lines)
        self.assertNotIn("removed: base", lines)

    def test_second_run_is_noop(self):
        self.populate()
        self.scratch()
        self.assertEqual(self.run_script().returncode, 0)
        after_first = self.names()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names(), after_first)
        lines = result.stdout.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("kept: "))

    def test_removes_read_only_files(self):
        self.populate()
        inner = self.task / "base" / "objects"
        inner.mkdir(parents=True)
        ro_inner = inner / "pack.bin"
        ro_inner.write_text("x", encoding="utf-8")
        ro_top = self.task / "locked.txt"
        ro_top.write_text("y", encoding="utf-8")
        os.chmod(ro_inner, stat.S_IREAD)
        os.chmod(ro_top, stat.S_IREAD)
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.task / "base").exists())
        self.assertFalse(ro_top.exists())

    def test_symlink_not_followed(self):
        self.populate()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("k", encoding="utf-8")
        outside_file = self.root / "outside.md"
        outside_file.write_text("o", encoding="utf-8")
        try:
            os.symlink(outside, self.task / "linkdir", target_is_directory=True)
            os.symlink(outside_file, self.task / "link.md")
        except OSError as exc:
            self.skipTest(f"cannot create symlinks: {exc}")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.lexists(self.task / "linkdir"))
        self.assertFalse(os.path.lexists(self.task / "link.md"))
        self.assertEqual((outside / "keep.txt").read_text(encoding="utf-8"), "k")
        self.assertEqual(outside_file.read_text(encoding="utf-8"), "o")

    @unittest.skipUnless(sys.platform == "win32", "junctions are Windows-only")
    def test_junction_removed_target_survives(self):
        import _winapi

        self.populate()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("k", encoding="utf-8")
        _winapi.CreateJunction(str(outside), str(self.task / "jdir"))
        _winapi.CreateJunction(str(outside), str(self.task / "j.md"))
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.lexists(self.task / "jdir"))
        self.assertFalse(os.path.lexists(self.task / "j.md"))
        lines = result.stdout.decode("utf-8").splitlines()
        self.assertIn("removed: jdir", lines)
        self.assertIn("removed: j.md", lines)
        self.assertEqual((outside / "keep.txt").read_text(encoding="utf-8"), "k")

    def test_unremovable_entry_is_reported_and_rest_pruned(self):
        self.populate()
        (self.task / "a.txt").write_text("a", encoding="utf-8")
        (self.task / "z.txt").write_text("z", encoding="utf-8")
        locked_dir = None
        if sys.platform == "win32":
            # an open handle (no delete sharing) makes the removal fail
            holder = open(self.task / "a.txt", encoding="utf-8")  # noqa: SIM115
            self.addCleanup(holder.close)
        else:
            if os.geteuid() == 0:
                self.skipTest("root ignores directory permissions")
            locked_dir = self.task / "locked"
            locked_dir.mkdir()
            (locked_dir / "f.txt").write_text("f", encoding="utf-8")
            os.chmod(locked_dir, stat.S_IREAD | stat.S_IEXEC)
            self.addCleanup(os.chmod, locked_dir, stat.S_IRWXU)
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        stderr = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", stderr)
        self.assertIn(
            "failed: " + ("a.txt" if locked_dir is None else "locked"), stderr
        )
        self.assertFalse((self.task / "z.txt").exists())
        self.assertTrue((self.task / "plan.md").exists())
        self.assertIn("kept: ", result.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
