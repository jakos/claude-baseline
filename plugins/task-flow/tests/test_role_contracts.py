# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/role_contracts.py: plan.md artifact validation.

Run: uv run --script plugins/task-flow/tests/test_role_contracts.py

Every fixture is a string written to a temporary file; no repository, git state, model
or credential is involved. The valid plan below mirrors the contract of
skills/workflow/SKILL.md exactly.
"""

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import gates
import role_contracts

VALID_PLAN = """# Plan: Example task
## Goal
Do the thing.
## Context
Repository facts.
## Profiles
python: src/x.py, tests/test_x.py
docs: docs/x.md
## Steps
1. Change src/x.py (python).
2. Add a test (python).
## Acceptance criteria
AC1: The new behaviour is covered. Verify: uv run --script plugins/task-flow/tests/test_x.py
AC2: Docs name the key. Verify: manual: read docs/x.md
## Verification commands
```
uv run --script plugins/task-flow/tests/test_x.py
uvx ruff==0.17.3 check src
```
## Project rules
- No version bumps.
## Out of scope
Nothing else.
## Risks / open questions
None.
"""

BLOCKED_PLAN = """# Plan: Blocked task
## Goal
Something ambiguous.
## Context
Facts.
## Profiles
generic: README.md
## Steps
## Acceptance criteria
## Verification commands
## Project rules
- None.
## Out of scope
Everything else.
## Risks / open questions
BLOCKED: yes
Which of the two designs should we use?
"""

GATE_MD = """# task-flow configuration

## Verification commands

