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

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "autopilot.py"
sys.path.insert(0, str(SCRIPTS))
import autopilot
import epics

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
        usd=None,
        source=None,
        epic=None,
    ):
        """rows: (id, depends, tokens, phase) tuples; also creates the task folders.

        usd: None for the 4-column table, or a list of 5th-cell strings, one per row.
        source: None omits the `source:` header line, otherwise its value.
        epic: None omits the `epic:` header line, otherwise its value.
        """
        header = {
            "mode": mode,
            "base": "main",
            "budget": budget,
            "planning-tokens": planning,
            "continue-past": cont,
            "bootstrap": bootstrap,
        }
        if source is not None:
            header["source"] = source
        if epic is not None:
            header["epic"] = epic
        header["state"] = "running"
        lines = ["# Autopilot: r1"]
        lines += [f"- {k}: {v}" for k, v in header.items() if k not in omit]
        if usd is None:
            lines += [
                "## Tasks",
                "| # | Task | Depends on | Tokens used |",
                "| --- | --- | --- | --- |",
            ]
            for number, (task_id, deps, tokens, phase) in enumerate(rows, 1):
                lines.append(f"| {number} | {task_id} | {deps} | {tokens} |")
                self.task(task_id, phase, plan())
        else:
            lines += [
                "## Tasks",
                "| # | Task | Depends on | Tokens used | External USD |",
                "| --- | --- | --- | --- | --- |",
            ]
            for number, (row, usd_cell) in enumerate(zip(rows, usd), 1):
                task_id, deps, tokens, phase = row
                lines.append(
                    f"| {number} | {task_id} | {deps} | {tokens} | {usd_cell} |"
                )
                self.task(task_id, phase, plan())
        lines += ["## History", "- 2026-10-24T00:00:00Z running — start"]
        (self.run_dir / "run.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def call(self, command, target=None, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), command, str(target or self.run_dir), *extra],
            capture_output=True,
            check=False,
        )

    def out(self, command, *extra):
        result = self.call(command, self.run_dir, *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode("utf-8").strip()


MAX = 400000  # 2 steps, 3 criteria, 2 commands
ONE = 154000
EXT_MAX = 238000  # same plan with IMPLEMENT = 0 (external implementer)
EXT_ONE = 100000


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

    def test_verification_commands_lists_first_block(self):
        text = (
            "# Plan: x\n## Goal\ng\n## Verification commands\n```\n"
            "cmd one\n\n# a comment\n"
            'git grep -nE "a|b" -- path\n'
            "```\n```\ncmd ignored\n```\na trailing paragraph\n## Steps\n1. not a command\n"
        )
        self.assertEqual(
            autopilot.verification_commands(text),
            ["cmd one", 'git grep -nE "a|b" -- path'],
        )
        self.assertEqual(autopilot.verification_commands("# Plan: x\n## Goal\ng\n"), [])
        # the shared parser counts exactly what plan_counts counts
        self.assertEqual(autopilot.plan_counts(text)[2], 2)

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


class ExternalTest(RunFixture):
    def test_estimate_external_implementer_zeroes_implement(self):
        self.run_md([("a", "-", "0", "approved")])
        out = self.out("estimate", "--implementer-kind", "profile")
        self.assertIn(f"| 1 | a | 2 | 3 | 2 | {EXT_ONE} | {EXT_MAX} |", out)
        self.assertIn(
            "implementer: external profile; IMPLEMENT counts 0 Claude tokens; "
            "its cost is measured in USD per round (impl-<n>.json), not estimated",
            out,
        )

    def test_estimate_default_kind_has_no_external_lines(self):
        self.run_md([("a", "-", "0", "approved")])
        out = self.out("estimate")
        self.assertNotIn("implementer:", out)
        self.assertNotIn("external spent:", out)
        self.assertEqual(out, self.out("estimate", "--implementer-kind", "claude"))

    def test_estimate_external_usd_column_summed(self):
        rows = [("a", "-", "0", "approved"), ("b", "a", "0", "approved")]
        self.run_md(rows, usd=["0.5", "1.25"])
        self.assertEqual(
            self.out("estimate").splitlines()[-1], "external spent: 1.75 USD"
        )
        self.run_md(rows, usd=["0.5", "unknown"])
        self.assertEqual(
            self.out("estimate").splitlines()[-1],
            "external spent: at least 0.50 USD",
        )

    def test_next_ignores_external_usd_column(self):
        rows = [("a", "-", "10", "done"), ("b", "-", "0", "approved")]
        self.run_md(rows, usd=["0.5", "1.25"])
        with_column = self.out("next")
        self.run_md(rows)
        self.assertEqual(with_column, self.out("next"))
        self.assertEqual(with_column, "run b")

    def test_estimate_external_usd_scientific_notation(self):
        rows = [("a", "-", "0", "approved"), ("b", "a", "0", "approved")]
        cases = {
            ("1.2e-05", "0.5"): "external spent: 0.50 USD",
            ("1e-05", "1.2E-05"): "external spent: 0.00 USD",
            ("0.000012", "0"): "external spent: 0.00 USD",
        }
        for usd, expected in cases.items():
            with self.subTest(usd=usd):
                self.run_md(rows, usd=list(usd))
                self.assertEqual(self.out("estimate").splitlines()[-1], expected)

    def test_next_accepts_external_usd_scientific_notation(self):
        rows = [("a", "-", "10", "done"), ("b", "-", "0", "approved")]
        self.run_md(rows, usd=["1.2e-05", "0"])
        with_column = self.out("next")
        self.run_md(rows)
        self.assertEqual(with_column, self.out("next"))
        self.assertEqual(with_column, "run b")

    def test_breaker_burn_rate_external(self):
        self.run_md(
            [("a", "-", str(2 * EXT_MAX + 1), "done"), ("b", "-", "0", "approved")]
        )
        self.assertEqual(
            self.out("next", "--implementer-kind", "profile"),
            f"stop: burn rate: a used {2 * EXT_MAX + 1}, "
            f"more than 2 x its estimate {EXT_MAX}",
        )
        self.run_md([("a", "-", str(2 * EXT_MAX), "done"), ("b", "-", "0", "approved")])
        self.assertEqual(self.out("next", "--implementer-kind", "profile"), "run b")

    def test_breaker_budget_external(self):
        rows = [("a", "-", "100000", "done"), ("b", "-", "0", "approved")]
        self.run_md(rows, budget="387999", planning="50000")
        self.assertEqual(
            self.out("next", "--implementer-kind", "profile"),
            f"stop: budget: spent 150000 + next worst case {EXT_MAX} > budget 387999",
        )
        self.run_md(rows, budget="388000", planning="50000")
        self.assertEqual(self.out("next", "--implementer-kind", "profile"), "run b")


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

    def test_malformed_external_inputs_exit_2(self):
        ok = [("a", "-", "0", "approved"), ("b", "a", "0", "approved")]
        for bad in ("abc", "-1"):
            with self.subTest(usd=bad):
                self.run_md(ok, usd=[bad, "0"])
                result = self.call("estimate")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(result.stdout, b"")
                self.assertIn(
                    "External USD of a must be a non-negative number or 'unknown', "
                    f"got {bad!r}",
                    result.stderr.decode("utf-8"),
                )
        with self.subTest(header="wrong 5th cell"):
            self.run_md(ok, usd=["0", "0"])
            path = self.run_dir / "run.md"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "| Tokens used | External USD |", "| Tokens used | Cost |"
                ),
                encoding="utf-8",
            )
            result = self.call("estimate")
            self.assertEqual((result.returncode, result.stdout), (2, b""))
            self.assertIn(
                "the 5th header cell must be 'External USD', got 'Cost'",
                result.stderr.decode("utf-8"),
            )
        with self.subTest(header="5 cells, short data row"):
            self.run_md(ok, usd=["0", "0"])
            path = self.run_dir / "run.md"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "| 1 | a | - | 0 | 0 |", "| 1 | a | - | 0 |"
                ),
                encoding="utf-8",
            )
            result = self.call("next")
            self.assertEqual((result.returncode, result.stdout), (2, b""))
            self.assertIn(
                "malformed task row: '| 1 | a | - | 0 |'",
                result.stderr.decode("utf-8"),
            )
        with self.subTest(kind="external"):
            self.run_md(ok)
            result = self.call("estimate", None, "--implementer-kind", "external")
            self.assertEqual((result.returncode, result.stdout), (2, b""))
            self.assertIn(
                "argument --implementer-kind: invalid choice",
                result.stderr.decode("utf-8"),
            )

    def test_malformed_external_usd_numbers_exit_2(self):
        ok = [("a", "-", "0", "approved"), ("b", "a", "0", "approved")]
        for bad in ("-1.2e-05", "1e", "1e-", "inf", "nan", "1e999"):
            with self.subTest(usd=bad):
                self.run_md(ok, usd=[bad, "0"])
                result = self.call("estimate")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(result.stdout, b"")
                self.assertIn(
                    "External USD of a must be a non-negative number or 'unknown'",
                    result.stderr.decode("utf-8"),
                )

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


