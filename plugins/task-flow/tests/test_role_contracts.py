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


VALID_BACKLOG = """# Backlog: run-x
## Tasks
### 1. add-basic-feature
- depends-on: -
#### Goal
Implement the requirement.
#### Context
requirements.md supplies the request.
#### Constraints
Use stdlib.
## Risks / open questions
none
"""

REVIEW_PLAN = """# Plan: example
## Goal
Implement feature(value) to add one for every integer, including negative input.
## Context
A fixture.
## Profiles
python: src/x.py
## Steps
1. Implement feature.
## Acceptance criteria
AC1: feature(0) returns 1 and feature(-2) returns -1. Verify: test_feature
## Verification commands
```
python test_feature.py
```
## Project rules
None.
## Out of scope
Everything else.
## Risks / open questions
None.
"""

VALID_REVIEW = """# Review: round 1
## Acceptance criteria
| AC | Result | Evidence |
| --- | --- | --- |
| AC1 | PASS | test_feature passed in checks-1 |
## Pre-existing failures
none
## Findings
none
## Scope
none
## Verdict: APPROVED
## Fix list
none
"""


def checks_fixture(
    round_number: int = 1,
    result: str = "PASS",
    evidence: str = "test_feature: AssertionError",
) -> str:
    return (
        f"# Checks: round {round_number}\n\n"
        f"| # | Command | Result | Exit | Duration |\n| --- | --- | --- | --- | --- |\n"
        f"| 1 | `python test_feature.py` | {result} | {0 if result == 'PASS' else 1} | 1s |\n"
        "## Failures\n"
        + (
            "none\n"
            if result == "PASS"
            else f"### 1. `python test_feature.py`\n{evidence}\n"
        )
        + "## Not run\nnone\n"
    )


class BacklogContractTest(unittest.TestCase):
    def verdict(self, text: str, thin: bool = False) -> role_contracts.Validation:
        return role_contracts.validate_backlog(text, run_id="run-x", thin=thin)

    def test_valid_blocked_and_bootstrap(self) -> None:
        self.assertTrue(self.verdict(VALID_BACKLOG))
        blocked = "# Backlog: run-x\n## Tasks\n## Risks / open questions\nBLOCKED: yes\nWhich interface?\n"
        self.assertTrue(self.verdict(blocked).blocked)
        bootstrap = VALID_BACKLOG.replace(
            "add-basic-feature", "bootstrap-project"
        ).replace(
            "requirements.md supplies the request.",
            "requirements.md; dev-baseline:project-bootstrap is optional.",
        )
        self.assertTrue(self.verdict(bootstrap, True))
        self.assertFalse(self.verdict(bootstrap))
        self.assertFalse(self.verdict(VALID_BACKLOG, True))

    def test_malformed_backlogs(self) -> None:
        mutations = [
            VALID_BACKLOG.replace("run-x", "other"),
            VALID_BACKLOG.replace("### 1.", "### 2."),
            VALID_BACKLOG.replace("add-basic-feature", "../escape"),
            VALID_BACKLOG.replace("depends-on: -", "depends-on: later-task"),
            VALID_BACKLOG.replace("#### Context", "#### Details"),
            VALID_BACKLOG.replace("## Tasks", "# Extra title\n## Tasks"),
            VALID_BACKLOG.replace("Use stdlib.", ""),
            VALID_BACKLOG.replace(
                "## Risks",
                "### 2. add-basic-feature\n- depends-on: -\n#### Goal\nx\n#### Context\nx\n#### Constraints\nx\n## Risks",
            ),
        ]
        for text in mutations:
            with self.subTest(text=text):
                self.assertFalse(self.verdict(text))
        task = VALID_BACKLOG.split("### 1. ", 1)[1].split("## Risks", 1)[0]
        eleven = (
            "# Backlog: run-x\n## Tasks\n"
            + "".join(
                f"### {i}. " + task.replace("add-basic-feature", f"add-feature-{i}")
                for i in range(1, 12)
            )
            + "## Risks / open questions\nnone\n"
        )
        self.assertFalse(self.verdict(eleven))

    def test_exact_envelope_and_preserved_cited_requirements(self) -> None:
        import json

        data = {
            "requirements.md": "# Requirements\n- Add feature (README.md:3).\n",
            "backlog.md": VALID_BACKLOG,
        }
        self.assertEqual(role_contracts.backlog_envelope(json.dumps(data)), data)
        for raw in (
            "{}",
            '{"backlog.md":"x"}',
            '{"requirements.md":"x","backlog.md":"x","extra":"x"}',
            '{"requirements.md":"x","requirements.md":"y","backlog.md":"x"}',
        ):
            with self.assertRaises(ValueError):
                role_contracts.backlog_envelope(raw)
        original = "User requirement.\n"
        self.assertTrue(
            role_contracts.validate_requirements(
                original + "- Addition (README.md:2).\n", original=original
            )
        )
        self.assertFalse(
            role_contracts.validate_requirements(
                "- Addition (README.md:2).\n", original=original
            )
        )
        self.assertFalse(
            role_contracts.validate_requirements(
                original + "- Uncited.\n", original=original
            )
        )


