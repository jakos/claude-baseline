# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/verify.py, the CI entry point for the shared check list.

Run: uv run --script plugins/task-flow/tests/test_ci.py

Every fixture is a temporary root — a `.claude/task-flow.release.toml`, a
`.claude/task-flow.md` and a `plugins/task-flow/tests/` folder written by the test — so
no real task folder, this repository's git state, a model or a login is involved. The
subprocess calls are either real `sys.executable -c ...` checks or an injected fake
`popen` factory; the Windows shim path is exercised through `popen_arg`'s `windows`
override, so the test runs unchanged on Linux, macOS and Windows.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import verify

ROOT = Path(__file__).resolve().parents[3]
PY = sys.executable
TEST_A = "plugins/task-flow/tests/test_a.py"
TEST_B = "plugins/task-flow/tests/test_b.py"


def release_toml(checks: list[list[str]], target: str = "task-flow") -> str:
    """A minimal but strictly valid release config with `checks` as its check list."""
    entries = ",\n".join(
        "  [" + ", ".join(json.dumps(arg) for arg in check) + "]" for check in checks
    )
    return (
        f"[targets.{target}]\n"
        f'tag = "{target}-{{version}}"\n'
        'changelog = "CHANGELOG.md"\n'
        'scope = ["plugins/task-flow"]\n'
        "checks = [\n"
        f"{entries}\n"
        "]\n"
        f"[[targets.{target}.version_files]]\n"
        'path = "plugin.json"\n'
        'format = "json"\n'
        'selector = "version"\n'
    )


def gate_line(argv: list[str]) -> str:
    """One gate-block line for `argv`: every argument quoted, as the shared splitter
    (gates.split_command) refuses shell characters such as ( ) & outside quotes."""
    return " ".join(f'"{arg}"' for arg in argv)


def gate_md(lines: list[str]) -> str:
    return (
        "# task-flow configuration\n\n"
        "## Verification commands\n\n"
        "### python, docs, config, generic\n"
        "```\n" + "\n".join(lines) + "\n```\n"
    )


def make_root(
    base: Path,
    checks: list[list[str]],
    tests: tuple[str, ...] = ("test_a.py",),
    gate: list[str] | None = None,
    config_text: str | None = None,
    gate_text: str | None = None,
) -> Path:
    """A temporary repository root whose two lists hold `checks` in order."""
    (base / ".claude").mkdir(parents=True, exist_ok=True)
    (base / ".claude" / "task-flow.release.toml").write_text(
        release_toml(checks) if config_text is None else config_text, encoding="utf-8"
    )
    lines = [gate_line(check) for check in checks] if gate is None else gate
    (base / ".claude" / "task-flow.md").write_text(
        gate_md(lines) if gate_text is None else gate_text, encoding="utf-8"
    )
    tests_dir = base / "plugins" / "task-flow" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    for name in tests:
        (tests_dir / name).write_text("# stub\n", encoding="utf-8")
    return base


def run_main(root: Path, *extra: str) -> tuple[int, str, str]:
    """verify.main(["--root", root, *extra]) with captured stdout and stderr."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = verify.main(["--root", str(root), *extra])
    return code, out.getvalue(), err.getvalue()


class RealRepoListTest(unittest.TestCase):
    """The repository's own two lists: parity, completeness, pins, no self-reference."""

    def test_shared_lists_agree_and_cover_every_test_once(self):
        checks = verify.load_checks(ROOT, None, "task-flow")
        gate = verify.gate_argvs(ROOT)
        self.assertEqual(verify.parity_problems(checks, gate), [])
        self.assertEqual(verify.test_guard_problems(checks, ROOT), [])
        # task-record-in-git added plugins/task-flow/tests/test_task_record.py to the
        # shared list, so it grew from 21 to 22 checks; task-epics added test_epics.py,
        # so it grew to 23; cli-implementer-driver added test_cli_driver.py, so it grew
        # to 24; external-role-delegation added test_role_run.py and test_role_contracts.py
        # after test_cli_driver.py, so it is 26.
        self.assertEqual(len(checks), 26, "the shared list has 26 checks")
        listed = verify.listed_tests(checks)
        self.assertEqual(len(listed), len(set(listed)), "no duplicate test entry")
        self.assertEqual(sorted(set(listed)), verify.discovered_tests(ROOT))
        self.assertIn("plugins/task-flow/tests/test_ci.py", listed)

    def test_pinned_ruff_and_original_validator_present(self):
        checks = verify.load_checks(ROOT, None, "task-flow")
        ruff = [check for check in checks if check[:2] == ["uvx", "ruff==0.17.3"]]
        self.assertEqual(len(ruff), 2)
        self.assertEqual(ruff[0][2], "check")
        self.assertEqual(ruff[1][2:4], ["format", "--check"])
        self.assertIn(["claude", "plugin", "validate", "."], checks)
        for check in checks:
            self.assertNotIn(verify.SELF_PATH, check, "verify.py must not list itself")


