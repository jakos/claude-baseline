# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/task_record.py: temp .work/<id> fixtures, fake runners.

Run: uv run --script plugins/task-flow/tests/test_task_record.py

Never touches this repository's git state, real .work tasks, models or login: the
CLI runs against temporary repositories and worktrees, `git interpret-trailers
--parse` gets a fixture message on stdin (no commit, no repository), and the
attribution hook plugins/dev-baseline/scripts/block_attribution.py is invoked
through its JSON hook interface on synthetic commands.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "plugins" / "task-flow" / "scripts" / "task_record.py"
BLOCK_ATTRIBUTION = (
    ROOT / "plugins" / "dev-baseline" / "scripts" / "block_attribution.py"
)
TASK_ID = "demo-task"

PLAN = """# Plan: Demo task
## Goal
Demo goal.
## Context
Some context.
## Profiles
python: plugins/demo.py
## Steps
1. Do it.
## Acceptance criteria
AC1: First criterion. Verify: test_one
AC2: Second criterion with a | pipe, unicode — ünïcødé ✓ and a
   continuation line. Verify: manual: look at it
AC10: Tenth criterion. Verify: test_ten
## Verification commands
```
uv run --script plugins/demo.py
```
## Project rules
None.
## Out of scope
Nothing.
## Risks / open questions
None.
"""

SUMMARY = """# Summary: demo-task
## Goal
Make the demo work end to end.
## Changes
- plugins/demo.py
## Verification
All checks pass.
## Rounds
Two rounds.
## Out of scope
- nothing extra
"""


def status_text(round_n=2, approved="yes (user, 2026-11-02)", task_id=TASK_ID):
    return f"""# Status: {task_id}
- phase: review-approved
- round: {round_n}
- base: main
- branch: task/{task_id}
- worktree: .work/{task_id}/worktree
- plan-approved: {approved}
- last-verdict: APPROVED
## History
- 2026-11-02T10:00:00Z new — demo
## Log
"""


def review_text(n, verdict, results):
    rows = "\n".join(
        f"| {acid} | {result} | evidence for {acid} |"
        for acid, result in results.items()
    )
    return f"""# Review: round {n}
## Acceptance criteria
| AC | Result | Evidence |
| --- | --- | --- |
{rows}
## Pre-existing failures
none
## Findings
1. [suggestion] something
## Scope
none
## Verdict: {verdict}
## Fix list
1. none
"""


IMPL_EXTERNAL = {
    "profile": "mistral-large",
    "model": "mistral-large-4-0",
    "fallback_chain": ["mistral-small"],
    "status": "ok",
}
IMPL_CLAUDE = {"kind": "claude", "profile": "claude", "model": "opus"}