class ReviewContractTest(unittest.TestCase):
    def checks(
        self,
        result: str = "PASS",
        round_number: int = 1,
        evidence: str = "test_feature: AssertionError",
    ) -> role_contracts.CheckEvidence:
        return role_contracts.parse_checks(
            checks_fixture(round_number, result, evidence),
            round_number=round_number,
            commands=["python test_feature.py"],
        )

    def verdict(
        self, text: str, current=None, baseline=None
    ) -> role_contracts.Validation:
        return role_contracts.validate_review(
            text,
            round_number=1,
            plan=REVIEW_PLAN,
            current=current or self.checks(),
            baseline=baseline,
        )

    def test_approval_requires_consistent_complete_evidence(self) -> None:
        self.assertTrue(self.verdict(VALID_REVIEW))
        for text in [
            VALID_REVIEW.replace("round 1", "round 2"),
            VALID_REVIEW.replace(
                "| AC1 | PASS | test_feature passed in checks-1 |", ""
            ),
            VALID_REVIEW.replace("| AC1 |", "| AC2 |"),
            VALID_REVIEW.replace("test_feature passed in checks-1", ""),
            VALID_REVIEW.replace("## Scope", "## Unknown"),
            VALID_REVIEW.replace("| AC1 | PASS", "| AC1 | FAIL"),
            VALID_REVIEW.replace(
                "## Findings\nnone",
                "## Findings\n1. [blocking] src/x.py:2 fails for negative input",
            ),
            VALID_REVIEW.replace(
                "## Scope\nnone", "## Scope\nUnexpected unrelated file: src/y.py:1"
            ),
        ]:
            with self.subTest(text=text):
                self.assertFalse(self.verdict(text))
        duplicate = VALID_REVIEW.replace(
            "## Pre-existing", "| AC1 | PASS | test_feature |\n## Pre-existing"
        )
        self.assertFalse(self.verdict(duplicate))

    def test_baseline_matching_and_insufficient_evidence(self) -> None:
        failure = self.checks("FAIL")
        self.assertFalse(self.verdict(VALID_REVIEW, failure))
        baseline = self.checks("FAIL", 0)
        review = VALID_REVIEW.replace(
            "## Pre-existing failures\nnone",
            "## Pre-existing failures\npython test_feature.py: test_feature: AssertionError matches checks-0",
        )
        self.assertTrue(self.verdict(review, failure, baseline))
        self.assertFalse(
            self.verdict(
                review,
                self.checks("ERROR", evidence="RuntimeError: new error"),
                baseline,
            )
        )
        for text in [
            checks_fixture(result="FAIL", evidence=""),
            checks_fixture().replace("| PASS |", "| WHAT |"),
            checks_fixture().replace("test_feature.py` | PASS", "other.py` | PASS"),
            checks_fixture().replace("round 1", "round 2"),
        ]:
            with self.assertRaises(ValueError):
                role_contracts.parse_checks(
                    text, round_number=1, commands=["python test_feature.py"]
                )

    def test_checker_rendered_commands_and_malformed_evidence(self) -> None:
        lint = self.checks("FAIL", evidence="src/x.py:2:1: F401 unused import")
        baseline_lint = self.checks("FAIL", 0, "src/x.py:2:1: F401 unused import")
        self.assertEqual(
            role_contracts.classify_checks(lint, baseline_lint),
            ({("python", "test_feature.py")}, set()),
        )
        command = 'python test_feature.py "a|b" "`literal`"'
        rendered = role_contracts.gates.render(
            1,
            "fixture",
            600,
            [
                {
                    "number": 1,
                    "command": command,
                    "status": "FAIL",
                    "exit": 1,
                    "duration": 1,
                    "detail": ["test_feature: AssertionError"],
                }
            ],
        )
        evidence = role_contracts.parse_checks(
            rendered, round_number=1, commands=[command]
        )
        self.assertEqual(len(evidence.failures), 1)
        for text in (
            checks_fixture().replace("| PASS | 0 |", "| PASS | 1 |"),
            checks_fixture(result="FAIL", evidence="[no evidence]"),
            checks_fixture(result="FAIL", evidence="1 failed"),
            checks_fixture(result="ERROR", evidence="ERROR"),
            checks_fixture(
                result="FAIL", evidence="Traceback (most recent call last):"
            ),
        ):
            with self.assertRaises(ValueError):
                role_contracts.parse_checks(
                    text, round_number=1, commands=["python test_feature.py"]
                )
        self.assertFalse(
            self.verdict(VALID_REVIEW.replace("## Findings\nnone", "## Findings\n"))
        )

    def test_changes_requested_requires_fix(self) -> None:
        text = VALID_REVIEW.replace("| AC1 | PASS", "| AC1 | FAIL").replace(
            "APPROVED", "CHANGES_REQUESTED"
        )
        self.assertFalse(self.verdict(text))
        text = text.replace(
            "## Fix list\nnone", "## Fix list\n1. Implement missing feature."
        )
        self.assertTrue(self.verdict(text))


if __name__ == "__main__":
    unittest.main()
