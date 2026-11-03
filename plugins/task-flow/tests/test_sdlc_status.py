# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/sdlc_status.py: schema, evidence, rendering, CLI, finish.

Run: uv run --script plugins/task-flow/tests/test_sdlc_status.py

Every fixture is a temporary repository — a README.md with the sdlc:status markers, a
docs/task-flow/sdlc-status.toml written by the script's own writer, evidence files, and
a task file in a separate directory outside the repository — so no real `.work/<id>/`,
git state, model or login is ever touched. The script is exercised both as an imported
module (validation, derivation and rendering units) and as a subprocess
(check / render / done / finish).
"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sdlc_status.py"
_spec = importlib.util.spec_from_file_location("sdlc_status", SCRIPT)
sdlc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sdlc)

README_TEMPLATE = """\
# Fixture repo

Intro line.

<!-- bench:latest:start -->
bench body
<!-- bench:latest:end -->

## task-flow

<!-- sdlc:status:start -->
stale body
<!-- sdlc:status:end -->

Trailing line.
"""

TASK_WITH_DECLARATION = """\
# Task: fixture

## Goal
Close some criteria.

## Context
- SDLC criteria closed by this task: plan/plan-open, measure/measure-view
"""


def fixture_stages() -> list:
    """Nine stages: plan partial, build achieved, the rest missing."""
    data = {
        "plan": [
            ("plan-gate", "Plan gate", "done", "", "docs/evidence.txt"),
            ("plan-open", "Plan open", "open", "plan-task", "docs/evidence.txt"),
        ],
        "design": [("design-open", "Design open", "open", "design-task", "")],
        "build": [("build-done", "Build done", "done", "", "docs/evidence.txt")],
        "test": [("test-open", "Test open", "open", "", "")],
        "review": [("review-open", "Review open", "open", "review-task", "")],
        "release": [("release-open", "Release open", "open", "release-task", "")],
        "deploy": [("deploy-open", "Deploy open", "open", "", "")],
        "maintain": [("maintain-open", "Maintain open", "open", "maintain-task", "")],
        "measure": [
            ("measure-view", "Measure view", "open", "measure-task", "README.md")
        ],
    }
    return [
        sdlc.Stage(sid, sdlc.STAGE_TITLES[sid], [sdlc.Criterion(*row) for row in rows])
        for sid, rows in data.items()
    ]