def load_module():
    spec = importlib.util.spec_from_file_location("task_record", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TaskRecordTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mod = load_module()

    # ------------------------------------------------------------- fixtures

    def make_dir_link(self, link: Path, target: Path) -> None:
        """Symlink the directory, or a Windows junction when symlinks need privileges."""
        try:
            link.symlink_to(target, target_is_directory=True)
            return
        except OSError:
            if sys.platform == "win32":
                import _winapi

                _winapi.CreateJunction(str(target), str(link))
                return
            raise

    def fresh(self, task_id=TASK_ID):
        """A fresh temp repository with .work/<id>/worktree and records enabled."""
        self._fresh_counter = getattr(self, "_fresh_counter", 0) + 1
        repo = self.root / f"repo-{self._fresh_counter}-{task_id}" / "repo"
        task = repo / ".work" / task_id
        worktree = task / "worktree"
        worktree.mkdir(parents=True)
        (repo / ".work" / ".gitignore").write_text("*\n", encoding="utf-8")
        (worktree / ".claude").mkdir(parents=True)
        (worktree / ".claude" / "task-flow.records.toml").write_text(
            "[records]\nenabled = true\n", encoding="utf-8"
        )
        return repo, task, worktree

    def write_task(self, task, rounds=2, approved="yes (user, 2026-11-02)"):
        task.mkdir(parents=True, exist_ok=True)
        (task / "status.md").write_text(
            status_text(round_n=rounds, approved=approved), encoding="utf-8"
        )
        (task / "plan.md").write_text(PLAN, encoding="utf-8")
        (task / "summary.md").write_text(SUMMARY, encoding="utf-8")
        if rounds >= 1:
            (task / "review-1.md").write_text(
                review_text(
                    1,
                    "CHANGES_REQUESTED",
                    {"AC1": "PASS", "AC2": "FAIL", "AC10": "PASS"},
                ),
                encoding="utf-8",
            )
            (task / "impl-1.json").write_text(
                json.dumps(IMPL_EXTERNAL), encoding="utf-8"
            )
        if rounds >= 2:
            (task / "review-2.md").write_text(
                review_text(
                    2, "APPROVED", {"AC1": "PASS", "AC2": "PASS", "AC10": "PASS"}
                ),
                encoding="utf-8",
            )
            (task / "impl-2.json").write_text(json.dumps(IMPL_CLAUDE), encoding="utf-8")

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, check=False
        )

    def cli(self, command, repo, task, worktree):
        return self.run_cli(
            command,
            "--repo",
            str(repo),
            "--task-dir",
            str(task),
            "--worktree",
            str(worktree),
        )

    def record_path(self, worktree, task_id=TASK_ID):
        return worktree / "docs" / "tasks" / f"{task_id}.md"

    # ------------------------------------------------------ AC1: disabled

    def test_disabled_is_noop(self):
        repo, task, worktree = self.fresh()
        self.write_task(task)
        # no config at all
        (worktree / ".claude" / "task-flow.records.toml").unlink()
        result = self.cli("render", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"disabled", result.stdout)
        self.assertFalse((worktree / "docs").exists())
        result = self.cli("trailers", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")
        # explicit enabled = false
        (worktree / ".claude" / "task-flow.records.toml").write_text(
            "[records]\nenabled = false\n", encoding="utf-8"
        )
        result = self.cli("render", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"disabled", result.stdout)
        self.assertFalse((worktree / "docs").exists())
        result = self.cli("trailers", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")

    def test_disabled_needs_no_artifacts(self):
        repo, task, worktree = self.fresh()
        task.mkdir(parents=True, exist_ok=True)
        (task / "status.md").write_text(status_text(), encoding="utf-8")
        # no plan.md, summary.md or review files at all
        (worktree / ".claude" / "task-flow.records.toml").unlink()
        result = self.cli("render", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((worktree / "docs").exists())

    def test_strict_config(self):
        cases = [
            ('[records]\nenabled = "yes"\n', "enabled"),
            ("[records]\nextra = true\nenabled = true\n", "extra"),
            ("[other]\nenabled = true\n", "other"),
            ("[records]\n", "enabled"),
            ("enabled = \n", "TOML"),
            ("[records]\nenabled = 1\n", "enabled"),
        ]
        for text, needle in cases:
            with self.subTest(text=text):
                repo, task, worktree = self.fresh()
                self.write_task(task)
                (worktree / ".claude" / "task-flow.records.toml").write_text(
                    text, encoding="utf-8"
                )
                result = self.cli("render", repo, task, worktree)
                self.assertEqual(result.returncode, 2)
                stderr = result.stderr.decode("utf-8")
                self.assertIn("task_record:", stderr)
                self.assertIn(needle, stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertFalse((worktree / "docs").exists())

    # ------------------------------------------------------ AC2: record

    def test_record_contents(self):
        repo, task, worktree = self.fresh()
        self.write_task(task)
        result = self.cli("render", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = self.record_path(worktree)
        self.assertTrue(dest.is_file())
        self.assertEqual(
            sorted(p.name for p in (worktree / "docs" / "tasks").iterdir()),
            [f"{TASK_ID}.md"],
        )
        text = dest.read_text(encoding="utf-8")
        self.assertIn(f"# Task record: {TASK_ID}", text)
        self.assertIn("- schema: task-record/1", text)
        self.assertIn("- approval: user", text)
        self.assertIn("- rounds: 2", text)
        self.assertIn("- final-verdict: APPROVED", text)
        self.assertIn("- risk: unavailable", text)
        self.assertIn("Make the demo work end to end.", text)
        self.assertIn("- AC1: First criterion. — PASS", text)
        self.assertIn("AC2: Second criterion with a | pipe, unicode — ünïcødé ✓", text)
        self.assertIn("continuation line. — PASS", text)
        self.assertIn("- AC10: Tenth criterion. — PASS", text)
        self.assertIn(
            "- round 1: CHANGES_REQUESTED — implementer mistral-large/mistral-large-4-0",
            text,
        )
        self.assertIn("- round 2: APPROVED — implementer claude/opus", text)
        self.assertIn("## Out of scope", text)
        self.assertIn("- nothing extra", text)
        self.assertNotIn(str(self.root), text, "no absolute paths in the record")

    def test_deterministic_bounded_record(self):
        repo, task, worktree = self.fresh()
        self.write_task(task)
        first = self.cli("render", repo, task, worktree)
        self.assertEqual(first.returncode, 0, first.stderr)
        data = self.record_path(worktree).read_bytes()
        self.assertNotIn(b"\r", data)
        data.decode("utf-8")
        self.assertLessEqual(len(data), self.mod.MAX_RECORD_BYTES)
        second = self.cli("render", repo, task, worktree)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn(b"unchanged", second.stdout)
        self.assertEqual(self.record_path(worktree).read_bytes(), data)
        # a long goal is truncated visibly at the field limit
        long_summary = SUMMARY.replace(
            "Make the demo work end to end.", "G" + "o" * 2000
        )
        (task / "summary.md").write_text(long_summary, encoding="utf-8")
        third = self.cli("render", repo, task, worktree)
        # the existing record conflicts now — regenerate into a fresh worktree
        self.assertEqual(third.returncode, 2)
        repo2, task2, worktree2 = self.fresh("demo-two")
        self.write_task(task2)
        (task2 / "summary.md").write_text(
            long_summary.replace("demo-task", "demo-two"), encoding="utf-8"
        )
        (task2 / "status.md").write_text(
            status_text(task_id="demo-two"), encoding="utf-8"
        )
        result = self.cli("render", repo2, task2, worktree2)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.record_path(worktree2, "demo-two").read_text(encoding="utf-8")
        self.assertIn(self.mod.TRUNCATED, text)
        goal_line = next(line for line in text.splitlines() if line.startswith("G"))
        self.assertLessEqual(len(goal_line), self.mod.MAX_GOAL_CHARS + 1)

    # ------------------------------------------------------ AC3: trailers

    def git_trailers(self, message):
        git = shutil.which("git")
        if git is None:
            self.skipTest("git not on PATH")
        result = subprocess.run(
            [git, "interpret-trailers", "--parse"],
            input=message.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode("utf-8")

    def test_trailers_parse(self):
        repo, task, worktree = self.fresh()
        self.write_task(task)
        result = self.cli("trailers", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        block = result.stdout.decode("utf-8")
        self.assertTrue(block.endswith("\n"))
        message = "feat: demo task\n\nbody text\n\n" + block
        parsed = self.git_trailers(message)
        entries: dict[str, list[str]] = {}
        for line in parsed.splitlines():
            key, _, value = line.partition(": ")
            entries.setdefault(key, []).append(value)
        self.assertEqual(entries["Task-Id"], [TASK_ID])
        self.assertEqual(entries["Task-Rounds"], ["2"])
        self.assertEqual(
            entries["Task-Implementer"],
            ["mistral-large/mistral-large-4-0", "claude/opus"],
        )
        self.assertEqual(entries["Task-Plan-Approved"], ["user"])
        self.assertNotIn("Task-Risk", entries)
        # --auto maps to auto
        repo2, task2, worktree2 = self.fresh("demo-auto")
        self.write_task(task2, approved="yes (--auto, 2026-11-02)")
        (task2 / "status.md").write_text(
            status_text(approved="yes (--auto, 2026-11-02)", task_id="demo-auto"),
            encoding="utf-8",
        )
        result = self.cli("trailers", repo2, task2, worktree2)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = self.git_trailers("s\n\nb\n\n" + result.stdout.decode("utf-8"))
        self.assertIn("Task-Plan-Approved: auto", parsed.splitlines())

    def test_trailer_injection_rejected(self):
        repo, task, worktree = self.fresh()
        self.write_task(task)
        (task / "impl-2.json").write_text(
            json.dumps({"profile": "claude", "model": "opus\nInjected: yes"}),
            encoding="utf-8",
        )
        for command in ("trailers", "render"):
            result = self.cli(command, repo, task, worktree)
            self.assertEqual(result.returncode, 2, command)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"task_record:", result.stderr)
            self.assertNotIn(b"Traceback", result.stderr)
        self.assertFalse((worktree / "docs").exists())
        # a control character in exported prose fails closed too
        repo2, task2, worktree2 = self.fresh("demo-ctrl")
        self.write_task(task2)
        (task2 / "summary.md").write_text(
            SUMMARY.replace("end to end.", "end to end.\x00"), encoding="utf-8"
        )
        result = self.cli("render", repo2, task2, worktree2)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertFalse((worktree2 / "docs").exists())

    # ------------------------------------------------------ AC4: implementers

    def test_implementer_metadata(self):
        repo, task, worktree = self.fresh("demo-three")
        self.write_task(task, rounds=3)
        (task / "status.md").write_text(
            status_text(round_n=3, task_id="demo-three"), encoding="utf-8"
        )
        (task / "review-3.md").write_text(
            review_text(3, "APPROVED", {"AC1": "PASS", "AC2": "PASS", "AC10": "PASS"}),
            encoding="utf-8",
        )
        # round 1: external profile that actually ran after a fallback
        (task / "impl-1.json").write_text(
            json.dumps(
                {
                    "profile": "mistral-large",
                    "model": "mistral-large-4-0",
                    "fallback_chain": ["mistral-small", "mistral-large"],
                    "status": "ok",
                }
            ),
            encoding="utf-8",
        )
        # round 2: legacy round without impl-2.json -> unknown, never guessed
        (task / "impl-2.json").unlink()
        # round 3: Claude execution metadata written at implement time
        (task / "impl-3.json").write_text(
            json.dumps({"kind": "claude", "profile": "claude", "model": "sonnet"}),
            encoding="utf-8",
        )
        result = self.cli("render", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.record_path(worktree, "demo-three").read_text(encoding="utf-8")
        self.assertIn(
            "round 1: CHANGES_REQUESTED — implementer mistral-large/mistral-large-4-0",
            text,
        )
        self.assertNotIn("mistral-small/mistral-small", text)
        self.assertIn("round 2: APPROVED — implementer unknown/unknown", text)
        self.assertIn("round 3: APPROVED — implementer claude/sonnet", text)
        result = self.cli("trailers", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.decode("utf-8").splitlines()
        self.assertEqual(
            [line for line in lines if line.startswith("Task-Implementer:")],
            [
                "Task-Implementer: mistral-large/mistral-large-4-0",
                "Task-Implementer: unknown/unknown",
                "Task-Implementer: claude/sonnet",
            ],
        )
        # fallback_chain only (no profile key): the last chain entry is the actual one
        repo2, task2, worktree2 = self.fresh("demo-chain")
        self.write_task(task2, rounds=1)
        (task2 / "status.md").write_text(
            status_text(round_n=1, task_id="demo-chain"), encoding="utf-8"
        )
        (task2 / "review-1.md").write_text(
            review_text(1, "APPROVED", {"AC1": "PASS", "AC2": "PASS", "AC10": "PASS"}),
            encoding="utf-8",
        )
        (task2 / "impl-1.json").write_text(
            json.dumps(
                {"fallback_chain": ["mistral-small", "glm-flash"], "status": "ok"}
            ),
            encoding="utf-8",
        )
        result = self.cli("trailers", repo2, task2, worktree2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"Task-Implementer: glm-flash/unknown", result.stdout)

    # ------------------------------------------------------ AC5: scrubbing

    def test_no_personal_paths_or_secrets(self):
        nasty = (
            "Goal: read C:\\Users\\jakub\\secret\\key.pem and "
            "https://user:hunter2@example.com/api with sk-abcdefghijklmnop123456, "
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWX, AKIAIOSFODNN7EXAMPLE, "
            'api_key = "topsecret" and password: hunter2 and '
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK\n-----END RSA PRIVATE KEY----- "
            "and /home/jakub/.ssh/id_rsa and ~/.claude/settings.json and "
            "\\\\server\\share\\file and /tmp/build-123 and /var/log/syslog and "
            "Claude-Session: abc123 and see https://claude.ai/code/session/abc and "
            "Generated with Claude Code and "
            "Co-Authored-By: Claude <bot@anthropic.com> and xoxb-1234567890abcdef"
        )
        repo, task, worktree = self.fresh()
        self.write_task(task)
        (task / "summary.md").write_text(
            SUMMARY.replace("Make the demo work end to end.", nasty), encoding="utf-8"
        )
        result = self.cli("render", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.record_path(worktree).read_text(encoding="utf-8")
        for secret in (
            "jakub",
            "hunter2",
            "sk-abcdefghijklmnop123456",
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWX",
            "AKIAIOSFODNN7EXAMPLE",
            "topsecret",
            "MIIEowIBAAK",
            "BEGIN RSA PRIVATE KEY",
            "server",
            "abc123",
            "claude.ai/code/session",
            "xoxb-1234567890abcdef",
            "bot@anthropic.com",
            "/tmp/build-123",
            "/var/log/syslog",
            "C:\\Users",
            "/home/",
            "~/.claude",
        ):
            self.assertNotIn(secret, text, f"record leaked {secret!r}")
        self.assertIn("<redacted>", text)
        self.assertIn("https://<redacted>@example.com/api", text)
        # diagnostics never echo the sensitive value
        repo2, task2, worktree2 = self.fresh("demo-diag")
        self.write_task(task2)
        (task2 / "plan.md").write_text(
            PLAN.replace(
                "AC1: First criterion. Verify: test_one",
                "AC1: bad \x00 sk-abcdefghijklmnop123456. Verify: test_one",
            ),
            encoding="utf-8",
        )
        result = self.cli("render", repo2, task2, worktree2)
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertNotIn("sk-abcdefghijklmnop123456", stderr)
        self.assertNotIn("\x00", stderr)
        self.assertIn("task_record:", stderr)
        self.assertFalse((worktree2 / "docs").exists())

    def test_scrub_unit(self):
        mod = self.mod
        self.assertEqual(mod.scrub("see C:\\Users\\x\\y now"), "see <redacted> now")
        self.assertEqual(
            mod.scrub("u https://a:b@h.example.com/x v"),
            "u https://<redacted>@h.example.com/x v",
        )
        self.assertEqual(mod.scrub("k sk-abcdefghijklmnop"), "k <redacted>")
        self.assertEqual(mod.scrub("t api_key: abc123"), "t api_key: <redacted>")
        self.assertEqual(mod.scrub("p /home/u/z"), "p <redacted>")
        self.assertEqual(mod.scrub("h ~/.claude/x"), "h <redacted>")
        self.assertEqual(mod.scrub("n \\\\srv\\share"), "n <redacted>")
        self.assertEqual(
            mod.scrub(
                "-----BEGIN OPENSSH PRIVATE KEY-----\nx\n-----END OPENSSH PRIVATE KEY-----"
            ),
            "<redacted>",
        )
        self.assertEqual(mod.scrub("Generated with Claude Code"), "<redacted>")
        with self.assertRaises(mod.RecordError):
            mod.clean_field("a\x00b", "unit")

    # ------------------------------------------------------ AC6: attribution

    def run_hook(self, command):
        payload = json.dumps({"tool_input": {"command": command}}).encode("utf-8")
        return subprocess.run(
            [sys.executable, str(BLOCK_ATTRIBUTION)],
            input=payload,
            capture_output=True,
            check=False,
        )

    def test_task_trailers_not_blocked(self):
        repo, task, worktree = self.fresh()
        self.write_task(task)
        result = self.cli("trailers", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        block = result.stdout.decode("utf-8").strip()
        self.assertIn("Task-Implementer: claude/opus", block)
        commands = [
            f'git commit -m "feat: demo" -m "{block}"',
            f'gh pr create --title t --body "{block}"',
            'git commit -m "x" -m "Task-Implementer: claude/claude-opus-4-5"',
        ]
        for command in commands:
            with self.subTest(command=command):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(
                    result.stdout, b"", "the hook must allow Task-* trailers"
                )

    def test_attribution_still_blocked(self):
        denied = [
            'git commit -m "x" -m "Co-Authored-By: Claude <bot@anthropic.com>"',
            'git commit -m "Claude-Session: abc123"',
            'git commit -m "see https://claude.ai/code/session/abc"',
            'git commit -m "Generated with Claude Code"',
            'gh pr create --title t --body "Co-Authored-By: Anthropic"',
        ]
        for command in denied:
            with self.subTest(command=command):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 0)
                payload = json.loads(result.stdout.decode("utf-8"))
                self.assertEqual(
                    payload["hookSpecificOutput"]["permissionDecision"], "deny"
                )
        # a command that creates no history is not refused
        result = self.run_hook('git grep -n "Co-Authored-By: Claude" -- x')
        self.assertEqual(result.stdout, b"")

    # ------------------------------------------------------ AC7: refusals

    def test_refuse_unsafe_destination(self):
        mod = self.mod
        repo, task, worktree = self.fresh()
        self.write_task(task)
        # invalid task id (uppercase/underscore) in status and directory name
        bad = repo / ".work" / "Demo_Task"
        bad.mkdir()
        (bad / "status.md").write_text(
            status_text(task_id="Demo_Task"), encoding="utf-8"
        )
        result = self.cli("render", repo, bad, worktree)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"task_record:", result.stderr)
        self.assertFalse((worktree / "docs").exists())
        # traversal-shaped ids are refused by the pure helper too
        for evil in ("../evil", "..", "a/b", "demo task"):
            with self.assertRaises(mod.RecordError):
                mod.safe_destination(worktree, evil)
        # symlink escape: docs/tasks points outside the worktree
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (worktree / "docs").mkdir()
            self.make_dir_link(worktree / "docs" / "tasks", outside)
        except OSError as exc:
            self.skipTest(f"cannot create directory links: {exc}")
        result = self.cli("render", repo, task, worktree)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"symlink", result.stderr)
        self.assertEqual(list(outside.iterdir()), [])
        # a symlinked record file is refused as well
        repo2, task2, worktree2 = self.fresh()
        self.write_task(task2)
        outside_file = self.root / "outside-record.md"
        outside_file.write_text("keep me\n", encoding="utf-8")
        (worktree2 / "docs" / "tasks").mkdir(parents=True)
        try:
            (worktree2 / "docs" / "tasks" / f"{TASK_ID}.md").symlink_to(outside_file)
        except OSError as exc:
            if sys.platform != "win32":
                self.skipTest(f"cannot create symlinks: {exc}")
            self.skipTest("cannot create file symlinks on this Windows setup")
        result = self.cli("render", repo2, task2, worktree2)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(outside_file.read_text(encoding="utf-8"), "keep me\n")

    def test_conflicting_record(self):
        repo, task, worktree = self.fresh()
        self.write_task(task)
        result = self.cli("render", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = self.record_path(worktree)
        original = dest.read_bytes()
        # identical regeneration is a no-op
        again = self.cli("render", repo, task, worktree)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn(b"unchanged", again.stdout)
        self.assertEqual(dest.read_bytes(), original)
        # a conflicting existing record is refused, not overwritten
        dest.write_text("# someone else's record\n", encoding="utf-8")
        conflict = self.cli("render", repo, task, worktree)
        self.assertEqual(conflict.returncode, 2)
        self.assertIn(b"conflicting", conflict.stderr)
        self.assertEqual(dest.read_text(encoding="utf-8"), "# someone else's record\n")

    def test_invalid_artifacts_no_partial_write(self):
        mutations = []

        def mutation(fn):
            mutations.append(fn)
            return fn

        @mutation
        def missing_summary(repo, task, worktree):
            (task / "summary.md").unlink()

        @mutation
        def missing_review(repo, task, worktree):
            (task / "review-2.md").unlink()

        @mutation
        def duplicate_ac_row(repo, task, worktree):
            text = (task / "review-2.md").read_text(encoding="utf-8")
            (task / "review-2.md").write_text(
                text.replace(
                    "| AC10 | PASS | evidence for AC10 |",
                    "| AC10 | PASS | evidence for AC10 |\n| AC10 | PASS | again |",
                ),
                encoding="utf-8",
            )

        @mutation
        def missing_ac_row(repo, task, worktree):
            text = (task / "review-2.md").read_text(encoding="utf-8")
            (task / "review-2.md").write_text(
                text.replace("| AC10 | PASS | evidence for AC10 |\n", ""),
                encoding="utf-8",
            )

        @mutation
        def extra_ac_row(repo, task, worktree):
            text = (task / "review-2.md").read_text(encoding="utf-8")
            (task / "review-2.md").write_text(
                text.replace(
                    "## Pre-existing failures",
                    "| AC99 | PASS | nope |\n## Pre-existing failures",
                ),
                encoding="utf-8",
            )

        @mutation
        def bad_result(repo, task, worktree):
            text = (task / "review-2.md").read_text(encoding="utf-8")
            (task / "review-2.md").write_text(
                text.replace("| AC2 | PASS |", "| AC2 | MAYBE |"), encoding="utf-8"
            )

        @mutation
        def unapproved_final(repo, task, worktree):
            text = (task / "review-2.md").read_text(encoding="utf-8")
            (task / "review-2.md").write_text(
                text.replace("## Verdict: APPROVED", "## Verdict: CHANGES_REQUESTED"),
                encoding="utf-8",
            )

        @mutation
        def inconsistent_rounds(repo, task, worktree):
            (task / "review-3.md").write_text(
                review_text(
                    3, "APPROVED", {"AC1": "PASS", "AC2": "PASS", "AC10": "PASS"}
                ),
                encoding="utf-8",
            )

        @mutation
        def plan_without_acs(repo, task, worktree):
            (task / "plan.md").write_text(
                PLAN.replace("AC1: First criterion. Verify: test_one\n", "")
                .replace(
                    "AC2: Second criterion with a | pipe, unicode — ünïcødé ✓ and a\n   continuation line. Verify: manual: look at it\n",
                    "",
                )
                .replace("AC10: Tenth criterion. Verify: test_ten\n", ""),
                encoding="utf-8",
            )

        @mutation
        def summary_without_goal(repo, task, worktree):
            (task / "summary.md").write_text(
                SUMMARY.replace("## Goal\nMake the demo work end to end.\n", ""),
                encoding="utf-8",
            )

        @mutation
        def not_approved(repo, task, worktree):
            (task / "status.md").write_text(
                status_text(approved="no"), encoding="utf-8"
            )

        @mutation
        def bad_impl_json(repo, task, worktree):
            (task / "impl-1.json").write_text("{not json", encoding="utf-8")

        @mutation
        def round_zero(repo, task, worktree):
            (task / "status.md").write_text(status_text(round_n=0), encoding="utf-8")

        for mutate in mutations:
            with self.subTest(mutation=mutate.__name__):
                repo, task, worktree = self.fresh()
                self.write_task(task)
                mutate(repo, task, worktree)
                result = self.cli("render", repo, task, worktree)
                self.assertEqual(result.returncode, 2)
                stderr = result.stderr.decode("utf-8")
                self.assertIn("task_record:", stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertFalse(
                    (worktree / "docs").exists(), "no partial output on refusal"
                )
                # trailers refuses too, without printing a block
                result = self.cli("trailers", repo, task, worktree)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")

    # ------------------------------------------------------ AC8: CLI wiring

    def test_cli_worktree_output_and_failure(self):
        repo, task, worktree = self.fresh()
        self.write_task(task)
        # the record lands in the worktree, never in the repository root
        result = self.cli("render", repo, task, worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.record_path(worktree).is_file())
        self.assertFalse((repo / "docs").exists())
        # --worktree none writes into the repository root instead (config there)
        repo2, task2, worktree2 = self.fresh()
        self.write_task(task2)
        (repo2 / ".claude").mkdir()
        (repo2 / ".claude" / "task-flow.records.toml").write_text(
            "[records]\nenabled = true\n", encoding="utf-8"
        )
        result = self.run_cli(
            "render",
            "--repo",
            str(repo2),
            "--task-dir",
            str(task2),
            "--worktree",
            "none",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((repo2 / "docs" / "tasks" / f"{TASK_ID}.md").is_file())
        self.assertFalse((worktree2 / "docs").exists())
        # a task-dir outside <repo>/.work is refused and writes nothing
        repo5, task5, worktree5 = self.fresh()
        self.write_task(task5)
        stranger = self.root / "elsewhere" / "x"
        stranger.mkdir(parents=True)
        result = self.run_cli(
            "render",
            "--repo",
            str(repo5),
            "--task-dir",
            str(stranger),
            "--worktree",
            str(worktree5),
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse((worktree5 / "docs").exists())
        # a helper failure (missing artifact) leaves no record behind
        repo3, task3, worktree3 = self.fresh()
        self.write_task(task3)
        (task3 / "summary.md").unlink()
        result = self.cli("render", repo3, task3, worktree3)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((worktree3 / "docs").exists())
        # a custom --config is honoured
        repo4, task4, worktree4 = self.fresh()
        self.write_task(task4)
        custom = self.root / "custom-records.toml"
        custom.write_text("[records]\nenabled = true\n", encoding="utf-8")
        (worktree4 / ".claude" / "task-flow.records.toml").write_text(
            "[records]\nenabled = false\n", encoding="utf-8"
        )
        result = self.run_cli(
            "render",
            "--repo",
            str(repo4),
            "--task-dir",
            str(task4),
            "--worktree",
            str(worktree4),
            "--config",
            str(custom),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.record_path(worktree4).is_file())

    # ------------------------------------------------------ AC9: registration

    def test_config_and_gate_registration(self):
        project = ROOT / ".claude" / "task-flow.records.toml"
        template = ROOT / "plugins" / "task-flow" / "templates" / "records.toml"
        self.assertTrue(project.is_file(), f"missing {project}")
        self.assertTrue(template.is_file(), f"missing {template}")
        data = tomllib.loads(project.read_text(encoding="utf-8"))
        self.assertIs(data["records"]["enabled"], True)
        data = tomllib.loads(template.read_text(encoding="utf-8"))
        self.assertIs(data["records"]["enabled"], False)
        gate = (ROOT / ".claude" / "task-flow.md").read_text(encoding="utf-8")
        line = "uv run --script plugins/task-flow/tests/test_task_record.py"
        self.assertEqual(gate.count(line), 1, "the gate list names the new suite once")
        release = tomllib.loads(
            (ROOT / ".claude" / "task-flow.release.toml").read_text(encoding="utf-8")
        )
        entry = ["uv", "run", "--script", "plugins/task-flow/tests/test_task_record.py"]
        checks = release["targets"]["task-flow"]["checks"]
        self.assertEqual(checks.count(entry), 1, "the release list names it once")
        self.assertTrue(SCRIPT.is_file())


if __name__ == "__main__":
    unittest.main()