@unittest.skipUnless(HAS_GIT, "git is not on PATH")
class StackTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "commit.gpgsign", "false")
        self.work = self.repo / ".work"
        self.work.mkdir()

    def git(self, *args):
        subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True
        )

    def task(self, task_id, phase, plan_text=None):
        directory = self.work / task_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "status.md").write_text(
            f"# Status: {task_id}\n- phase: {phase}\n## History\n", encoding="utf-8"
        )
        if plan_text is not None:
            (directory / "plan.md").write_text(plan_text, encoding="utf-8")

    def stack(self, ids, *extra, root=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "stack", str(root or self.repo), ids, *extra],
            capture_output=True,
            check=False,
        )

    def assert_refused(self, result, needle=None):
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(result.stderr)
        if needle is not None:
            self.assertIn(needle, result.stderr.decode("utf-8", "replace"))

    def test_stack_prints_table(self):
        self.task("a", "new")
        self.task("b", "planned", plan())
        self.task("c", "approved", plan())
        result = self.stack("a,b,c")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.decode("utf-8").replace("\r\n", "\n"),
            "| # | Task | Depends on | Tokens used |\n"
            "| --- | --- | --- | --- |\n"
            "| 1 | a | - | 0 |\n"
            "| 2 | b | a | 0 |\n"
            "| 3 | c | b | 0 |\n",
        )

    def test_stack_strips_whitespace_around_ids(self):
        self.task("a", "new")
        self.task("b", "new")
        result = self.stack(" a , b ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("| 2 | b | a | 0 |", result.stdout.decode("utf-8"))

    def test_stack_profile_adds_usd_column(self):
        self.task("a", "new")
        self.task("b", "planned", plan())
        result = self.stack("a,b", "--implementer-kind", "profile")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.decode("utf-8").replace("\r\n", "\n"),
            "| # | Task | Depends on | Tokens used | External USD |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 1 | a | - | 0 | 0 |\n"
            "| 2 | b | a | 0 | 0 |\n",
        )

    def test_stack_refuses_unknown_id(self):
        self.task("a", "new")
        self.assert_refused(self.stack("a,zzz"), "zzz")

    def test_stack_refuses_done(self):
        self.task("a", "done")
        self.assert_refused(self.stack("a"), "a")

    def test_stack_refuses_escalated(self):
        self.task("a", "escalated")
        self.assert_refused(self.stack("a"), "a")

    def test_stack_refuses_blocked(self):
        self.task("a", "blocked")
        self.assert_refused(self.stack("a"), "a")

    def test_stack_refuses_in_progress(self):
        self.task("a", "implementing")
        self.assert_refused(self.stack("a"), "a")

    def test_stack_refuses_duplicate(self):
        self.task("a", "new")
        self.assert_refused(self.stack("a,a"), "a")

    def test_stack_refuses_empty_element(self):
        self.task("a", "new")
        self.task("b", "new")
        self.assert_refused(self.stack("a,,b"))

    def test_stack_refuses_empty_list(self):
        self.assert_refused(self.stack(""))

    def test_stack_refuses_path_separator(self):
        self.assert_refused(self.stack("a/b"), "a/b")
        self.assert_refused(self.stack("a\\b"), "a\\b")

    def test_stack_refuses_hidden_or_underscore_id(self):
        self.assert_refused(self.stack("_autopilot"), "_autopilot")
        self.assert_refused(self.stack(".hidden"), ".hidden")

    def test_stack_refuses_epic_id(self):
        self.task("a", "new")
        (self.work / "_epics" / "e1").mkdir(parents=True)
        self.assert_refused(self.stack("e1"), "epic id, not a task")
        self.assert_refused(self.stack("a,e1"), "epic id, not a task")

    def test_stack_refuses_planned_without_plan(self):
        self.task("a", "planned")
        self.assert_refused(self.stack("a"), "a")

    def test_stack_refuses_worktree(self):
        self.task("a", "new")
        (self.work / "a" / "worktree").mkdir()
        self.assert_refused(self.stack("a"), "a")

    def test_stack_refuses_existing_branch(self):
        self.task("a", "new")
        self.git("commit", "-q", "--allow-empty", "-m", "c")
        self.git("branch", "task/a")
        self.assert_refused(self.stack("a"), "a")

    def test_stack_refuses_non_git_root(self):
        with tempfile.TemporaryDirectory() as plain:
            work = Path(plain) / ".work" / "a"
            work.mkdir(parents=True)
            (work / "status.md").write_text(
                "# Status: a\n- phase: new\n", encoding="utf-8"
            )
            result = self.stack("a", root=plain)
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertIn(b"git", result.stderr)


