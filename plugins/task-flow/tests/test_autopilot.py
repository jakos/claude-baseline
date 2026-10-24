# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/autopilot.py: runs it against fixture .work/ and git repos.

Run: uv run --script plugins/task-flow/tests/test_autopilot.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "autopilot.py"
HAS_GIT = shutil.which("git") is not None


def plan(steps=2, criteria=3, commands=2):
    lines = ["# Plan: x", "## Goal", "g", "## Steps"]
    lines += [f"{i}. step {i}" for i in range(1, steps + 1)]
    lines += ["## Acceptance criteria"]
    lines += [f"AC{i}. criterion\nVerify: x" for i in range(1, criteria + 1)]
    lines += ["## Verification commands", "```"]
    lines += [f"cmd {i}" for i in range(1, commands + 1)]
    lines += ["```", "## Project rules", "1. not a step", "AC9. not a criterion"]
    return "\n".join(lines) + "\n"


class RunFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.run_dir = self.root / ".work" / "_autopilot" / "r1"
        self.run_dir.mkdir(parents=True)

    def task(self, task_id, phase, plan_text=None):
        directory = self.root / ".work" / task_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "status.md").write_text(
            f"# Status: {task_id}\n- phase: {phase}\n## History\n", encoding="utf-8"
        )
        if plan_text is not None:
            (directory / "plan.md").write_text(plan_text, encoding="utf-8")

    def run_md(
        self,
        rows,
        mode="semi",
        budget="none",
        planning="0",
        cont="none",
        bootstrap="none",
        omit=(),
    ):
        """rows: (id, depends, tokens, phase) tuples; also creates the task folders."""
        header = {
            "mode": mode,
            "base": "main",
            "budget": budget,
            "planning-tokens": planning,
            "continue-past": cont,
            "bootstrap": bootstrap,
            "state": "running",
        }
        lines = ["# Autopilot: r1"]
        lines += [f"- {k}: {v}" for k, v in header.items() if k not in omit]
        lines += [
            "## Tasks",
            "| # | Task | Depends on | Tokens used |",
            "| --- | --- | --- | --- |",
        ]
        for number, (task_id, deps, tokens, phase) in enumerate(rows, 1):
            lines.append(f"| {number} | {task_id} | {deps} | {tokens} |")
            self.task(task_id, phase, plan())
        lines += ["## History", "- 2026-10-24T00:00:00Z running — start"]
        (self.run_dir / "run.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def call(self, command, target=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), command, str(target or self.run_dir)],
            capture_output=True,
            check=False,
        )

    def out(self, command):
        result = self.call(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode("utf-8").strip()


MAX = 400000  # 2 steps, 3 criteria, 2 commands
ONE = 154000


class EstimateTest(RunFixture):
    def test_estimate_formula(self):
        self.run_md([("a", "-", "0", "approved")])
        out = self.out("estimate")
        self.assertIn("| 1 | a | 2 | 3 | 2 | 154000 | 400000 |", out)
        self.assertTrue(out.startswith("| # | task | steps | criteria | commands |"))

    def test_estimate_totals_and_budget_fits(self):
        self.run_md(
            [("a", "-", "0", "approved"), ("b", "a", "0", "approved")],
            budget="2000000",
            planning="50000",
        )
        out = self.out("estimate").splitlines()
        self.assertEqual(out[-3], f"| total | | | | | {2 * ONE} | {2 * MAX} |")
        self.assertEqual(out[-2], "spent: 50000 tokens")
        self.assertEqual(
            out[-1],
            "budget: 2000000 tokens; pending at 3 rounds each: fits; "
            "pending at 1 round each: fits",
        )

    def test_estimate_budget_does_not_fit(self):
        self.run_md(
            [("a", "-", "0", "approved"), ("b", "a", "0", "approved")],
            budget="800000",
            planning="50000",
        )
        self.assertEqual(
            self.out("estimate").splitlines()[-1],
            "budget: 800000 tokens; pending at 3 rounds each: does not fit; "
            "pending at 1 round each: fits",
        )

    def test_estimate_pending_excludes_skipped_dependents(self):
        self.run_md(
            [
                ("a", "-", "1000", "escalated"),
                ("b", "a", "0", "approved"),
                ("c", "b", "0", "approved"),
                ("d", "-", "0", "approved"),
            ],
            budget="600000",
            planning="0",
        )
        # only d is still pending (b and c would be skipped): 1000 + 400000 fits
        self.assertEqual(
            self.out("estimate").splitlines()[-1],
            "budget: 600000 tokens; pending at 3 rounds each: fits; "
            "pending at 1 round each: fits",
        )

    def test_plan_counts_ignore_headings_in_fenced_blocks(self):
        self.run_md([("a", "-", "0", "approved")])
        fenced = plan().replace(
            "2. step 2",
            "2. step 2\n```markdown\n## Acceptance criteria\n## Verification commands\n```",
        )
        self.task("a", "approved", fenced)
        self.assertIn("| 1 | a | 2 | 3 | 2 | 154000 | 400000 |", self.out("estimate"))

    def test_fence_closes_only_on_matching_fence(self):
        self.run_md([("a", "-", "0", "approved")])
        fenced = plan().replace(
            "2. step 2",
            "2. step 2\n````markdown\n```\n## Acceptance criteria\n```\n## Verification commands\n````",
        )
        self.task("a", "approved", fenced)
        self.assertIn("| 1 | a | 2 | 3 | 2 | 154000 | 400000 |", self.out("estimate"))

    def test_unclosed_fence_does_not_zero_later_sections(self):
        self.run_md([("a", "-", "0", "approved")])
        self.task("a", "approved", plan().replace("2. step 2", "2. step 2\n```"))
        self.assertIn("| 1 | a | 2 | 3 | 2 | 154000 | 400000 |", self.out("estimate"))

    def test_tilde_and_indented_command_blocks_are_counted(self):
        self.run_md([("a", "-", "0", "approved")])
        for opening, closing in (("~~~", "~~~"), ("  ```", "  ```")):
            with self.subTest(opening=opening):
                text = (
                    plan()
                    .replace("```\ncmd", f"{opening}\ncmd")
                    .replace("cmd 2\n```", f"cmd 2\n{closing}")
                )
                self.task("a", "approved", text)
                self.assertIn(
                    "| 1 | a | 2 | 3 | 2 | 154000 | 400000 |", self.out("estimate")
                )

    def test_estimate_without_budget(self):
        self.run_md([("a", "-", "0", "approved")])
        self.assertEqual(
            self.out("estimate").splitlines()[-1],
            "budget: none set; compare the totals with your plan's usage limit (/usage)",
        )

    def test_estimate_unknown_tokens_says_at_least(self):
        self.run_md(
            [("a", "-", "1000", "done"), ("b", "a", "unknown", "approved")],
            planning="500",
        )
        self.assertIn("spent: at least 1500 tokens", self.out("estimate"))

    def test_estimate_planning_tokens_unknown_says_at_least(self):
        self.run_md([("a", "-", "1000", "done")], planning="unknown")
        self.assertIn("spent: at least 1000 tokens", self.out("estimate"))

    def test_estimate_accepts_unapproved_tasks(self):
        self.run_md([("a", "-", "0", "planned")])
        self.assertIn("| 1 | a |", self.out("estimate"))


class NextTest(RunFixture):
    def test_next_runs_first_pending(self):
        self.run_md([("a", "-", "10", "done"), ("b", "-", "0", "approved")])
        self.assertEqual(self.out("next"), "run b")

    def test_next_resumes_in_progress_before_any_breaker(self):
        self.run_md(
            [
                ("a", "-", "5000000", "escalated"),
                ("b", "-", "5000000", "blocked"),
                ("c", "-", "0", "implementing"),
                ("d", "-", "0", "approved"),
            ],
            budget="1000",
        )
        self.assertEqual(self.out("next"), "run c")

    def test_next_done_when_all_finished(self):
        self.run_md([("a", "-", "10", "done"), ("b", "a", "10", "done")])
        self.assertEqual(self.out("next"), "done")

    def test_next_semi_waits_on_failure(self):
        self.run_md([("a", "-", "10", "escalated"), ("b", "-", "0", "approved")])
        self.assertEqual(self.out("next"), "wait a: escalated")

    def test_next_semi_continue_past(self):
        self.run_md(
            [
                ("a", "-", "10", "blocked"),
                ("b", "a", "0", "approved"),
                ("c", "-", "0", "approved"),
            ],
            cont="a",
        )
        self.assertEqual(self.out("next"), "run c")

    def test_next_full_skips_dependents_of_failed(self):
        rows = [
            ("a", "-", "10", "escalated"),
            ("b", "a", "0", "approved"),
            ("c", "b", "0", "approved"),
            ("d", "-", "0", "approved"),
        ]
        self.run_md(rows, mode="full")
        self.assertEqual(self.out("next"), "run d")
        self.run_md(rows[:3] + [("d", "-", "10", "done")], mode="full")
        self.assertEqual(
            self.out("next"), "stop: cannot finish: b, c depend on failed a"
        )

    def test_breaker_wrong_direction(self):
        self.run_md(
            [
                ("a", "-", "10", "escalated"),
                ("b", "-", "10", "blocked"),
                ("c", "-", "0", "approved"),
            ],
            mode="full",
        )
        self.assertEqual(
            self.out("next"), "stop: wrong direction: a, b failed in a row"
        )

    def test_breaker_cannot_finish(self):
        self.run_md(
            [
                ("a", "-", "10", "done"),
                ("b", "-", "10", "escalated"),
                ("c", "b", "0", "approved"),
            ],
            mode="full",
        )
        self.assertEqual(self.out("next"), "stop: cannot finish: c depend on failed b")

    def test_breaker_burn_rate(self):
        self.run_md([("a", "-", str(2 * MAX + 1), "done"), ("b", "-", "0", "approved")])
        self.assertEqual(
            self.out("next"),
            f"stop: burn rate: a used {2 * MAX + 1}, more than 2 x its estimate {MAX}",
        )
        self.run_md([("a", "-", str(2 * MAX), "done"), ("b", "-", "0", "approved")])
        self.assertEqual(self.out("next"), "run b")

    def test_unknown_tokens_never_trip_burn_rate(self):
        self.run_md([("a", "-", "unknown", "done"), ("b", "-", "0", "approved")])
        self.assertEqual(self.out("next"), "run b")

    def test_breaker_budget(self):
        self.run_md(
            [("a", "-", "100000", "done"), ("b", "-", "0", "approved")],
            budget="450000",
            planning="50000",
        )
        self.assertEqual(
            self.out("next"),
            f"stop: budget: spent 150000 + next worst case {MAX} > budget 450000",
        )
        self.run_md(
            [("a", "-", "100000", "done"), ("b", "-", "0", "approved")],
            budget="550000",
            planning="50000",
        )
        self.assertEqual(self.out("next"), "run b")

    def test_failed_bootstrap_skips_every_task(self):
        rows = [
            ("boot", "-", "10", "escalated"),
            ("a", "boot", "0", "approved"),
            ("b", "boot, a", "0", "approved"),
        ]
        self.run_md(rows, mode="full", bootstrap="boot")
        self.assertEqual(
            self.out("next"), "stop: cannot finish: a, b depend on failed boot"
        )
        self.run_md(rows, mode="semi", bootstrap="boot")
        self.assertEqual(self.out("next"), "wait boot: escalated")


class MalformedTest(RunFixture):
    def test_malformed_run_exits_2(self):
        ok = [("a", "-", "0", "approved"), ("b", "a", "0", "approved")]
        cases = {
            "bad mode": (ok, {"mode": "fast"}, "next"),
            "bad budget": (ok, {"budget": "0"}, "next"),
            "bad budget text": (ok, {"budget": "lots"}, "estimate"),
            "dependency on later row": (
                [("a", "b", "0", "approved"), ("b", "-", "0", "approved")],
                {},
                "next",
            ),
            "duplicate id": (
                [("a", "-", "0", "approved"), ("a", "-", "0", "approved")],
                {},
                "next",
            ),
            "bad tokens": ([("a", "-", "-5", "approved")], {}, "estimate"),
            "missing bootstrap key": (ok, {"omit": ("bootstrap",)}, "next"),
            "non-integer planning-tokens": (ok, {"planning": "12k"}, "estimate"),
            "unrecognised phase": (
                [("a", "-", "0", "weird")],
                {},
                "estimate",
            ),
            "bootstrap not row 1": (ok, {"bootstrap": "b"}, "next"),
            "row not depending on bootstrap": (
                [("a", "-", "0", "approved"), ("b", "-", "0", "approved")],
                {"bootstrap": "a"},
                "next",
            ),
        }
        for name, (rows, kwargs, command) in cases.items():
            with self.subTest(name):
                self.run_md(rows, **kwargs)
                result = self.call(command)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(result.stdout, b"")
                self.assertTrue(result.stderr)
        with self.subTest("missing plan.md"):
            self.run_md([("a", "-", "0", "approved")])
            (self.root / ".work" / "a" / "plan.md").unlink()
            result = self.call("next")
            self.assertEqual((result.returncode, result.stdout), (2, b""))
            self.assertTrue(result.stderr)
        with self.subTest("unapproved task for next"):
            self.run_md([("a", "-", "0", "planned")])
            result = self.call("next")
            self.assertEqual((result.returncode, result.stdout), (2, b""))
            self.assertTrue(result.stderr)
            self.assertEqual(self.call("estimate").returncode, 0)

    def test_missing_run_md_exits_2(self):
        result = self.call("next")
        self.assertEqual((result.returncode, result.stdout), (2, b""))

    def test_undecodable_run_md_exits_2(self):
        (self.run_dir / "run.md").write_bytes(b"\xff\xfe\x00bad\xff")
        result = self.call("next")
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertTrue(result.stderr)

    def test_task_row_with_wrong_cell_count_exits_2(self):
        self.run_md([("a", "-", "0", "approved")])
        path = self.run_dir / "run.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "| 1 | a | - | 0 |", "| 1 | a | - |"
            ),
            encoding="utf-8",
        )
        result = self.call("next")
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertTrue(result.stderr)

    def test_rejects_dir_outside_autopilot(self):
        self.run_md([("a", "-", "0", "approved")])
        outside = self.root / "elsewhere" / "r1"
        outside.mkdir(parents=True)
        shutil.copy(self.run_dir / "run.md", outside / "run.md")
        for command in ("estimate", "next"):
            with self.subTest(command=command):
                result = self.call(command, outside)
                self.assertEqual((result.returncode, result.stdout), (2, b""))
        result = self.call("next", self.root / "does-not-exist")
        self.assertEqual((result.returncode, result.stdout), (2, b""))