class FixtureRunTest(unittest.TestCase):
    """End-to-end runs of verify.main against temporary roots."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def case(self, name: str) -> Path:
        return self.base / name

    def test_success_returns_zero(self):
        root = make_root(self.case("ok"), [[PY, "-c", "print('fine')"]], tests=())
        code, out, err = run_main(root)
        self.assertEqual(code, 0, err)
        self.assertIn(f"PASS {PY} -c print('fine')", out)
        self.assertIn("1 passed, 0 failed of 1 checks", out)

    def test_failing_check_continues_and_exits_nonzero(self):
        checks = [
            [PY, "-c", "import sys; sys.exit(3)"],
            [PY, "-c", "print('second ran')"],
        ]
        root = make_root(self.case("fail"), checks, tests=())
        code, out, _ = run_main(root)
        self.assertEqual(
            code, 1, "a failed check must fail the entry point, not just report"
        )
        self.assertIn("FAIL", out)
        self.assertIn("exit 3", out)
        self.assertIn("PASS", out)
        self.assertLess(
            out.index("FAIL"), out.index("PASS"), "the run continues after a failure"
        )
        self.assertIn("1 passed, 1 failed of 2 checks", out)

    def test_missing_executable_is_notrun_and_fails(self):
        root = make_root(
            self.case("missing"), [["no-such-tool-verify-ci", "--flag"]], tests=()
        )
        code, out, _ = run_main(root)
        self.assertEqual(code, 1)
        self.assertIn("NOTRUN no-such-tool-verify-ci --flag", out)
        self.assertIn("program not found", out)

    def test_denied_command_fails_without_starting(self):
        root = make_root(self.case("denied"), [["git", "commit", "-m", "x"]], tests=())
        code, out, _ = run_main(root)
        self.assertEqual(code, 1)
        self.assertIn("ERROR git commit -m x", out)
        self.assertIn("denied:", out)

    def test_timeout_kills_the_check_and_fails(self):
        checks = [[PY, "-c", "import time; time.sleep(60)"]]
        root = make_root(self.case("timeout"), checks, tests=())
        code, out, err = run_main(root, "--timeout-seconds", "1")
        self.assertEqual(code, 1, err)
        self.assertIn("ERROR", out)
        self.assertIn("timed out after 1 s", out)

    def test_startup_error_is_reported(self):
        def boom(*args, **kwargs):
            raise OSError("boom")

        root = make_root(self.case("start"), [[PY, "-c", "pass"]], tests=())
        out = io.StringIO()
        with redirect_stdout(out):
            results = verify.run_all(
                [[PY, "-c", "pass"]], root, os.environ, 30.0, popen=boom
            )
        self.assertEqual(results[0]["status"], "ERROR")
        self.assertIn("cannot start: boom", results[0]["detail"][0])
        self.assertIn("ERROR", out.getvalue())


class GuardTest(unittest.TestCase):
    """The pre-execution guard: parity, discovery, duplicates, staleness, self-reference."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def case(self, name: str) -> Path:
        return self.base / name

    def test_parity_mismatch_refused_before_any_run(self):
        checks = [[PY, "-c", "print('a')"]]
        root = make_root(
            self.case("parity"), checks, gate=[gate_line([PY, "-c", "print('b')"])]
        )
        code, out, err = run_main(root)
        self.assertEqual(code, 2)
        self.assertIn("entry 1 differs", err)
        self.assertNotIn("PASS", out, "no check may run on a list disagreement")

    def test_list_length_mismatch_refused(self):
        checks = [[PY, "-c", "pass"]]
        root = make_root(
            self.case("length"),
            checks,
            gate=[gate_line(checks[0]), gate_line([PY, "-c", "pass"])],
        )
        code, _, err = run_main(root)
        self.assertEqual(code, 2)
        self.assertIn("has 1 entries but .claude/task-flow.md has 2", err)

    def test_newly_discovered_unlisted_test_refused(self):
        checks = [["uv", "run", "--script", TEST_A]]
        root = make_root(self.case("new"), checks, tests=("test_a.py", "test_b.py"))
        code, out, err = run_main(root)
        self.assertEqual(code, 2)
        self.assertIn(f"discovered but listed in neither list: {TEST_B}", err)
        self.assertNotIn("PASS", out)

    def test_stale_entry_refused(self):
        checks = [["uv", "run", "--script", "plugins/task-flow/tests/test_gone.py"]]
        root = make_root(self.case("stale"), checks)
        code, _, err = run_main(root)
        self.assertEqual(code, 2)
        self.assertIn("(stale): plugins/task-flow/tests/test_gone.py", err)
        self.assertIn(TEST_A, err, "the present test is reported missing too")

    def test_duplicate_entry_refused(self):
        checks = [["uv", "run", "--script", TEST_A], ["uv", "run", "--script", TEST_A]]
        root = make_root(self.case("dup"), checks)
        code, _, err = run_main(root)
        self.assertEqual(code, 2)
        self.assertIn(f"duplicate test entry: {TEST_A}", err)

    def test_self_reference_refused(self):
        checks = [["uv", "run", "--script", verify.SELF_PATH]]
        root = make_root(self.case("self"), checks)
        code, out, err = run_main(root)
        self.assertEqual(code, 2)
        self.assertIn("must not run verify.py itself", err)
        self.assertNotIn("PASS", out)