class StackedRunTest(RunFixture):
    @staticmethod
    def chain(count):
        rows = [("t1", "-", "0", "approved")]
        rows += [(f"t{i}", f"t{i - 1}", "0", "approved") for i in range(2, count + 1)]
        return rows

    def assert_both_refuse(self):
        for command in ("estimate", "next"):
            with self.subTest(command=command):
                result = self.call(command)
                self.assertEqual((result.returncode, result.stdout), (2, b""))
                self.assertTrue(result.stderr)

    def test_accepts_strict_chain(self):
        self.run_md(
            [("a", "-", "0", "approved"), ("b", "a", "0", "approved")],
            source="tasks",
        )
        self.assertIn("| 1 | a |", self.out("estimate"))
        self.assertEqual(self.out("next"), "run a")

    def test_refuses_row1_with_dependency(self):
        self.run_md(
            [("a", "b", "0", "approved"), ("b", "-", "0", "approved")],
            source="tasks",
        )
        self.assert_both_refuse()

    def test_refuses_later_row_not_exactly_previous(self):
        cases = [
            [("a", "-", "0", "approved"), ("b", "-", "0", "approved")],
            [
                ("a", "-", "0", "approved"),
                ("b", "a", "0", "approved"),
                ("c", "a", "0", "approved"),
            ],
            [
                ("a", "-", "0", "approved"),
                ("b", "a", "0", "approved"),
                ("c", "a, b", "0", "approved"),
            ],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                self.run_md(rows, source="tasks")
                self.assert_both_refuse()

    def test_refuses_bootstrap(self):
        self.run_md(
            [("a", "-", "0", "approved"), ("b", "a", "0", "approved")],
            source="tasks",
            bootstrap="a",
        )
        self.assert_both_refuse()

    def test_refuses_unknown_source(self):
        self.run_md([("a", "-", "0", "approved")], source="weird")
        self.assert_both_refuse()

    def test_backlog_run_without_source_needs_no_chain(self):
        self.run_md([("a", "-", "0", "approved"), ("b", "-", "0", "approved")])
        self.assertEqual(self.out("next"), "run a")

    def test_estimate_warns_above_ten_tasks(self):
        self.run_md(self.chain(11), source="tasks")
        self.assertEqual(
            self.out("estimate").splitlines()[-1],
            "warning: 11 tasks; more than the 10 a decomposed backlog allows — "
            "check the totals against your usage limit (/usage)",
        )

    def test_estimate_no_warning_at_ten_tasks(self):
        self.run_md(self.chain(10), source="tasks")
        self.assertNotIn("warning:", self.out("estimate"))

    def test_estimate_no_warning_for_backlog_run_with_eleven_rows(self):
        rows = [(f"t{i}", "-", "0", "approved") for i in range(1, 12)]
        self.run_md(rows)
        self.assertNotIn("warning:", self.out("estimate"))

    def test_next_runs_rows_in_order(self):
        self.run_md(
            [("a", "-", "0", "approved"), ("b", "a", "0", "approved")],
            source="tasks",
        )
        self.assertEqual(self.out("next"), "run a")
        self.run_md(
            [("a", "-", "10", "done"), ("b", "a", "0", "approved")],
            source="tasks",
        )
        self.assertEqual(self.out("next"), "run b")

    def test_next_full_skips_everything_after_failure(self):
        rows = [
            ("a", "-", "10", "done"),
            ("b", "a", "10", "escalated"),
            ("c", "b", "0", "approved"),
            ("d", "c", "0", "approved"),
        ]
        self.run_md(rows, mode="full", source="tasks")
        self.assertEqual(
            self.out("next"), "stop: cannot finish: c, d depend on failed b"
        )

    def test_next_semi_waits_then_cannot_finish(self):
        rows = [
            ("a", "-", "10", "done"),
            ("b", "a", "10", "escalated"),
            ("c", "b", "0", "approved"),
        ]
        self.run_md(rows, mode="semi", source="tasks")
        self.assertEqual(self.out("next"), "wait b: escalated")
        self.run_md(rows, mode="semi", source="tasks", cont="b")
        self.assertEqual(self.out("next"), "stop: cannot finish: c depend on failed b")


class EpicSourceTest(RunFixture):
    """`source: epic` runs: validated epic linkage, forest `next`, dependency-aware base."""

    def make_epic(self, rows, epic_id="demo", base="epic-base", active_run="r1"):
        directory = self.root / ".work" / "_epics" / epic_id
        directory.mkdir(parents=True, exist_ok=True)
        tasks = [
            epics.EpicTask(number=number, task_id=task_id, depends=depends)
            for number, (task_id, depends) in enumerate(rows, 1)
        ]
        epic = epics.Epic(
            epic_id=epic_id,
            title="T",
            goal="G",
            base=base,
            state="active",
            delivery="per-task",
            active_run=active_run,
            tasks=tasks,
            history=["- 2026-01-01T00:00:00Z created — fixture"],
        )
        (directory / "epic.md").write_bytes(epics.render_epic(epic).encode("utf-8"))
        return epic

    def epic_run(
        self, rows, mode="full", epic_id="demo", source="epic", epic_field="demo"
    ):
        self.run_md(
            rows,
            mode=mode,
            source=source,
            epic=epic_field,
        )

    def test_accepts_matching_epic_table(self):
        self.make_epic([("a", None), ("b", None), ("c", "a")])
        self.epic_run(
            [
                ("a", "-", "0", "approved"),
                ("b", "-", "0", "approved"),
                ("c", "a", "0", "approved"),
            ]
        )
        self.assertIn("| 1 | a |", self.out("estimate"))
        self.assertEqual(self.out("next"), "run a")

    def test_refuses_unlinked_epic(self):
        self.make_epic([("a", None)], active_run="none")
        self.epic_run([("a", "-", "0", "approved")])
        for command in ("estimate", "next"):
            with self.subTest(command=command):
                result = self.call(command)
                self.assertEqual((result.returncode, result.stdout), (2, b""))
                self.assertIn(b"not linked to run", result.stderr)

    def test_refuses_second_run_of_same_epic(self):
        self.make_epic([("a", None)], active_run="r1")
        self.epic_run([("a", "-", "0", "approved")])
        self.assertEqual(self.out("next"), "run a")
        other = self.root / ".work" / "_autopilot" / "r2"
        other.mkdir(parents=True)
        text = (
            (self.run_dir / "run.md")
            .read_text(encoding="utf-8")
            .replace("# Autopilot: r1", "# Autopilot: r2")
        )
        (other / "run.md").write_text(text, encoding="utf-8")
        result = self.call("next", other)
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertIn(b"not linked to run 'r2'", result.stderr)

    def test_refuses_row_mismatch_with_epic(self):
        self.make_epic([("a", None), ("b", None)])
        self.task("b", "approved", plan())
        self.epic_run([("a", "-", "0", "approved"), ("c", "-", "0", "approved")])
        result = self.call("next")
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertIn(b"do not match the epic", result.stderr)

    def test_refuses_dependency_mismatch_with_epic(self):
        self.make_epic([("a", None), ("c", "a")])
        self.epic_run([("a", "-", "0", "approved"), ("c", "-", "0", "approved")])
        result = self.call("next")
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertIn(b"depends on", result.stderr)

    def test_refuses_epic_line_without_epic_source(self):
        self.make_epic([("a", None)])
        self.epic_run([("a", "-", "0", "approved")], source="tasks", epic_field="demo")
        result = self.call("next")
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertIn(b"only allowed with source: epic", result.stderr)

    def test_refuses_missing_epic_line(self):
        self.make_epic([("a", None)])
        self.run_md([("a", "-", "0", "approved")], source="epic")
        result = self.call("next")
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertIn(b"epic: <epic-id>", result.stderr)

    def test_refuses_completed_epic(self):
        self.make_epic([("a", None)])
        self.task("a", "done", plan())
        self.epic_run([("a", "-", "10", "done")])
        result = self.call("next")
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertIn(b"completed", result.stderr)

    def test_next_forest_failing_root_blocks_child_permits_other_root(self):
        self.make_epic([("a", None), ("b", None), ("c", "a")])
        self.epic_run(
            [
                ("a", "-", "10", "escalated"),
                ("b", "-", "0", "approved"),
                ("c", "a", "0", "approved"),
            ],
            mode="full",
        )
        self.assertEqual(self.out("next"), "run b")
        self.epic_run(
            [
                ("a", "-", "10", "escalated"),
                ("b", "-", "10", "done"),
                ("c", "a", "0", "approved"),
            ],
            mode="full",
        )
        self.assertEqual(self.out("next"), "stop: cannot finish: c depend on failed a")

    def test_base_roots_use_epic_base_children_use_parent(self):
        self.make_epic(
            [("a", None), ("z", None), ("b", None), ("c", "a")], base="epic-base"
        )
        self.epic_run(
            [
                ("a", "-", "10", "done"),
                ("z", "-", "10", "done"),
                ("b", "-", "0", "approved"),
                ("c", "a", "0", "approved"),
            ]
        )
        # roots use the epic base, irrespective of the unrelated done rows a and z
        self.assertEqual(self.out_base("b"), "epic-base")
        # the child uses its completed parent's branch, not the last done row
        self.assertEqual(self.out_base("c"), "task/a")

    def test_base_refuses_child_with_parent_not_done(self):
        self.make_epic([("a", None), ("c", "a")])
        self.epic_run([("a", "-", "0", "approved"), ("c", "a", "0", "approved")])
        result = self.call_base("c")
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertIn(b"parent a of c is not done", result.stderr)

    def call_base(self, task_id):
        return self.call("base", self.run_dir, task_id)

    def out_base(self, task_id):
        result = self.call_base(task_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode("utf-8").strip()


class BaseTest(RunFixture):
    def call_base(self, task_id):
        return self.call("base", self.run_dir, task_id)

    def out_base(self, task_id):
        result = self.call_base(task_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode("utf-8").strip()

    def test_base_is_run_base_when_nothing_done(self):
        self.run_md([("a", "-", "0", "approved"), ("b", "a", "0", "approved")])
        self.assertEqual(self.out_base("a"), "main")
        self.assertEqual(self.out_base("b"), "main")

    def test_base_is_last_done_branch(self):
        self.run_md(
            [
                ("a", "-", "10", "done"),
                ("b", "a", "0", "approved"),
                ("c", "b", "0", "approved"),
            ]
        )
        self.assertEqual(self.out_base("b"), "task/a")
        self.assertEqual(self.out_base("c"), "task/a")

    def test_base_is_last_done_in_table_order(self):
        self.run_md(
            [
                ("a", "-", "10", "done"),
                ("b", "a", "10", "done"),
                ("c", "b", "0", "approved"),
            ]
        )
        self.assertEqual(self.out_base("c"), "task/b")

    def test_base_refuses_id_not_in_table(self):
        self.run_md([("a", "-", "0", "approved")])
        result = self.call_base("zzz")
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertIn(b"zzz", result.stderr)

    def test_base_refuses_started_task(self):
        self.run_md([("a", "-", "0", "implementing"), ("b", "a", "0", "approved")])
        result = self.call_base("a")
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertTrue(result.stderr)

    def test_base_refuses_worktree(self):
        self.run_md([("a", "-", "0", "approved")])
        (self.root / ".work" / "a" / "worktree").mkdir()
        result = self.call_base("a")
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertTrue(result.stderr)

    def test_base_refuses_missing_base_header(self):
        self.run_md([("a", "-", "0", "approved")], omit=("base",))
        result = self.call_base("a")
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertTrue(result.stderr)


if __name__ == "__main__":
    unittest.main()
