# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/rules.py, the denial rules shared by guard.py and implement.py.

Run: uv run --script plugins/task-flow/tests/test_rules.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
GUARD = SCRIPTS / "guard.py"
sys.path.insert(0, str(SCRIPTS))
import rules

ALL_ROLES = ("planner", "implementer", "checker", "reviewer", "backlog")
READ_ONLY_ROLES = ("planner", "reviewer", "backlog")

# Every shell command used in test_guard.py, with the tool it is run through there.
SHELL_CASES = [
    ("Bash", "git show HEAD:README.md"),
    ("Bash", "git diff main --stat"),
    ("Bash", "git diff main -- plugins/task-flow | head -50"),
    ("Bash", "git status"),
    ("Bash", "git ls-files"),
    ("Bash", "git rev-parse --verify task/x"),
    ("Bash", "git blame README.md"),
    ("Bash", "head -n 20 .work/x/status.md"),
    ("Bash", "cat .gitignore; head .work/x/status.md"),
    ("Bash", "git -C /repo log --oneline -5"),
    ("Bash", 'git log --oneline --grep "add"'),
    ("Bash", "git log --grep 'push'"),
    (
        "Bash",
        'git grep -n -e "worktree add" -- plugins/task-flow/skills/workflow/orchestration.md',
    ),
    ("Bash", 'git log -S "git commit" --oneline'),
    ("Bash", 'head -n 20 .work/x/status.md && git grep -n "worktree add" -- .'),
    ("PowerShell", 'git grep -n -e "worktree add" -- file'),
    ("Bash", "git add ."),
    ("Bash", "git -C /repo checkout main"),
    ("Bash", "git worktree add -b task/x .work/x/worktree main"),
    ("Bash", "git worktree remove p"),
    ("Bash", "git branch -D foo"),
    ("Bash", "git stash"),
    ("Bash", "git reset --hard"),
    ("Bash", "cd /repo && git reset --hard"),
    ("Bash", "git status; git add -A"),
    ("Bash", "ls | xargs git rm"),
    ("Bash", "git switch -c task/x"),
    ("Bash", 'git add "unterminated'),
    ("PowerShell", "git.exe restore file"),
    ("Bash", 'git commit -m "x"'),
    ("Bash", "git -c user.name=x commit -m y"),
    ("Bash", "git push origin main"),
    ("Bash", "git status && git push"),
    ("Bash", "gh pr create --fill"),
    ("Bash", "kubectl apply -f m.yaml"),
    ("Bash", "terraform apply"),
    ("Bash", "git log --grep 'commit'"),
    ("Bash", "git log -S 'push'"),
    ("Bash", "kubectl apply --dry-run=client -f m.yaml"),
    ("Bash", "git worktree add p"),
    ("Bash", "habit-snooze --snooze E501 src/a.py"),
    ("Bash", "habit-snooze --prune"),
    ("Bash", "uvx habit-snooze --snooze x"),
    ("Bash", "git status; habit-snooze --prune"),
    ("Bash", "habit-snooze --list"),
]

# Every write path used in test_guard.py, with the role it is tried with there.
WRITE_CASES = [
    ("/repo/src/a.py", "reviewer"),
    ("/repo/.work/x/review-1.md", "reviewer"),
    ("/repo/src/a.py", "implementer"),
    ("/repo/.work/_autopilot/r/backlog.md", "backlog"),
    ("/repo/src/x.py", "backlog"),
    ("/repo/.habit-hooks/snooze.json", "reviewer"),
    ("/repo/.work/x/worktree/.habit-hooks/snooze.json", "checker"),
    ("C:\\repo\\.habit-hooks\\snooze.json", "planner"),
    ("/repo/.HABIT-HOOKS/Snooze.json", "backlog"),
]


def guard_allows(agent_type: str, tool: str, tool_input: dict) -> bool:
    """Run guard.py as a subprocess and return whether it allows the call."""
    payload = {"tool_name": tool, "tool_input": tool_input, "agent_type": agent_type}
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"guard exited {result.returncode}: {result.stderr}")
    if not result.stdout.strip():
        return True
    decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
    return decision == "allow"


class RulesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "worktree"
        self.root.mkdir()

    def test_parity_with_guard_for_claude_roles(self):
        for tool, command in SHELL_CASES:
            for role in ALL_ROLES:
                with self.subTest(tool=tool, role=role, command=command):
                    expected = guard_allows(
                        f"task-flow:{role}", tool, {"command": command}
                    )
                    self.assertEqual(
                        rules.check_command(command, role) is None,
                        expected,
                        command,
                    )
        for path, _role in WRITE_CASES:
            for role in ALL_ROLES:
                with self.subTest(role=role, path=path):
                    expected = guard_allows(
                        f"task-flow:{role}", "Write", {"file_path": path}
                    )
                    self.assertEqual(
                        rules.check_write(path, role) is None,
                        expected,
                        path,
                    )

    def test_reasons_keep_previous_texts(self):
        leaves = (
            "pushing, opening PRs and deploying are not allowed inside task-flow agents. "
            "Nothing leaves the machine until /task-finish asks the user."
        )
        commits = (
            "task-flow agents do not commit. Leave changes staged; /task-finish commits "
            "once review has approved them."
        )
        for role in ALL_ROLES:
            with self.subTest(role=role, rule="leaves"):
                self.assertEqual(
                    rules.check_command("git push origin main", role), leaves
                )
            with self.subTest(role=role, rule="commits"):
                self.assertEqual(
                    rules.check_command('git commit -m "x"', role), commits
                )
        for role in READ_ONLY_ROLES:
            with self.subTest(role=role, rule="mutates-git"):
                self.assertEqual(
                    rules.check_command("git add .", role),
                    f"the {role} is read-only: this git command changes the tree, "
                    "index or branches.",
                )
        for role in ("planner", "checker", "reviewer", "backlog"):
            with self.subTest(role=role, rule="write"):
                self.assertEqual(
                    rules.check_write("/repo/src/a.py", role),
                    f"the {role} may only write inside .work/<task-id>/; "
                    "'/repo/src/a.py' is outside it. Record findings in the task's "
                    ".work files instead of changing the code.",
                )
        self.assertEqual(
            rules.check_command("git push origin main", "external-implementer"),
            "pushing, opening PRs and deploying are not allowed; "
            "nothing leaves the machine until /task-finish",
        )
        self.assertEqual(
            rules.check_command("git commit -m x", "external-implementer"),
            "do not commit; leave changes in the worktree, the script stages them",
        )
        self.assertEqual(
            rules.check_write("../x", "external-implementer", self.root),
            "../x is outside the worktree",
        )
        self.assertEqual(
            rules.check_write(".git/config", "external-implementer", self.root),
            "writing under .git or .work is not allowed (.git/config)",
        )

    def test_snooze_denied_for_every_role(self):
        commands = [
            "habit-snooze --snooze E501 src/a.py",
            "habit-snooze --prune",
            "uvx habit-snooze --snooze x",
            "git status; habit-snooze --prune",
        ]
        writes = [
            "/repo/.habit-hooks/snooze.json",
            "/repo/.work/x/worktree/.habit-hooks/snooze.json",
            "C:\\repo\\.habit-hooks\\snooze.json",
            "/repo/.HABIT-HOOKS/Snooze.json",
        ]
        for role in rules.ROLES:
            for command in commands:
                with self.subTest(role=role, command=command):
                    self.assertEqual(
                        rules.check_command(command, role), rules.SNOOZE_COMMAND
                    )
        for role in ALL_ROLES:
            for path in writes:
                with self.subTest(role=role, path=path):
                    self.assertEqual(rules.check_write(path, role), rules.SNOOZE_WRITE)
        for path in (
            ".habit-hooks/snooze.json",
            "sub/../.habit-hooks/snooze.json",
            ".habit-hooks/snooze.json.",
        ):
            with self.subTest(role="external-implementer", path=path):
                self.assertEqual(
                    rules.check_write(path, "external-implementer", self.root),
                    rules.SNOOZE_WRITE,
                )

    def test_snooze_neighbours_allowed(self):
        for role in rules.ROLES:
            with self.subTest(role=role, command="habit-snooze --list"):
                self.assertIsNone(rules.check_command("habit-snooze --list", role))
            with self.subTest(role=role, command="git log --grep snooze"):
                self.assertIsNone(rules.check_command("git log --grep snooze", role))
        self.assertIsNone(rules.check_write(".habit-hooks/config.toml", "implementer"))
        self.assertIsNone(
            rules.check_write(
                ".habit-hooks/config.toml", "external-implementer", self.root
            )
        )

    def test_quoted_phrase_false_positive_still_pinned(self):
        for command in (
            'git grep -n -e "worktree add"',
            'git log -S "git commit" --oneline',
        ):
            for role in READ_ONLY_ROLES:
                with self.subTest(role=role, command=command):
                    self.assertIsNotNone(rules.check_command(command, role), command)

    def test_external_implementer_commands(self):
        denied = [
            "git commit -m x",
            "git push origin main",
            "git status && git push",
            "gh pr create --fill",
            "kubectl apply -f m.yaml",
            "terraform apply",
        ]
        allowed = [
            "git add .",
            "git status",
            "kubectl apply --dry-run=client -f m.yaml",
            "git log --grep 'commit'",
        ]
        for command in denied:
            with self.subTest(command=command):
                self.assertIsNotNone(
                    rules.check_command(command, "external-implementer"), command
                )
        for command in allowed:
            with self.subTest(command=command):
                self.assertIsNone(
                    rules.check_command(command, "external-implementer"), command
                )

    def test_external_planner_argv_allowlist(self):
        allowed = [
            ["git", "status"],
            ["git", "status", "--short"],
            ["git", "log", "--oneline", "-5"],
            ["git", "ls-files"],
            ["git", "diff", "main", "--stat"],
            ["git", "show", "HEAD:README.md"],
            ["git", "blame", "README.md"],
            ["git", "rev-parse", "--verify", "HEAD"],
            ["git", "grep", "-n", "x", "--", "."],
            ["git", "--no-pager", "log", "-1"],
            ["git", "--version"],
            ["ls", "-la", "plugins"],
            ["rg", "--files", "plugins/task-flow"],
            ["grep", "-rn", "planner", "docs"],
            ["find", ".", "-name", "*.py"],
            ["find", ".", "-name", "*.py", "-print"],
            ["head", "-n", "20", "README.md"],
            ["cat", ".gitignore"],
            ["C:\\tools\\rg.exe", "--version"],
        ]
        denied = [
            (["python", "-c", "print(1)"], "allowlist"),
            (["python3", "-V"], "allowlist"),
            (["node", "-e", "1"], "allowlist"),
            (["powershell", "-Command", "ls"], "allowlist"),
            (["cmd", "/c", "dir"], "allowlist"),
            (["uv", "run", "x.py"], "allowlist"),
            (["uvx", "ruff", "check", "."], "allowlist"),
            (["env", "X=1", "ls"], "allowlist"),
            (["xargs", "rm"], "allowlist"),
            (["sh", "-c", "git push"], "allowlist"),
            (["git", "commit", "-m", "x"], "read-only"),
            (["git", "push", "origin", "main"], None),
            (["git", "add", "."], "read-only"),
            (["git", "checkout", "main"], "read-only"),
            (["git", "-C", "/etc", "log"], "outside the selected repository"),
            (
                ["git", "--work-tree", "/tmp", "status"],
                "outside the selected repository",
            ),
            (
                ["git", "--git-dir=/etc", "log"],
                "outside the selected repository",
            ),
            (["git", "-C/etc", "log"], "outside the selected repository"),
            (["ls", "a>b"], "shell syntax"),
            (["ls", "$(rm -rf /)"], "shell syntax"),
            (["ls", "a|b"], "shell syntax"),
            (["grep", "-r", "x", ".", ">", "out"], "shell syntax"),
            ([], "non-empty argv"),
            ([""], "non-empty argv"),
        ]
        for argv in allowed:
            with self.subTest(argv=argv):
                self.assertIsNone(rules.check_argv(argv, "external-planner"), argv)
        for argv, needle in denied:
            with self.subTest(argv=argv):
                reason = rules.check_argv(argv, "external-planner")
                self.assertIsNotNone(reason, argv)
                if needle:
                    self.assertIn(needle, reason, argv)
        # other roles are not restricted by check_argv
        self.assertIsNone(
            rules.check_argv(["python", "-c", "x"], "external-implementer")
        )
        self.assertIsNone(rules.check_argv(["python", "-c", "x"], "planner"))
        with self.assertRaises(ValueError):
            rules.check_argv(["ls"], "general-purpose")

    def test_external_planner_argv_read_only_holes_closed(self):
        """The allowlisted programs themselves must not mutate (review round 1, finding 1)."""
        denied = [
            (["find", ".", "-delete"], "-delete"),
            (["find", ".", "-exec", "rm", "{}", "+"], "-exec"),
            (["find", ".", "-execdir", "sh", "-c", "id", "{}", ";"], "-execdir"),
            (["find", ".", "-ok", "rm", "{}"], "-ok"),
            (["find", ".", "-okdir", "rm", "{}"], "-okdir"),
            (["find", ".", "-fprint", "x.txt"], "-fprint"),
            (["find", ".", "-fprint0", "x.txt"], "-fprint0"),
            (["find", ".", "-fprintf", "x.txt", "%p"], "-fprintf"),
            (["find", ".", "-fls", "x"], "-fls"),
            (["rg", "--pre", "python", "x", "."], "--pre"),
            (["rg", "--pre=python", "x", "."], "--pre=python"),
            (["rg", "--pre-glob", "*.py", "--pre", "python", "."], "--pre"),
            (["git", "diff", "--output=out.txt"], "--output=out.txt"),
            (["git", "log", "--output", "o"], "--output"),
            (["git", "grep", "-O", "sh", "x"], "-O"),
            (["git", "--open-files-in-pager", "grep", "x"], "--open-files-in-pager"),
            (["git", "log", "--ext-diff"], "--ext-diff"),
            (["git", "log", "--textconv"], "--textconv"),
            (["git", "--paginate", "log"], "--paginate"),
            (["git", "-ccore.pager=sh", "log"], "-ccore.pager=sh"),
            (["git", "-c", "core.pager=sh", "log"], "-c"),
            (["git", "--config", "core.pager=sh", "log"], "--config"),
            (["git", "log", "-c", "core.pager=sh"], "-c"),
            (["git", "config", "--list"], "'config'"),
            (["git", "remote", "-v"], "'remote'"),
            (["git", "branch", "-a"], "'branch'"),
            (["file", "-C", "x"], "not in the read-only allowlist"),
            (["file", "x"], "not in the read-only allowlist"),
            (["jq", "."], "not in the read-only allowlist"),
            (
                ["jq", "--rawfile", "s", "/etc/passwd", "."],
                "not in the read-only allowlist",
            ),
            (["jq", "-f", "prog.jq", "."], "not in the read-only allowlist"),
        ]
        for argv, needle in denied:
            with self.subTest(argv=argv):
                reason = rules.check_argv(argv, "external-planner")
                self.assertIsNotNone(reason, argv)
                self.assertIn(needle, reason, argv)
        # read-only forms of the same programs stay allowed
        allowed = [
            ["find", ".", "-name", "*.py", "-type", "f"],
            ["rg", "--files", "."],
            ["rg", "-g", "*.py", "planner", "."],
            ["git", "diff", "--stat"],
            ["git", "log", "--no-textconv", "-1"],
            ["git", "grep", "-n", "x", "."],
        ]
        for argv in allowed:
            with self.subTest(allowed=argv):
                self.assertIsNone(rules.check_argv(argv, "external-planner"), argv)

    def test_external_planner_commands_and_writes(self):
        # the same leaves/commit/snooze denials as the external implementer
        for command in ("git push", "git commit -m x", "habit-snooze --snooze x"):
            self.assertIsNotNone(rules.check_command(command, "external-planner"))
        self.assertIsNone(rules.check_command("git status", "external-planner"))
        # a producer may not write anything: the runner publishes the artifact
        self.assertIsNotNone(rules.check_write("plan.md", "external-planner"))
        self.assertIsNotNone(rules.check_write(".work/x/plan.md", "external-planner"))
        self.assertIn("may not write", rules.check_write("plan.md", "external-planner"))

    def test_external_implementer_writes_confined_to_worktree(self):
        outside = Path(self._tmp.name) / "elsewhere"
        outside.mkdir()
        cases = [
            ("src/a.py", None),
            ("../x", "../x is outside the worktree"),
            (str(outside / "a.py"), f"{outside / 'a.py'} is outside the worktree"),
            (".git", "writing under .git or .work is not allowed (.git)"),
            (
                "sub/.git/config",
                "writing under .git or .work is not allowed (sub/.git/config)",
            ),
            (
                "sub/.git./config",
                "writing under .git or .work is not allowed (sub/.git./config)",
            ),
            (".work./x", "writing under .git or .work is not allowed (.work./x)"),
            (
                ".work/t1/plan.md",
                "writing under .git or .work is not allowed (.work/t1/plan.md)",
            ),
            (".GIT/x", "writing under .git or .work is not allowed (.GIT/x)"),
        ]
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(
                    rules.check_write(path, "external-implementer", self.root), expected
                )

    def test_claude_role_writes(self):
        for role in ("planner", "checker", "reviewer", "backlog"):
            with self.subTest(role=role):
                self.assertIsNotNone(rules.check_write("/repo/src/a.py", role))
                self.assertIsNone(rules.check_write("/repo/.work/x/review-1.md", role))
                self.assertIsNone(
                    rules.check_write("C:\\repo\\.work\\x\\plan.md", role)
                )
        self.assertIsNone(rules.check_write("/repo/src/a.py", "implementer"))

    def test_unknown_role_and_missing_root_raise(self):
        for role in ("general-purpose", "external", ""):
            with self.subTest(role=role):
                with self.assertRaises(ValueError):
                    rules.check_command("git push", role)
                with self.assertRaises(ValueError):
                    rules.check_write("a.py", role)
        with self.assertRaises(ValueError):
            rules.check_write("a", "external-implementer")

    def test_guard_runs_from_another_cwd(self):
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push"},
            "agent_type": "task-flow:planner",
        }
        result = subprocess.run(
            [sys.executable, str(GUARD)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=self._tmp.name,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(decision, "deny")

    def test_guard_runs_under_uv(self):
        if shutil.which("uv") is None:
            self.skipTest("uv is not on PATH")
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push"},
            "agent_type": "task-flow:planner",
        }
        result = subprocess.run(
            ["uv", "run", "--quiet", "--script", str(GUARD)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=self._tmp.name,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(decision, "deny")


if __name__ == "__main__":
    unittest.main()