@unittest.skipUnless(HAS_GIT, "git is not on PATH")
class InspectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args):
        subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True
        )

    def write(self, name, text="x\n"):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, *names, force=False):
        if names:
            self.git("add", *(["-f"] if force else []), *names)
        self.git("commit", "-q", "--allow-empty", "-m", "c")

    def inspect(self, target=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "inspect", str(target or self.repo)],
            capture_output=True,
            check=False,
        )

    def lines(self):
        result = self.inspect()
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode("utf-8").splitlines()

    def test_inspect_no_commit(self):
        lines = self.lines()
        self.assertEqual(lines[0], "commits: no")
        self.assertEqual(lines[3], "thin: yes")

    def test_inspect_thin_empty_commit(self):
        self.commit()
        self.assertEqual(
            self.lines(),
            [
                "commits: yes",
                "tracked: 0 files outside .work/",
                "claude-md: absent",
                "thin: yes",
            ],
        )

    def test_inspect_thin_minimal_claude_md(self):
        self.write("CLAUDE.md", "line\n" * 10)
        self.write("README.md")
        self.write(".gitignore")
        self.commit("CLAUDE.md", "README.md", ".gitignore")
        lines = self.lines()
        self.assertEqual(lines[1], "tracked: 3 files outside .work/")
        self.assertEqual(lines[2], "claude-md: 10 non-blank lines")
        self.assertEqual(lines[3], "thin: yes")

    def test_inspect_not_thin_with_code(self):
        self.write("CLAUDE.md", "line\n" * 10)
        self.write("src/app.py")
        self.commit("CLAUDE.md", "src/app.py")
        self.assertEqual(self.lines()[3], "thin: no")

    def test_inspect_not_thin_long_claude_md(self):
        self.write("CLAUDE.md", "line\n\n" * 41)
        self.commit("CLAUDE.md")
        lines = self.lines()
        self.assertEqual(lines[2], "claude-md: 41 non-blank lines")
        self.assertEqual(lines[3], "thin: no")

    def test_inspect_ignores_work_and_untracked(self):
        self.write("CLAUDE.md", "line\n")
        self.write(".work/x/task.md")
        self.commit("CLAUDE.md", ".work/x/task.md", force=True)
        self.write("main.py")
        lines = self.lines()
        self.assertEqual(lines[1], "tracked: 1 files outside .work/")
        self.assertEqual(lines[3], "thin: yes")

    def test_inspect_rejects_non_repo(self):
        with tempfile.TemporaryDirectory() as plain:
            result = self.inspect(plain)
            self.assertEqual((result.returncode, result.stdout), (2, b""))
            self.assertTrue(result.stderr)

    def test_inspect_rejects_unreadable_claude_md(self):
        (self.repo / "CLAUDE.md").write_bytes(b"\xff\xfe\x00bad\xff")
        result = self.inspect()
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertTrue(result.stderr)

    def test_inspect_refuses_when_git_is_missing(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "inspect", str(self.repo)],
            capture_output=True,
            check=False,
            env={"PATH": str(self.repo / "no-such-dir")},
        )
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertIn(b"git", result.stderr)

    def test_inspect_rejects_subdirectory(self):
        (self.repo / "sub").mkdir()
        result = self.inspect(self.repo / "sub")
        self.assertEqual((result.returncode, result.stdout), (2, b""))


if __name__ == "__main__":
    unittest.main()
