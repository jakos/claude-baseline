# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/task_list.py: runs it against fixture .work/ directories.

Run: uv run --script plugins/task-flow/tests/test_task_list.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "task_list.py"


def status(phase="new", rnd="0", verdict="none", history=None, log=""):
    lines = [
        "# Status: x",
        f"- phase: {phase}",
        f"- round: {rnd}",
        f"- last-verdict: {verdict}",
        "## History",
    ]
    lines += history if history is not None else []
    lines.append("## Log")
    if log:
        lines.append(log)
    return "\n".join(lines) + "\n"


def hist(ts, text="did a thing"):
    return f"- {ts} new — {text}"


class TaskListTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def add(self, task_id, content):
        directory = self.root / ".work" / task_id
        directory.mkdir(parents=True)
        (directory / "status.md").write_text(content, encoding="utf-8")

    def run_script(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args],
            capture_output=True,
            check=False,
            env=env,
        )

    def out(self, *args):
        result = self.run_script(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode("utf-8")

    def ids(self, *args):
        rows = [
            line
            for line in self.out(*args).splitlines()
            if line.startswith("| ") and not line.startswith("| id ")
        ]
        return [row.split("|")[1].strip() for row in rows if "---" not in row]

    def test_hides_done_by_default(self):
        self.add("a", status("done"))
        self.add("b", status("new"))
        self.assertEqual(self.ids(), ["b"])

    def test_all_includes_done(self):
        self.add("a", status("done"))
        self.add("b", status("new"))
        self.assertEqual(self.ids("--all"), ["b", "a"])

    def test_reports_hidden_done_count(self):
        self.add("a", status("done"))
        self.add("c", status("done"))
        self.add("b", status("new"))
        self.assertIn("2 done task(s) hidden; --all lists them.", self.out())
        self.assertNotIn("hidden", self.out("--all"))

    def test_orders_by_phase_group(self):
        for name, phase in [
            ("t-done", "done"),
            ("t-new", "new"),
            ("t-impl", "implementing"),
            ("t-plan", "planned"),
            ("t-esc", "escalated"),
            ("t-appr", "review-approved"),
        ]:
            self.add(name, status(phase, history=[hist("2026-01-01T00:00:00Z")]))
        self.assertEqual(
            self.ids("--all"),
            ["t-esc", "t-plan", "t-appr", "t-impl", "t-new", "t-done"],
        )

    def test_ties_broken_by_oldest_history_first(self):
        self.add("young", status("planned", history=[hist("2026-03-01T00:00:00Z")]))
        self.add("old", status("planned", history=[hist("2026-01-01T00:00:00Z")]))
        self.add("naive", status("planned", history=[hist("2026-02-01T00:00:00")]))
        self.assertEqual(self.ids(), ["old", "naive", "young"])

    def test_ties_broken_by_id_last(self):
        stamp = [hist("2026-01-01T00:00:00Z")]
        self.add("b", status("planned", history=stamp))
        self.add("a", status("planned", history=stamp))
        self.assertEqual(self.ids(), ["a", "b"])

    def test_last_history_line_ignores_log_section(self):
        self.add(
            "a",
            status(
                history=[
                    hist("2026-01-01T00:00:00Z", "first"),
                    hist("2026-01-02T00:00:00Z", "second"),
                ],
                log="- round 1 implementer: LOGLINE",
            ),
        )
        output = self.out()
        self.assertIn("second", output)
        self.assertNotIn("first", output)
        self.assertNotIn("LOGLINE", output)

    def test_missing_or_bad_timestamp_sorts_last_in_group(self):
        self.add("a-none", status("planned"))
        self.add("b-bad", status("planned", history=["- notatime planned — x"]))
        self.add("c-dated", status("planned", history=[hist("2026-01-01T00:00:00Z")]))
        self.assertEqual(self.ids(), ["c-dated", "a-none", "b-bad"])

    def test_unknown_or_missing_phase_sorts_before_done(self):
        stamp = [hist("2026-01-01T00:00:00Z")]
        self.add("a-done", status("done", history=stamp))
        self.add("b-weird", status("weird", history=stamp))
        self.add("c-nophase", "# Status: x\n## History\n")
        self.add("d-new", status("new", history=stamp))
        self.assertEqual(self.ids("--all"), ["d-new", "b-weird", "c-nophase", "a-done"])

    def test_undecodable_status_is_listed_as_unrecognised(self):
        stamp = [hist("2026-01-01T00:00:00Z")]
        self.add("a-new", status("new", history=stamp))
        directory = self.root / ".work" / "b-broken"
        directory.mkdir(parents=True)
        (directory / "status.md").write_bytes(b"- phase: \xff\xfe\n")
        self.assertEqual(self.ids(), ["a-new", "b-broken"])
        self.assertIn("| b-broken | ? | ? | ? | ? |", self.out())

    def test_no_work_dir_prints_message(self):
        self.assertIn("No open tasks under .work/.", self.out())
        self.assertIn("No tasks under .work/.", self.out("--all"))

    def test_escapes_pipe_in_history(self):
        self.add("a", status(history=[hist("2026-01-01T00:00:00Z", "a | b")]))
        self.assertIn("a \\| b", self.out())

    def test_utf8_em_dash_survives_pipe(self):
        self.add("a", status(history=[hist("2026-01-01T00:00:00Z")]))
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("PYTHONIOENCODING", "PYTHONUTF8")
        }
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("—", result.stdout.decode("utf-8"))

    def test_unknown_argument_exits_2(self):
        self.assertEqual(self.run_script("--bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
