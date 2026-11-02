# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/gates.py, the deterministic checker (no model involved).

Each test builds a temp fake repository (`.work/t1/plan.md` plus a worktree dir),
points HOME and USERPROFILE at a temp dir and runs the script by subprocess. Plan
command lines are built from sys.executable and small helper scripts written into the
temp dir, always as quoted paths, so the backslash-literal rule is exercised on
Windows. No real `.work/<id>/` and no repository state is touched.

Run: uv run --script plugins/task-flow/tests/test_gates.py
"""

import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "gates.py"
sys.path.insert(0, str(SCRIPTS))
import gates

PLAN_HEAD = "# Plan: demo\n## Goal\nDo it.\n## Verification commands\n```\n"


def quoted(path) -> str:
    return '"' + str(path) + '"'


class GatesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.repo = self.root / "repo"
        self.worktree = self.root / "worktree"
        for folder in (self.home, self.repo, self.worktree):
            folder.mkdir()
        (self.repo / ".work").mkdir()
        self.task = self.repo / ".work" / "t1"
        self.task.mkdir()

    def write_plan(self, commands):
        text = PLAN_HEAD + "\n".join(commands) + "\n```\n"
        (self.task / "plan.md").write_text(text, encoding="utf-8")

    def helper(self, name, code):
        path = self.root / name
        path.write_text(code, encoding="utf-8")
        return path

    def py(self, path, *args):
        """A plan line running a helper with sys.executable, both paths quoted."""
        return " ".join([quoted(sys.executable), quoted(path), *args])

    def env(self):
        environment = dict(os.environ)
        environment["HOME"] = str(self.home)
        environment["USERPROFILE"] = str(self.home)
        return environment

    def run_gates(self, *extra, round_no=1, worktree=None, task_dir=None):
        cmd = [
            sys.executable,
            str(SCRIPT),
            "--task-dir",
            str(task_dir or self.task),
            "--worktree",
            str(worktree) if worktree is not None else str(self.worktree),
            "--round",
            str(round_no),
            *extra,
        ]
        return subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            env=self.env(),
            cwd=self.root,
            timeout=120,
            check=False,
        )

    def checks(self, round_no=1):
        return (self.task / f"checks-{round_no}.md").read_text(encoding="utf-8")

    def rows(self, text):
        return [line for line in text.splitlines() if re.match(r"\| \d+ \|", line)]

    def test_split_command_rules(self):
        split = gates.split_command
        self.assertEqual(split("a b  c"), ["a", "b", "c"])
        self.assertEqual(split("a 'b c' d"), ["a", "b c", "d"])
        self.assertEqual(split('a "b c" d'), ["a", "b c", "d"])
        self.assertEqual(split('--x="a b"'), ["--x=a b"])
        self.assertEqual(split("C:\\a\\b.exe arg"), ["C:\\a\\b.exe", "arg"])
        self.assertEqual(
            split('git grep --untracked -nE "a|b" -- path'),
            ["git", "grep", "--untracked", "-nE", "a|b", "--", "path"],
        )
        for line in (
            "x | y",
            "x && y",
            "a; b",
            "a > f",
            "a < f",
            "cmd 2>&1",
            "$HOME",
            "*.py",
            "`x`",
            "(x)",
            "FOO=1 cmd",
            '"abc',
            "'abc",
        ):
            with self.subTest(line=line):
                with self.assertRaises(ValueError) as ctx:
                    split(line)
                self.assertIn("without a shell", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            split("")
        self.assertIn("empty", str(ctx.exception))
        for line, char in (
            ("x | y", "'|'"),
            ("x && y", "'&'"),
            ("a; b", "';'"),
            ("a > f", "'>'"),
            ("a < f", "'<'"),
            ("cmd 2>&1", "'>'"),  # in 2>&1 the > is met before the &
            ("$HOME", "'$'"),
            ("*.py", "'*'"),
            ("`x`", "'`'"),
            ("(x)", "'('"),
        ):
            with self.subTest(line=line):
                with self.assertRaises(ValueError) as ctx:
                    split(line)
                self.assertIn(char, str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            split("FOO=1 cmd")
        self.assertIn("environment assignment", str(ctx.exception))
        self.assertIn("FOO=1", str(ctx.exception))
        for line in ('"abc', "'abc"):
            with self.subTest(line=line):
                with self.assertRaises(ValueError) as ctx:
                    split(line)
                self.assertIn("not closed", str(ctx.exception))

    def test_format_pass_fail_and_order(self):
        ok = self.helper("ok.py", "print('fine')\n")
        bad = self.helper("bad.py", "import sys\nprint('boom')\nsys.exit(3)\n")
        self.write_plan([self.py(ok), self.py(bad), self.py(ok)])
        done = self.run_gates(round_no=1)
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(
            lines[0],
            r"^checks-1: 2 PASS, 1 FAIL, 0 ERROR, 0 not run -> .+checks-1\.md$",
        )
        text = self.checks(1)
        self.assertTrue(text.startswith("# Checks: round 1\n"))
        self.assertIn("| # | Command | Result | Exit | Duration |", text)
        self.assertIn("| --- | --- | --- | --- | --- |", text)
        rows = self.rows(text)
        self.assertEqual(len(rows), 3)
        expected = (("1", "PASS", "0"), ("2", "FAIL", "3"), ("3", "PASS", "0"))
        for row, (number, result, code) in zip(rows, expected):
            self.assertRegex(row, rf"^\| {number} \| .* \| {result} \| {code} \| ")
            self.assertRegex(row, r"\| (<1s|\d+s) \|$")
        self.assertIn("## Failures\n", text)
        self.assertIn("### 2. ", text)
        self.assertIn("boom", text)
        self.assertIn("## Not run\n", text)
        self.assertEqual(text.splitlines()[-1], gates.PROFILE_DEFAULTS_LINE)

    def test_excerpt_unittest_and_pytest_lines(self):
        helper = self.helper(
            "noisy.py",
            "print('noise line one')\n"
            "print('FAIL: test_x (m.C.test_x)')\n"
            "print('Traceback (most recent call last):')\n"
            "print('  File \"x.py\", line 3, in <module>')\n"
            "print('AssertionError: 1 != 2')\n"
            "print('FAILED t.py::test_y - assert 0')\n"
            "print('more noise')\n"
            "import sys\n"
            "sys.exit(1)\n",
        )
        self.write_plan([self.py(helper)])
        done = self.run_gates()
        self.assertEqual(done.returncode, 0, done.stderr)
        text = self.checks(1)
        self.assertIn("FAIL: test_x (m.C.test_x)", text)
        self.assertIn("AssertionError: 1 != 2", text)
        self.assertIn("FAILED t.py::test_y - assert 0", text)
        self.assertNotIn("noise line one", text)
        self.assertNotIn("more noise", text)
        self.assertNotIn('File "x.py", line 3', text)

    def test_excerpt_ruff_lines(self):
        output = (
            "some ruff noise\n"
            "F841 Local variable `x` is assigned to but never used\n"
            " --> src/main.py:2:5\n"
            "src/main.py:3:9: E501 Line too long (91 > 88 characters)\n"
            "Would reformat: a.py\n"
            "Found 2 errors.\n"
            "Apply fixes with `ruff check --fix`.\n"
        )
        lines = gates.excerpt(output, False, 600)
        self.assertIn("F841 Local variable `x` is assigned to but never used", lines)
        self.assertIn(" --> src/main.py:2:5", lines)
        self.assertIn("src/main.py:3:9: E501 Line too long (91 > 88 characters)", lines)
        self.assertIn("Would reformat: a.py", lines)
        self.assertIn("Found 2 errors.", lines)
        self.assertNotIn("some ruff noise", lines)
        self.assertNotIn("Apply fixes with `ruff check --fix`.", lines)

    def test_excerpt_fallback_last_lines_and_limit(self):
        unmatched = "\n".join(f"plain line {i:03d}" for i in range(1, 101))
        lines = gates.excerpt(unmatched, False, 600)
        self.assertEqual(len(lines), 31)
        self.assertEqual(lines[0], "[... 70 earlier lines not shown]")
        self.assertIn("plain line 100", lines)
        self.assertIn("plain line 071", lines)
        self.assertNotIn("plain line 070", lines)
        matched = "\n".join(f"FAILED t{i}.py::test_{i}" for i in range(50))
        lines = gates.excerpt(matched, False, 600)
        self.assertEqual(len(lines), 31)
        self.assertEqual(lines[0], "FAILED t0.py::test_0")
        self.assertEqual(lines[-1], "[... 20 more matching lines not shown]")
        timed = gates.excerpt(matched, True, 5)
        self.assertEqual(len(timed), 31)
        self.assertTrue(timed[0].startswith("timed out after 5 s"))
        for output in (unmatched, matched):
            self.assertLessEqual(len(gates.excerpt(output, True, 2)), 31)

    def test_timeout_is_error_and_kills_tree(self):
        helper = self.helper(
            "tree.py",
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print('started', flush=True)\n"
            "time.sleep(60)\n",
        )
        self.write_plan([self.py(helper)])
        started = time.monotonic()
        done = self.run_gates("--timeout-seconds", "2")
        elapsed = time.monotonic() - started
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertLess(elapsed, 30)
        self.assertRegex(
            done.stdout, r"^checks-1: 0 PASS, 0 FAIL, 1 ERROR, 0 not run -> "
        )
        text = self.checks(1)
        rows = self.rows(text)
        self.assertEqual(len(rows), 1)
        self.assertRegex(rows[0], r"^\| 1 \| .* \| ERROR \| - \| (<1s|\d+s) \|$")
        self.assertIn("timed out after 2 s; the process tree was killed", text)

    def test_timeout_from_models_toml(self):
        helper = self.helper("sleep.py", "import time\ntime.sleep(30)\n")
        self.write_plan([self.py(helper)])
        config = self.repo / ".claude" / "task-flow.models.toml"
        config.parent.mkdir()
        config.write_text("[gates]\ncommand_timeout_seconds = 1\n", encoding="utf-8")
        done = self.run_gates()
        self.assertEqual(done.returncode, 0, done.stderr)
        text = self.checks(1)
        self.assertIn("Timeout 1 s per command", text)
        self.assertIn("timed out after 1 s; the process tree was killed", text)
        self.assertRegex(self.rows(text)[0], r"^\| 1 \| .* \| ERROR \| - \|")

    def test_refused_shell_line_is_error(self):
        marker = self.helper("mark.py", "open('marker.txt', 'w').write('x')\n")
        self.write_plan([self.py(marker) + " | cat", self.py(marker) + " > out.txt"])
        done = self.run_gates()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(
            done.stdout, r"^checks-1: 0 PASS, 0 FAIL, 2 ERROR, 0 not run -> "
        )
        text = self.checks(1)
        rows = self.rows(text)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertRegex(row, r"^\| \d+ \| .* \| ERROR \| - \|")
        self.assertIn("refused:", text)
        self.assertIn("'|'", text)
        self.assertIn("'>'", text)
        self.assertFalse((self.worktree / "marker.txt").exists())
        self.assertFalse((self.worktree / "out.txt").exists())

    def test_denied_command_is_error(self):
        self.write_plan(["git push origin main"])
        done = self.run_gates()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(
            done.stdout, r"^checks-1: 0 PASS, 0 FAIL, 1 ERROR, 0 not run -> "
        )
        text = self.checks(1)
        self.assertRegex(self.rows(text)[0], r"^\| 1 \| .* \| ERROR \| - \|")
        self.assertIn("denied:", text)
        self.assertIn("push", text)

    def test_missing_program_not_run(self):
        self.write_plan(["no-such-tool-xyz --version"])
        done = self.run_gates()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(
            done.stdout, r"^checks-1: 0 PASS, 0 FAIL, 0 ERROR, 1 not run -> "
        )
        text = self.checks(1)
        self.assertEqual(self.rows(text), [])
        self.assertIn("- 1. `no-such-tool-xyz --version`", text)
        self.assertIn("program not found: no-such-tool-xyz", text)
        self.assertEqual(text.splitlines()[-1], gates.PROFILE_DEFAULTS_LINE)

    def cwd_from_failures(self, text):
        block = text.split("## Failures\n", 1)[1]
        line = next(l for l in block.splitlines() if Path(l.strip()).is_absolute())
        return Path(line.strip()).resolve()

    def test_round_zero_and_worktree_none(self):
        helper = self.helper(
            "cwd.py", "import os, sys\nprint(os.getcwd())\nsys.exit(1)\n"
        )
        self.write_plan([self.py(helper)])
        done = self.run_gates(round_no=0, worktree="none")
        self.assertEqual(done.returncode, 0, done.stderr)  # round 0 failures exit 0
        text = self.checks(0)
        self.assertTrue(text.startswith("# Checks: round 0\n"))
        self.assertEqual(self.cwd_from_failures(text), self.repo.resolve())

    def test_worktree_is_cwd(self):
        helper = self.helper(
            "cwd.py", "import os, sys\nprint(os.getcwd())\nsys.exit(1)\n"
        )
        self.write_plan([self.py(helper)])
        done = self.run_gates(worktree=self.worktree)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            self.cwd_from_failures(self.checks(1)), self.worktree.resolve()
        )

    def test_refusals_exit_2_write_nothing(self):
        def assert_refused(done, task_dir=None):
            self.assertEqual(done.returncode, 2)
            self.assertEqual(done.stdout, "")
            self.assertNotEqual(done.stderr, "")
            folder = task_dir or self.task
            self.assertEqual(list(folder.glob("checks-*.md")), [])

        ok = self.helper("ok.py", "print('fine')\n")
        assert_refused(self.run_gates())  # no plan.md yet
        (self.task / "plan.md").write_text("# Plan: x\n## Goal\ng\n", encoding="utf-8")
        assert_refused(self.run_gates())  # no Verification commands section
        self.write_plan(["# nothing here"])
        assert_refused(self.run_gates())  # empty block
        self.write_plan([self.py(ok)])
        assert_refused(self.run_gates(worktree=self.root / "nope"))  # bad worktree
        assert_refused(self.run_gates(round_no=-1))  # negative round
        assert_refused(self.run_gates(task_dir=self.root / "no-such-task"))
        outside = self.root / "elsewhere" / "t9"
        outside.mkdir(parents=True)
        (outside / "plan.md").write_text(
            PLAN_HEAD + self.py(ok) + "\n```\n", encoding="utf-8"
        )
        assert_refused(self.run_gates(task_dir=outside), task_dir=outside)  # not .work
        config = self.repo / ".claude" / "task-flow.models.toml"
        config.parent.mkdir()
        config.write_text("[gates]\ncommand_timeout_seconds = 0\n", encoding="utf-8")
        assert_refused(self.run_gates())  # invalid models.toml

    def test_markdown_escaping(self):
        ticks = self.helper(
            "ticks.py", "print('err ``` tick')\nimport sys\nsys.exit(1)\n"
        )
        code = "print('a|b')"
        pipe_cmd = quoted(sys.executable) + " -c " + '"' + code + '"'
        self.write_plan([pipe_cmd, self.py(ticks)])
        done = self.run_gates()
        self.assertEqual(done.returncode, 0, done.stderr)
        text = self.checks(1)
        rows = self.rows(text)
        self.assertEqual(len(rows), 2)
        self.assertIn("a\\|b", rows[0])  # the pipe is escaped in the table cell
        self.assertRegex(rows[0], r"^\| 1 \| .* \| PASS \| 0 \|")
        self.assertRegex(rows[1], r"^\| 2 \| .* \| FAIL \| 1 \|")
        self.assertIn("\n````\nerr ``` tick\n````\n", text)  # a longer fence

    @unittest.skipUnless(os.name == "nt", "cmd.exe exists only on Windows")
    def test_cmd_script_runs_on_windows(self):
        script = self.root / "hi.cmd"
        script.write_text(
            "@echo off\r\necho [%~1]\r\nexit /b 4\r\n", encoding="utf-8", newline=""
        )
        self.write_plan([quoted(script) + ' "two words"', quoted(script) + ' "50%"'])
        done = self.run_gates()
        self.assertEqual(done.returncode, 0, done.stderr)
        text = self.checks(1)
        rows = self.rows(text)
        self.assertEqual(len(rows), 2)
        self.assertRegex(rows[0], r"^\| 1 \| .* \| FAIL \| 4 \|")
        self.assertRegex(rows[1], r"^\| 2 \| .* \| ERROR \| - \|")
        self.assertIn("[two words]", text)
        self.assertIn("refused:", text)
        self.assertIn("'%'", text)


if __name__ == "__main__":
    unittest.main()