```
uv run --script plugins/task-flow/tests/test_cli_driver.py
uv run --script plugins/task-flow/tests/test_role_run.py
uvx ruff==0.17.3 check plugins/task-flow/scripts
```
"""

GATE_COMMANDS = [
    ["uv", "run", "--script", "plugins/task-flow/tests/test_cli_driver.py"],
    ["uv", "run", "--script", "plugins/task-flow/tests/test_role_run.py"],
    ["uvx", "ruff==0.17.3", "check", "plugins/task-flow/scripts"],
]


def plan_with(**replacements):
    text = VALID_PLAN
    for old, new in replacements.items():
        assert old in text, old
        text = text.replace(old, new)
    return text


class ValidPlanTest(unittest.TestCase):
    def test_valid_plan_passes(self):
        verdict = role_contracts.validate_plan(VALID_PLAN)
        self.assertTrue(verdict.ok, verdict.reasons)
        self.assertFalse(verdict.blocked)
        self.assertEqual(verdict.reasons, [])
        self.assertEqual(
            verdict.commands,
            [
                "uv run --script plugins/task-flow/tests/test_x.py",
                "uvx ruff==0.17.3 check src",
            ],
        )

    def test_blocked_plan_is_valid_and_blocked(self):
        verdict = role_contracts.validate_plan(BLOCKED_PLAN)
        self.assertTrue(verdict.ok, verdict.reasons)
        self.assertTrue(verdict.blocked)

    def test_blocked_plan_with_gate_and_empty_verification_list(self):
        # A genuine BLOCKED: yes plan may leave Verification commands empty, so the
        # inherited gate is not enforced against an empty list.
        verdict = role_contracts.validate_plan(
            BLOCKED_PLAN, gate_commands=GATE_COMMANDS
        )
        self.assertTrue(verdict.ok, verdict.reasons)
        self.assertTrue(verdict.blocked)
        # ... but a blocked plan that does list commands must still retain them.
        listed = BLOCKED_PLAN.replace(
            "## Verification commands\n## Project rules",
            "## Verification commands\n"
            "```\nuv run --script plugins/task-flow/tests/test_x.py\n```\n"
            "## Project rules",
        )
        verdict = role_contracts.validate_plan(listed, gate_commands=GATE_COMMANDS)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("inherited checks" in r for r in verdict.reasons), verdict.reasons
        )

    def test_gate_commands_must_be_retained(self):
        verdict = role_contracts.validate_plan(VALID_PLAN, gate_commands=GATE_COMMANDS)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any(
                "inherited checks" in r and "test_cli_driver.py" in r
                for r in verdict.reasons
            ),
            verdict.reasons,
        )
        # adding every inherited check makes it pass again
        text = VALID_PLAN.replace(
            "uvx ruff==0.17.3 check src\n```",
            "uvx ruff==0.17.3 check src\n"
            "uv run --script plugins/task-flow/tests/test_cli_driver.py\n"
            "uv run --script plugins/task-flow/tests/test_role_run.py\n"
            "uvx ruff==0.17.3 check plugins/task-flow/scripts\n```",
        )
        verdict = role_contracts.validate_plan(text, gate_commands=GATE_COMMANDS)
        self.assertTrue(verdict.ok, verdict.reasons)

    def test_stack_prerequisite_not_missing(self):
        stack = {"satisfied": ["cli-implementer-driver"]}
        bad = VALID_PLAN.replace(
            "Repository facts.",
            "Repository facts. The prerequisite cli-implementer-driver is missing here.",
        )
        verdict = role_contracts.validate_plan(bad, stack=stack)
        self.assertFalse(verdict.ok)
        self.assertTrue(any("cli-implementer-driver" in r for r in verdict.reasons))
        # saying it is satisfied (unmerged is not missing) passes
        good = VALID_PLAN.replace(
            "Repository facts.",
            "cli-implementer-driver is satisfied by stack order even though its source "
            "is not on the current checkout.",
        )
        verdict = role_contracts.validate_plan(good, stack=stack)
        self.assertTrue(verdict.ok, verdict.reasons)


class StructureTest(unittest.TestCase):
    def test_missing_and_duplicate_sections(self):
        missing = VALID_PLAN.replace("## Out of scope\nNothing else.\n", "")
        verdict = role_contracts.validate_plan(missing)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("missing section" in r for r in verdict.reasons), verdict.reasons
        )
        duplicate = VALID_PLAN.replace(
            "## Out of scope", "## Goal\nAgain.\n## Out of scope"
        )
        verdict = role_contracts.validate_plan(duplicate)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any(
                "duplicate section" in r or "exact order" in r for r in verdict.reasons
            ),
            verdict.reasons,
        )

    def test_wrong_section_order(self):
        text = VALID_PLAN.replace("## Goal\nDo the thing.\n## Context", "## Context")
        text = text.replace("## Profiles", "## Goal\nDo the thing.\n## Profiles")
        verdict = role_contracts.validate_plan(text)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("exact order" in r for r in verdict.reasons), verdict.reasons
        )

    def test_extra_unknown_section(self):
        text = VALID_PLAN.replace(
            "## Risks / open questions", "## Bonus\nNo.\n## Risks / open questions"
        )
        verdict = role_contracts.validate_plan(text)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("unknown section" in r for r in verdict.reasons), verdict.reasons
        )

    def test_fence_wrapped_artifact_and_bad_title(self):
        verdict = role_contracts.validate_plan("```\n" + VALID_PLAN + "```\n")
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("code fence" in r for r in verdict.reasons), verdict.reasons
        )
        verdict = role_contracts.validate_plan(
            "# Not a plan\n" + VALID_PLAN.split("\n", 1)[1]
        )
        self.assertFalse(verdict.ok)
        self.assertTrue(any("# Plan:" in r for r in verdict.reasons), verdict.reasons)

    def test_oversized_and_truncated(self):
        big = VALID_PLAN + "\n" + ("x" * role_contracts.MAX_ARTIFACT_CHARS)
        verdict = role_contracts.validate_plan(big)
        self.assertFalse(verdict.ok)
        self.assertTrue(any("over the" in r for r in verdict.reasons), verdict.reasons)
        truncated = VALID_PLAN.replace(
            "Nothing else.", "Nothing else. ...[12 characters truncated]..."
        )
        verdict = role_contracts.validate_plan(truncated)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("truncation" in r for r in verdict.reasons), verdict.reasons
        )

    def test_empty_artifact(self):
        verdict = role_contracts.validate_plan("   \n")
        self.assertFalse(verdict.ok)
        self.assertIn("empty", verdict.reasons[0])


class StepsProfilesTest(unittest.TestCase):
    def test_steps_must_be_numbered_and_sequential(self):
        text = plan_with(
            **{"1. Change src/x.py (python).": "- Change src/x.py (python)."}
        )
        verdict = role_contracts.validate_plan(text)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("numbered step" in r for r in verdict.reasons), verdict.reasons
        )
        text = plan_with(
            **{
                "1. Change src/x.py (python).\n2. Add a test (python).": "1. Change src/x.py (python).\n3. Add a test (python)."
            }
        )
        verdict = role_contracts.validate_plan(text)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("1..n in order" in r for r in verdict.reasons), verdict.reasons
        )

    def test_unknown_profile_rejected(self):
        text = plan_with(**{"docs: docs/x.md": "rust: src/lib.rs"})
        verdict = role_contracts.validate_plan(text)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("unknown profile" in r for r in verdict.reasons), verdict.reasons
        )


class CriteriaTest(unittest.TestCase):
    def test_bullet_and_numbered_ac_labels_rejected(self):
        for prefix in ("- ", "* ", "1. "):
            text = plan_with(
                **{
                    "AC1: The new behaviour is covered. Verify: uv run --script plugins/task-flow/tests/test_x.py": f"{prefix}AC1: The new behaviour is covered. Verify: tests"
                }
            )
            verdict = role_contracts.validate_plan(text)
            self.assertFalse(verdict.ok, prefix)
            self.assertTrue(
                any("column zero" in r for r in verdict.reasons), verdict.reasons
            )

    def test_non_sequential_and_repeated_labels(self):
        text = plan_with(
            **{
                "AC1: The new behaviour is covered. Verify: uv run --script plugins/task-flow/tests/test_x.py\n"
                "AC2: Docs name the key. Verify: manual: read docs/x.md": "AC2: The new behaviour is covered. Verify: tests\n"
                "AC2: Docs name the key. Verify: manual: read docs/x.md"
            }
        )
        verdict = role_contracts.validate_plan(text)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("sequential" in r for r in verdict.reasons), verdict.reasons
        )
        text = plan_with(
            **{
                "AC1: The new behaviour is covered. Verify: uv run --script plugins/task-flow/tests/test_x.py\n"
                "AC2: Docs name the key. Verify: manual: read docs/x.md": "AC1: a. Verify: tests\nAC3: b. Verify: manual: x"
            }
        )
        verdict = role_contracts.validate_plan(text)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("sequential" in r for r in verdict.reasons), verdict.reasons
        )

    def test_missing_or_empty_verify_reference(self):
        text = plan_with(
            **{
                "AC2: Docs name the key. Verify: manual: read docs/x.md": "AC2: Docs name the key."
            }
        )
        verdict = role_contracts.validate_plan(text)
        self.assertFalse(verdict.ok)
        self.assertTrue(any("Verify:" in r for r in verdict.reasons), verdict.reasons)
        text = plan_with(
            **{
                "AC2: Docs name the key. Verify: manual: read docs/x.md": "AC2: Docs name the key. Verify:   "
            }
        )
        verdict = role_contracts.validate_plan(text)
        self.assertFalse(verdict.ok)
        self.assertTrue(any("empty" in r for r in verdict.reasons), verdict.reasons)

    def test_bare_ac_lines_accepted(self):
        text = plan_with(
            **{
                "AC1: The new behaviour is covered. Verify: uv run --script plugins/task-flow/tests/test_x.py": "AC1: covered by the suite. Verify: uv run --script plugins/task-flow/tests/test_x.py"
            }
        )
        verdict = role_contracts.validate_plan(text)
        self.assertTrue(verdict.ok, verdict.reasons)


class CommandsTest(unittest.TestCase):
    def test_shell_command_refused(self):
        text = plan_with(
            **{
                "uvx ruff==0.17.3 check src\n```": "uvx ruff==0.17.3 check src | tee out\n```"
            }
        )
        verdict = role_contracts.validate_plan(text)
        self.assertFalse(verdict.ok)
        self.assertTrue(any("refused" in r for r in verdict.reasons), verdict.reasons)

    def test_comment_lines_skipped(self):
        text = plan_with(
            **{
                "```\nuv run --script plugins/task-flow/tests/test_x.py": "```\n# a comment\nuv run --script plugins/task-flow/tests/test_x.py"
            }
        )
        verdict = role_contracts.validate_plan(text)
        self.assertTrue(verdict.ok, verdict.reasons)
        self.assertEqual(
            verdict.commands,
            [
                "uv run --script plugins/task-flow/tests/test_x.py",
                "uvx ruff==0.17.3 check src",
            ],
        )

    def test_no_fenced_block(self):
        text = VALID_PLAN.replace("```\nuv run", "uv run").replace(
            "uvx ruff==0.17.3 check src\n```", "uvx ruff==0.17.3 check src"
        )
        verdict = role_contracts.validate_plan(text)
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("fenced command block" in r for r in verdict.reasons), verdict.reasons
        )


class CliTest(unittest.TestCase):
    def test_main_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "plan.md"
            plan.write_text(VALID_PLAN, encoding="utf-8")
            gate = root / "task-flow.md"
            gate.write_text(GATE_MD, encoding="utf-8")
            self.assertEqual(role_contracts.main([str(plan)]), 0)
            self.assertEqual(role_contracts.main([str(plan), "--gate", str(gate)]), 1)
            bad = root / "bad.md"
            bad.write_text("nope\n", encoding="utf-8")
            self.assertEqual(role_contracts.main([str(bad)]), 1)
            self.assertEqual(role_contracts.main([str(root / "missing.md")]), 2)

    def test_gate_argvs_parse(self):
        self.assertEqual(role_contracts._gate_argvs(GATE_MD), GATE_COMMANDS)
        self.assertEqual(
            [tuple(a) for a in role_contracts._gate_argvs(GATE_MD)],
            [
                tuple(gates.split_command(line))
                for line in [
                    "uv run --script plugins/task-flow/tests/test_cli_driver.py",
                    "uv run --script plugins/task-flow/tests/test_role_run.py",
                    "uvx ruff==0.17.3 check plugins/task-flow/scripts",
                ]
            ],
        )


class FencedDeclarationTest(unittest.TestCase):
    def test_examples_do_not_become_declarations(self) -> None:
        text = VALID_PLAN.replace(
            "## Steps\n", "## Steps\n```\n99. Example\n```\n"
        ).replace(
            "## Acceptance criteria\n",
            "## Acceptance criteria\n~~~\nAC99: Example\n~~~\n",
        )
        result = role_contracts.validate_plan(text)
        self.assertTrue(result.ok, result.reasons)


if __name__ == "__main__":
    unittest.main()
