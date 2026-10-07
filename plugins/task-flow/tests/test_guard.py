# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/guard.py: feeds hook JSON on stdin and checks the decision.

Run: uv run --script plugins/task-flow/tests/test_guard.py
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

GUARD = Path(__file__).resolve().parent.parent / "scripts" / "guard.py"
READ_ONLY_ROLES = ("planner", "reviewer")
ALL_ROLES = ("planner", "implementer", "checker", "reviewer")


def decide(agent_type: str | None, tool: str, tool_input: dict) -> str:
    """Return "deny" or "allow" for one hook call."""
    payload: dict = {"tool_name": tool, "tool_input": tool_input}
    if agent_type is not None:
        payload["agent_type"] = agent_type
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
        return "allow"
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]


def shell(role: str, command: str, tool: str = "Bash") -> str:
    return decide(f"task-flow:{role}", tool, {"command": command})


class GuardTest(unittest.TestCase):
    def check(self, roles, commands, expected, tool="Bash"):
        for role in roles:
            for command in commands:
                with self.subTest(role=role, tool=tool, command=command):
                    self.assertEqual(shell(role, command, tool), expected, command)

    def test_plain_read_only_commands_allowed_for_planner_and_reviewer(self):
        self.check(
            READ_ONLY_ROLES,
            [
                "git show HEAD:README.md",
                "git diff main --stat",
                "git diff main -- plugins/task-flow | head -50",
                "git status",
                "git ls-files",
                "git rev-parse --verify task/x",
                "git blame README.md",
                "head -n 20 .work/x/status.md",
                "cat .gitignore; head .work/x/status.md",
                "git -C /repo log --oneline -5",
                'git log --oneline --grep "add"',
                "git log --grep 'push'",
            ],
            "allow",
        )

    def test_quoted_mutating_phrase_is_a_documented_false_positive(self):
        # The guard does not parse quotes, so these read-only commands are denied.
        # Workaround: use the Grep tool. Pinned so a change is noticed.
        self.check(
            READ_ONLY_ROLES,
            [
                'git grep -n -e "worktree add" -- plugins/task-flow/skills/workflow/orchestration.md',
                'git log -S "git commit" --oneline',
                'head -n 20 .work/x/status.md && git grep -n "worktree add" -- .',
            ],
            "deny",
        )
        self.check(
            READ_ONLY_ROLES,
            ['git grep -n -e "worktree add" -- file'],
            "deny",
            tool="PowerShell",
        )

    def test_mutating_git_denied_for_planner_and_reviewer(self):
        self.check(
            READ_ONLY_ROLES,
            [
                "git add .",
                "git -C /repo checkout main",
                "git worktree add -b task/x .work/x/worktree main",
                "git worktree remove p",
                "git branch -D foo",
                "git stash",
                "git reset --hard",
                "cd /repo && git reset --hard",
                "git status; git add -A",
                "ls | xargs git rm",
                "git switch -c task/x",
                'git add "unterminated',
            ],
            "deny",
        )
        self.check(READ_ONLY_ROLES, ["git.exe restore file"], "deny", tool="PowerShell")

    def test_commit_push_and_deploy_denied_for_every_role(self):
        self.check(
            ALL_ROLES,
            [
                'git commit -m "x"',
                "git -c user.name=x commit -m y",
                "git push origin main",
                "git status && git push",
                "gh pr create --fill",
                "kubectl apply -f m.yaml",
                "terraform apply",
            ],
            "deny",
        )

    def test_allowed_for_every_role(self):
        self.check(
            ALL_ROLES,
            [
                "git log --grep 'commit'",
                "git log -S 'push'",
                "kubectl apply --dry-run=client -f m.yaml",
            ],
            "allow",
        )

    def test_implementer_may_mutate(self):
        self.check(("implementer",), ["git add .", "git worktree add p"], "allow")

    def test_other_agents_untouched(self):
        for agent_type in (None, "general-purpose"):
            with self.subTest(agent_type=agent_type):
                self.assertEqual(
                    decide(agent_type, "Bash", {"command": "git push"}), "allow"
                )

    def test_write_tool_rules_unchanged(self):
        def write(role: str, path: str) -> str:
            return decide(f"task-flow:{role}", "Write", {"file_path": path})

        self.assertEqual(write("reviewer", "/repo/src/a.py"), "deny")
        self.assertEqual(write("reviewer", "/repo/.work/x/review-1.md"), "allow")
        self.assertEqual(write("implementer", "/repo/src/a.py"), "allow")


if __name__ == "__main__":
    unittest.main()