class ConfigTest(unittest.TestCase):
    """Missing or malformed configuration is a refusal, never a silent pass."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def case(self, name: str) -> Path:
        return self.base / name

    def test_missing_release_config(self):
        root = make_root(self.case("noconfig"), [[PY, "-c", "pass"]])
        (root / ".claude" / "task-flow.release.toml").unlink()
        code, _, err = run_main(root)
        self.assertEqual(code, 2)
        self.assertIn("no release config", err)

    def test_malformed_toml(self):
        root = make_root(
            self.case("badtoml"), [[PY, "-c", "pass"]], config_text="not [valid toml"
        )
        code, _, err = run_main(root)
        self.assertEqual(code, 2)
        self.assertIn("invalid TOML", err)

    def test_unknown_key_refused(self):
        root = make_root(
            self.case("unknown"),
            [[PY, "-c", "pass"]],
            config_text="unknown = 1\n" + release_toml([[PY, "-c", "pass"]]),
        )
        code, _, err = run_main(root)
        self.assertEqual(code, 2)
        self.assertIn("unknown top-level key", err)

    def test_missing_gate_block_refused(self):
        root = make_root(
            self.case("nogate"),
            [[PY, "-c", "pass"]],
            gate_text="# task-flow configuration\n\n## Architecture rules\n\n- none\n",
        )
        code, _, err = run_main(root)
        self.assertEqual(code, 2)
        self.assertIn("no Verification commands block", err)

    def test_shell_metachar_in_gate_line_refused(self):
        root = make_root(self.case("metachar"), [[PY, "-c", "pass"]], gate=["echo a|b"])
        code, _, err = run_main(root)
        self.assertEqual(code, 2)
        self.assertIn("refused command line", err)

    def test_bad_timeout_refused(self):
        root = make_root(self.case("timeout0"), [[PY, "-c", "pass"]])
        code, _, err = run_main(root, "--timeout-seconds", "0")
        self.assertEqual(code, 2)
        self.assertIn("--timeout-seconds must be > 0", err)


class PopenArgTest(unittest.TestCase):
    """Program resolution and the Windows .cmd/.bat shim, including paths with spaces."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def make_tool(self) -> Path:
        tool = self.dir / "tool dir" / "my tool.cmd"
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_text("@echo off\r\n", encoding="utf-8", newline="")
        return tool

    def test_windows_cmd_shim_line_with_spaces(self):
        tool = self.make_tool()
        env = {"SYSTEMROOT": r"C:\Win"}
        argv = [str(tool), "arg with spaces", "--flag=x"]
        line = verify.popen_arg(argv, self.dir, env, windows=True)
        self.assertEqual(
            line,
            f'"C:\\Win\\System32\\cmd.exe" /d /s /c ""{tool}" "arg with spaces" --flag=x"',
        )

    def test_windows_metacharacter_refused(self):
        tool = self.make_tool()
        with self.assertRaises(ValueError) as ctx:
            verify.popen_arg([str(tool), "a&b"], self.dir, {}, windows=True)
        self.assertIn("&", str(ctx.exception))

    def test_non_windows_returns_argument_list(self):
        tool = self.make_tool()
        self.assertEqual(
            verify.popen_arg([str(tool), "x"], self.dir, {}, windows=False),
            [str(tool), "x"],
        )

    def test_missing_program_returns_none(self):
        self.assertIsNone(
            verify.popen_arg(["no-such-tool-verify-ci"], self.dir, {}, windows=False)
        )
        self.assertIsNone(
            verify.popen_arg(["no-such-tool-verify-ci"], self.dir, {}, windows=True)
        )


if __name__ == "__main__":
    unittest.main()