class RepoTest(unittest.TestCase):
    """A temporary repository plus a task file in a separate directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.repo = self.base / "repo"
        (self.repo / "docs" / "task-flow").mkdir(parents=True)
        (self.repo / "docs" / "evidence.txt").write_text("evidence\n", encoding="utf-8")
        self.readme = self.repo / "README.md"
        self.readme.write_text(README_TEMPLATE, encoding="utf-8", newline="\n")
        self.source = self.repo / sdlc.SOURCE_RELPATH
        self.page = self.repo / sdlc.PAGE_RELPATH
        self.write_source(fixture_stages())
        self.artifacts = self.base / "artifacts"
        self.artifacts.mkdir()
        self.task_file = self.artifacts / "task.md"
        self.write_task(TASK_WITH_DECLARATION)

    def write_source(self, stages) -> None:
        self.source.write_text(sdlc.dump_source(stages), encoding="utf-8", newline="\n")

    def write_task(self, text: str) -> None:
        self.task_file.write_text(text, encoding="utf-8", newline="\n")

    def stages(self) -> list:
        return sdlc.load_source(self.source, self.repo)

    def load_text(self, text: str) -> list:
        self.source.write_text(text, encoding="utf-8", newline="\n")
        return sdlc.load_source(self.source, self.repo)

    def run_cli(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), *args],
            capture_output=True,
            check=False,
        )

    def snapshot(self) -> dict:
        return {
            p.relative_to(self.repo).as_posix(): p.read_bytes()
            for p in sorted(self.repo.rglob("*"))
            if p.is_file()
        }

    def assertRefused(self, result, code=2):
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertTrue(result.stderr.strip(), "a refusal must say why")


class SchemaTest(RepoTest):
    """AC2: malformed schema, duplicates, invalid states and ids fail with reasons."""

    def test_valid_source_loads_and_derives(self):
        stages = self.stages()
        self.assertEqual([s.id for s in stages], list(sdlc.STAGES))
        states = {s.id: sdlc.stage_state(s) for s in stages}
        self.assertEqual(states["plan"], "partial")
        self.assertEqual(states["build"], "achieved")
        for sid in (
            "design",
            "test",
            "review",
            "release",
            "deploy",
            "maintain",
            "measure",
        ):
            self.assertEqual(states[sid], "missing", sid)
        self.assertEqual(sum(len(s.criteria) for s in stages), 10)

    def test_unknown_stage_rejected(self):
        text = self.source.read_text(encoding="utf-8").replace(
            "[stage.plan]", "[stage.ideate]"
        )
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("unknown stage id", str(ctx.exception))

    def test_missing_stage_rejected(self):
        text = self.source.read_text(encoding="utf-8").split("[stage.measure]")[0]
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("in that order", str(ctx.exception))

    def test_wrong_stage_order_rejected(self):
        stages = fixture_stages()
        stages[0], stages[1] = stages[1], stages[0]
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(sdlc.dump_source(stages))
        self.assertIn("in that order", str(ctx.exception))

    def test_duplicate_criterion_rejected(self):
        text = self.source.read_text(encoding="utf-8").replace(
            'id = "plan-open"', 'id = "plan-gate"'
        )
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("duplicate criterion id", str(ctx.exception))

    def test_invalid_state_rejected(self):
        text = self.source.read_text(encoding="utf-8").replace(
            'state = "open"', 'state = "wip"', 1
        )
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("state must be 'done' or 'open'", str(ctx.exception))

    def test_non_string_value_rejected(self):
        text = self.source.read_text(encoding="utf-8").replace(
            'state = "open"', "state = 1", 1
        )
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("every value must be a string", str(ctx.exception))

    def test_empty_criteria_rejected(self):
        stages = fixture_stages()
        stages[0].criteria = []
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(sdlc.dump_source(stages))
        self.assertIn("nonempty list", str(ctx.exception))

    def test_unknown_criterion_key_rejected(self):
        text = self.source.read_text(encoding="utf-8").replace(
            'task = "plan-task"', 'task = "plan-task"\nextra = 1', 1
        )
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("unknown key", str(ctx.exception))

    def test_missing_key_rejected(self):
        text = self.source.read_text(encoding="utf-8").replace(
            'task = "plan-task"\n', "", 1
        )
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("missing key", str(ctx.exception))

    def test_bad_criterion_id_rejected(self):
        text = self.source.read_text(encoding="utf-8").replace(
            'id = "plan-open"', 'id = "Plan_Open"'
        )
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("must match [a-z][a-z0-9-]*", str(ctx.exception))

    def test_bad_task_id_rejected(self):
        text = self.source.read_text(encoding="utf-8").replace(
            'task = "plan-task"', 'task = "Plan Task"'
        )
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("task", str(ctx.exception))

    def test_done_without_evidence_rejected(self):
        text = self.source.read_text(encoding="utf-8").replace(
            'evidence = "docs/evidence.txt"', 'evidence = ""', 1
        )
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("state is done", str(ctx.exception))

    def test_open_without_evidence_is_valid(self):
        stages = self.stages()
        self.assertEqual(stages[0].criteria[1].evidence, "docs/evidence.txt")
        stages[0].criteria[1].evidence = ""
        self.write_source(stages)
        self.assertEqual(self.stages()[0].criteria[1].evidence, "")

    def test_malformed_toml_rejected(self):
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text("not [valid toml")
        self.assertIn("invalid TOML", str(ctx.exception))

    def test_missing_source_rejected(self):
        self.source.unlink()
        with self.assertRaises(sdlc.StatusError) as ctx:
            sdlc.load_source(self.source, self.repo)
        self.assertIn("missing source", str(ctx.exception))


class EvidenceTest(RepoTest):
    """AC2: evidence must be safe, repository-relative and existing."""

    def mutate_evidence(self, old: str, new: str) -> str:
        text = self.source.read_text(encoding="utf-8")
        self.assertIn(old, text)
        return text.replace(old, new, 1)

    def test_escaping_evidence_rejected(self):
        (self.base / "outside.txt").write_text("x\n", encoding="utf-8")
        text = self.mutate_evidence(
            'evidence = "docs/evidence.txt"', 'evidence = "../outside.txt"'
        )
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("evidence", str(ctx.exception))

    def test_absolute_evidence_rejected(self):
        for bad in ("/etc/passwd", "C:/Windows/system32", "C:\\\\Windows"):
            text = self.mutate_evidence(
                'evidence = "docs/evidence.txt"', f'evidence = "{bad}"'
            )
            with self.assertRaises(sdlc.StatusError, msg=bad):
                self.load_text(text)

    def test_backslash_evidence_rejected(self):
        text = self.mutate_evidence(
            'evidence = "docs/evidence.txt"', 'evidence = "docs\\\\evidence.txt"'
        )
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("backslash", str(ctx.exception))

    def test_missing_evidence_file_rejected(self):
        text = self.mutate_evidence(
            'evidence = "docs/evidence.txt"', 'evidence = "docs/nope.txt"'
        )
        with self.assertRaises(sdlc.StatusError) as ctx:
            self.load_text(text)
        self.assertIn("does not exist", str(ctx.exception))

    def test_evidence_must_exist_for_done_only(self):
        # an open criterion may carry no evidence; a done one may not
        stages = self.stages()
        stages[1].criteria[0].evidence = ""
        self.write_source(stages)
        self.stages()  # still valid


class RenderTest(RepoTest):
    """AC4: deterministic render, marker safety, content preservation, budgets."""

    def test_render_fills_block_and_creates_page(self):
        result = self.run_cli("render")
        self.assertEqual(result.returncode, 0, result.stderr)
        stages = self.stages()
        text = self.readme.read_text(encoding="utf-8")
        self.assertEqual(
            sdlc.replace_block(
                text, sdlc.README_START, sdlc.README_END, sdlc.readme_block_body(stages)
            ),
            text,
        )
        self.assertEqual(self.page.read_text(encoding="utf-8"), sdlc.page_text(stages))

    def test_render_preserves_surrounding_content(self):
        before = self.readme.read_text(encoding="utf-8")
        self.run_cli("render")
        after = self.readme.read_text(encoding="utf-8")

        def outside(text):
            lines = text.splitlines(keepends=True)
            s = next(i for i, l in enumerate(lines) if l.strip() == sdlc.README_START)
            e = next(i for i, l in enumerate(lines) if l.strip() == sdlc.README_END)
            return "".join(lines[: s + 1]), "".join(lines[e:])

        self.assertEqual(outside(after), outside(before))
        self.assertIn("<!-- bench:latest:start -->", after)
        self.assertIn("Trailing line.", after)

    def test_render_is_idempotent_byte_for_byte(self):
        self.run_cli("render")
        first = self.snapshot()
        result = self.run_cli("render")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), first)
        self.assertIn(b"unchanged", result.stdout)

    def test_render_output_is_lf_utf8(self):
        self.run_cli("render")
        for path in (self.readme, self.page, self.source):
            data = path.read_bytes()
            self.assertNotIn(b"\r", data, path)
            data.decode("utf-8")  # must not raise
        self.assertIn("—", self.page.read_text(encoding="utf-8"))

    def test_render_does_not_modify_source(self):
        before = self.source.read_bytes()
        self.run_cli("render")
        self.assertEqual(self.source.read_bytes(), before)

    def test_block_body_within_budget_and_shape(self):
        stages = self.stages()
        body = sdlc.readme_block_body(stages)
        lines = body.splitlines()
        self.assertLessEqual(len(lines), sdlc.MAX_BLOCK_LINES)
        self.assertIn(f"<!-- diagram:{sdlc.DIAGRAM_NAME} -->", lines)
        table = [l for l in lines if l.startswith("|")]
        self.assertEqual(len(table), 11, "header, separator and nine stage rows")
        # the diagram is shared identically between block and page
        self.assertIn(sdlc.diagram_body(stages), body)
        self.assertIn(sdlc.diagram_body(stages), sdlc.page_text(stages))

    def test_render_refuses_missing_marker_without_writing(self):
        text = self.readme.read_text(encoding="utf-8").replace(
            sdlc.README_START + "\n", ""
        )
        self.readme.write_text(text, encoding="utf-8")
        before = self.snapshot()
        result = self.run_cli("render")
        self.assertRefused(result)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.page.exists())

    def test_render_refuses_duplicate_marker(self):
        text = self.readme.read_text(encoding="utf-8").replace(
            sdlc.README_START, sdlc.README_START + "\n" + sdlc.README_START, 1
        )
        self.readme.write_text(text, encoding="utf-8")
        before = self.snapshot()
        self.assertRefused(self.run_cli("render"))
        self.assertEqual(self.snapshot(), before)

    def test_render_refuses_reversed_markers(self):
        text = self.readme.read_text(encoding="utf-8").replace(
            sdlc.README_START + "\nstale body\n" + sdlc.README_END,
            sdlc.README_END + "\nstale body\n" + sdlc.README_START,
        )
        self.readme.write_text(text, encoding="utf-8")
        before = self.snapshot()
        self.assertRefused(self.run_cli("render"))
        self.assertEqual(self.snapshot(), before)

    def test_render_refuses_invalid_source_without_writing(self):
        self.source.write_text("nope = [", encoding="utf-8")
        before = self.snapshot()
        self.assertRefused(self.run_cli("render"))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.page.exists())


class CheckTest(RepoTest):
    """AC5: check never writes; nonzero for stale/missing/invalid, zero when current."""

    def test_check_passes_after_render(self):
        self.run_cli("render")
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"ok:", result.stdout)

    def test_check_never_writes(self):
        self.run_cli("render")
        before = self.snapshot()
        self.run_cli("check")
        self.assertEqual(self.snapshot(), before)

    def test_check_fails_on_stale_block(self):
        self.run_cli("render")
        text = self.readme.read_text(encoding="utf-8").replace(
            "| Plan | partial |", "| Plan | achieved |", 1
        )
        self.readme.write_text(text, encoding="utf-8", newline="\n")
        before = self.snapshot()
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"stale", result.stderr)
        self.assertEqual(self.snapshot(), before, "check must not write")

    def test_check_fails_on_stale_page(self):
        self.run_cli("render")
        self.page.write_text(
            self.page.read_text(encoding="utf-8") + "\nextra\n", encoding="utf-8"
        )
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"stale", result.stderr)

    def test_check_fails_on_missing_page_and_render_fixes_it(self):
        self.run_cli("render")
        self.page.unlink()
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"missing", result.stderr)
        self.run_cli("render")
        self.assertEqual(self.run_cli("check").returncode, 0)

    def test_check_fails_on_missing_source(self):
        self.source.unlink()
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"missing source", result.stderr)

    def test_check_fails_on_invalid_source_without_writing(self):
        self.source.write_text("nope = [", encoding="utf-8")
        before = self.snapshot()
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_check_fails_on_done_without_evidence(self):
        self.run_cli("render")
        text = self.source.read_text(encoding="utf-8").replace(
            'evidence = "docs/evidence.txt"', 'evidence = ""', 1
        )
        self.source.write_text(text, encoding="utf-8")
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"state is done", result.stderr)

    def test_check_fails_on_marker_problem_without_writing(self):
        self.run_cli("render")
        text = self.readme.read_text(encoding="utf-8").replace(
            sdlc.README_END + "\n", ""
        )
        self.readme.write_text(text, encoding="utf-8")
        before = self.snapshot()
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"marker", result.stderr)
        self.assertEqual(self.snapshot(), before)


class DoneTest(RepoTest):
    """AC6: done validates ids/evidence, preserves the rest, is idempotent."""

    def test_done_marks_criterion_and_preserves_others(self):
        before = self.stages()
        result = self.run_cli("done", "plan/plan-open")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.stages()
        self.assertEqual(after[0].criteria[1].state, "done")
        for s_before, s_after in zip(before, after):
            for c_before, c_after in zip(s_before.criteria, s_after.criteria):
                if (s_before.id, c_before.id) != ("plan", "plan-open"):
                    self.assertEqual(
                        (c_before.state, c_before.task, c_before.evidence),
                        (c_after.state, c_after.task, c_after.evidence),
                    )
        text = self.source.read_text(encoding="utf-8")
        self.assertEqual(
            sdlc.dump_source(sdlc.load_source(self.source, self.repo)), text
        )

    def test_done_is_idempotent(self):
        self.run_cli("done", "plan/plan-open")
        first = self.snapshot()
        result = self.run_cli("done", "plan/plan-open")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), first)

    def test_done_multiple_refs(self):
        result = self.run_cli("done", "plan/plan-open", "measure/measure-view")
        self.assertEqual(result.returncode, 0, result.stderr)
        stages = self.stages()
        self.assertEqual(stages[0].criteria[1].state, "done")
        self.assertEqual(stages[8].criteria[0].state, "done")

    def test_done_unknown_ref_refused_without_writes(self):
        before = self.snapshot()
        result = self.run_cli("done", "plan/nope")
        self.assertRefused(result)
        self.assertIn(b"unknown criterion", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_done_malformed_ref_refused(self):
        before = self.snapshot()
        self.assertRefused(self.run_cli("done", "plan"))
        self.assertEqual(self.snapshot(), before)

    def test_done_without_evidence_refused(self):
        before = self.snapshot()
        result = self.run_cli("done", "test/test-open")  # open, no evidence
        self.assertRefused(result)
        self.assertIn(b"no evidence", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_done_batch_with_one_invalid_id_changes_nothing(self):
        before = self.snapshot()
        result = self.run_cli("done", "plan/plan-open", "plan/nope")
        self.assertRefused(result)
        self.assertEqual(self.snapshot(), before)


class FinishTest(RepoTest):
    """AC6/AC7: finish batch behaviour, no-op cases, separate directories."""

    def test_finish_marks_renders_and_checks(self):
        result = self.run_cli("finish", "--task-file", str(self.task_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        stages = self.stages()
        self.assertEqual(stages[0].criteria[1].state, "done")
        self.assertEqual(stages[8].criteria[0].state, "done")
        self.assertTrue(self.page.is_file())
        text = self.readme.read_text(encoding="utf-8")
        self.assertEqual(
            sdlc.replace_block(
                text, sdlc.README_START, sdlc.README_END, sdlc.readme_block_body(stages)
            ),
            text,
        )
        self.assertEqual(self.run_cli("check").returncode, 0)
        # the task file outside the repo is untouched
        self.assertEqual(
            self.task_file.read_text(encoding="utf-8"), TASK_WITH_DECLARATION
        )

    def test_finish_is_idempotent(self):
        self.run_cli("finish", "--task-file", str(self.task_file))
        first = self.snapshot()
        result = self.run_cli("finish", "--task-file", str(self.task_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), first)

    def test_finish_without_source_is_noop(self):
        self.source.unlink()
        before = self.snapshot()
        result = self.run_cli("finish", "--task-file", str(self.task_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"no-op", result.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.page.exists(), "no generated pages without a source")

    def test_finish_without_declaration_is_noop(self):
        self.write_task("# Task: fixture\n\n## Goal\nNothing declared.\n")
        before = self.snapshot()
        result = self.run_cli("finish", "--task-file", str(self.task_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"no-op", result.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.page.exists())

    def test_finish_with_empty_declaration_is_noop(self):
        self.write_task("# Task\n\n- SDLC criteria closed by this task:\n")
        before = self.snapshot()
        result = self.run_cli("finish", "--task-file", str(self.task_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"no-op", result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_finish_unknown_id_changes_nothing(self):
        self.write_task("# Task\n\n- SDLC criteria closed by this task: plan/nope\n")
        before = self.snapshot()
        result = self.run_cli("finish", "--task-file", str(self.task_file))
        self.assertRefused(result)
        self.assertIn(b"unknown criterion", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.page.exists())

    def test_finish_malformed_declaration_changes_nothing(self):
        self.write_task("# Task\n\n- SDLC criteria closed by this task: not-a-ref\n")
        before = self.snapshot()
        self.assertRefused(self.run_cli("finish", "--task-file", str(self.task_file)))
        self.assertEqual(self.snapshot(), before)

    def test_finish_multiple_declarations_rejected(self):
        self.write_task(
            "# Task\n\n- SDLC criteria closed by this task: plan/plan-open\n"
            "- SDLC criteria closed by this task: measure/measure-view\n"
        )
        before = self.snapshot()
        self.assertRefused(self.run_cli("finish", "--task-file", str(self.task_file)))
        self.assertEqual(self.snapshot(), before)

    def test_finish_missing_task_file_refused(self):
        result = self.run_cli("finish", "--task-file", str(self.artifacts / "nope.md"))
        self.assertRefused(result)

    def test_finish_task_file_outside_repo_with_explicit_paths(self):
        # plugin script, repository and task artifacts live in three places
        result = self.run_cli("finish", "--task-file", str(self.task_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / sdlc.PAGE_RELPATH).is_file())
        self.assertNotEqual(self.task_file.parent, self.repo)


class UnitTest(unittest.TestCase):
    """Derivation, diagram, declaration parsing and replace_block units."""

    def test_stage_state_covers_all_mixed_none(self):
        C, S = sdlc.Criterion, sdlc.Stage
        all_done = S(
            "plan", "Plan", [C("a", "A", "done", "", "x"), C("b", "B", "done", "", "x")]
        )
        mixed = S(
            "plan", "Plan", [C("a", "A", "done", "", "x"), C("b", "B", "open", "t", "")]
        )
        none_done = S("plan", "Plan", [C("a", "A", "open", "t", "")])
        self.assertEqual(sdlc.stage_state(all_done), "achieved")
        self.assertEqual(sdlc.stage_state(mixed), "partial")
        self.assertEqual(sdlc.stage_state(none_done), "missing")

    def test_diagram_is_deterministic_with_nine_nodes_and_three_classes(self):
        stages = fixture_stages()
        first = sdlc.diagram_body(stages)
        self.assertEqual(first, sdlc.diagram_body(stages))
        node_lines = [l for l in first.splitlines() if l.startswith("  ") and "[" in l]
        self.assertEqual(len(node_lines), 9)
        for state in sdlc.DERIVED_STATES:
            self.assertIn(f"classDef {state} ", first)
            self.assertIn(sdlc.STAGE_COLORS[state], first)
        self.assertIn('plan["Plan (partial)"]', first)
        self.assertIn('build["Build (achieved)"]', first)
        self.assertIn('deploy["Deploy (missing)"]', first)

    def test_dump_load_roundtrip(self):
        stages = fixture_stages()
        text = sdlc.dump_source(stages)
        import tomllib

        parsed, problems = sdlc.parse_source(tomllib.loads(text))
        self.assertEqual(problems, [])
        self.assertEqual(sdlc.dump_source(parsed), text)

    def test_declaration_matches_the_exact_task_bullet(self):
        line = "- SDLC criteria closed by this task: measure/sdlc-status-view"
        m = sdlc.DECLARATION_RE.match(line)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "measure/sdlc-status-view")
        comma = "- SDLC criteria closed by this task: a/b, c/d"
        self.assertEqual(sdlc.DECLARATION_RE.match(comma).group(1), "a/b, c/d")
        self.assertIsNone(
            sdlc.DECLARATION_RE.match("SDLC criteria closed by this task: a/b")
        )

    def test_replace_block_semantics(self):
        text = "before\nS\nold\nE\nafter\n"
        out = sdlc.replace_block(text, "S", "E", "new\n")
        self.assertEqual(out, "before\nS\nnew\nE\nafter\n")
        with self.assertRaises(sdlc.MarkerError):
            sdlc.replace_block("S\nE\nS\n", "S", "E", "x\n")
        with self.assertRaises(sdlc.MarkerError):
            sdlc.replace_block("E\nS\n", "S", "E", "x\n")
        with self.assertRaises(sdlc.MarkerError):
            sdlc.replace_block("S\n", "S", "E", "x\n")


if __name__ == "__main__":
    unittest.main()
