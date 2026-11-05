# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/epics.py: temp .work/ fixtures, no real tasks or git state.

Run: uv run --script plugins/task-flow/tests/test_epics.py

The CLI runs against temporary repositories only; the parser is imported for unit
tests. Never touches this repository's .work/, git state, models or login.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "epics.py"
sys.path.insert(0, str(SCRIPTS))
import epics


def status_text(task_id, phase="new", round_n="0", verdict="none"):
    return (
        f"# Status: {task_id}\n"
        f"- phase: {phase}\n"
        f"- round: {round_n}\n"
        f"- last-verdict: {verdict}\n"
        "## History\n"
        "- 2026-01-01T00:00:00Z new — fixture\n"
        "## Log\n"
    )


def epic_text(
    epic_id="demo",
    title="Demo epic",
    goal="Do the demo.",
    base="main",
    state="pending",
    delivery="per-task",
    active_run="none",
    rows=(("1", "a", "-"), ("2", "b", "a")),
    history=("- 2026-01-01T00:00:00Z created — fixture",),
):
    lines = [
        f"# Epic: {epic_id}",
        f"- title: {title}",
        f"- goal: {goal}",
        f"- base: {base}",
        f"- state: {state}",
        f"- delivery: {delivery}",
        f"- active-run: {active_run}",
        "## Tasks",
        "| # | Task | Depends on |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {n} | {t} | {d} |" for n, t, d in rows]
    lines += ["## History", *history, ""]
    return "\n".join(lines)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".work").mkdir()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def task(self, task_id, phase="new", plan=False, round_n="0", verdict="none"):
        directory = self.root / ".work" / task_id
        directory.mkdir(parents=True, exist_ok=True)
        self.write(
            directory / "status.md",
            status_text(task_id, phase, round_n, verdict),
        )
        if plan:
            self.write(directory / "plan.md", "# Plan: x\n")
        return directory

    def epic(self, text, epic_id="demo"):
        path = self.root / ".work" / "_epics" / epic_id / "epic.md"
        self.write(path, text)
        return path

    def run_md(
        self,
        run_id="r1",
        source="epic",
        epic="demo",
        state="running",
        rows=(),
        planning="0",
    ):
        directory = self.root / ".work" / "_autopilot" / run_id
        directory.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Autopilot: {run_id}",
            "- mode: full",
            "- base: main",
            "- budget: none",
            f"- planning-tokens: {planning}",
            "- continue-past: none",
            "- bootstrap: none",
        ]
        if source is not None:
            lines.append(f"- source: {source}")
        if epic is not None:
            lines.append(f"- epic: {epic}")
        width = 5 if any(len(row) == 5 for row in rows) else 4
        header = "| # | Task | Depends on | Tokens used |"
        separator = "| --- | --- | --- | --- |"
        if width == 5:
            header += " External USD |"
            separator += " --- |"
        lines += [f"- state: {state}", "## Tasks", header]
        if rows:
            lines.append(separator)
            lines += [f"| {' | '.join(str(c) for c in row)} |" for row in rows]
        lines += ["## History", "- 2026-01-01T00:00:00Z running — start"]
        self.write(directory / "run.md", "\n".join(lines) + "\n")

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            check=False,
            cwd=self.root,
        )

    def out(self, *args):
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode("utf-8").strip()

    def refused(self, *args):
        result = self.cli(*args)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(result.stderr)
        return result.stderr.decode("utf-8")

    def epic_bytes(self, epic_id="demo"):
        return (self.root / ".work" / "_epics" / epic_id / "epic.md").read_bytes()


class ParserTest(Fixture):
    def test_roundtrip(self):
        text = epic_text()
        parsed = epics.parse_epic(text, "demo")
        self.assertEqual(epics.render_epic(parsed), text)
        self.assertEqual([t.task_id for t in parsed.tasks], ["a", "b"])
        self.assertEqual(parsed.tasks[1].depends, "a")

    def test_roundtrip_empty_tasks(self):
        text = epic_text(rows=())
        parsed = epics.parse_epic(text, "demo")
        self.assertEqual(epics.render_epic(parsed), text)

    def test_independent_roots_roundtrip(self):
        text = epic_text(rows=(("1", "a", "-"), ("2", "b", "-"), ("3", "c", "a")))
        parsed = epics.parse_epic(text, "demo")
        self.assertIsNone(parsed.tasks[0].depends)
        self.assertIsNone(parsed.tasks[1].depends)
        self.assertEqual(parsed.tasks[2].depends, "a")

    def assert_refused(self, text, needle, epic_id="demo"):
        with self.assertRaises(epics.EpicError) as caught:
            epics.parse_epic(text, epic_id, root=self.root)
        self.assertIn(needle, str(caught.exception))

    def test_refuses_duplicate_field(self):
        self.assert_refused(
            epic_text().replace("- base: main", "- base: main\n- base: other"),
            "duplicate header field 'base'",
        )

    def test_refuses_unknown_field(self):
        self.assert_refused(
            epic_text().replace("- base: main", "- base: main\n- extra: x"),
            "unknown header field 'extra'",
        )

    def test_refuses_missing_field(self):
        self.assert_refused(epic_text().replace("- base: main\n", ""), "exactly")

    def test_refuses_wrong_field_order(self):
        text = epic_text().replace(
            "- title: Demo epic\n- goal: Do the demo.",
            "- goal: Do the demo.\n- title: Demo epic",
        )
        self.assert_refused(text, "in order")

    def test_refuses_bad_state(self):
        self.assert_refused(epic_text(state="weird"), "state must be one of")

    def test_refuses_bad_delivery(self):
        self.assert_refused(epic_text(delivery="sometimes"), "delivery must be one of")

    def test_refuses_bad_active_run(self):
        self.assert_refused(epic_text(active_run="../escape"), "active-run must be")

    def test_refuses_wrong_title_line(self):
        self.assert_refused(
            epic_text().replace("# Epic: demo", "# Epic: other"), "first line"
        )

    def test_refuses_bad_epic_id(self):
        with self.assertRaises(epics.EpicError):
            epics.parse_epic(epic_text(), "_bad")

    def test_refuses_cr_and_missing_newline(self):
        with self.assertRaises(epics.EpicError):
            epics.parse_epic(epic_text().replace("\n", "\r\n"), "demo")
        with self.assertRaises(epics.EpicError) as caught:
            epics.parse_epic(epic_text().rstrip("\n"), "demo")
        self.assertIn("newline", str(caught.exception))

    def test_refuses_unknown_section(self):
        self.assert_refused(
            epic_text().replace("## History", "## Extra\n## History"),
            "exactly '## Tasks'",
        )

    def test_refuses_empty_history(self):
        self.assert_refused(epic_text(history=()), "at least one entry")

    def test_refuses_malformed_history(self):
        self.assert_refused(epic_text(history=("- not-a-time created — x",)), "ISO")

    def test_refuses_bad_table_header(self):
        self.assert_refused(
            epic_text().replace("| # | Task | Depends on |", "| # | Task | Dep |"),
            "header must be",
        )

    def test_refuses_wrong_row_width(self):
        self.assert_refused(
            epic_text().replace("| 1 | a | - |", "| 1 | a |"),
            "needs 3 cells",
        )

    def test_refuses_bad_numbering(self):
        self.assert_refused(
            epic_text().replace("| 2 | b | a |", "| 3 | b | a |"), "numbered from 1"
        )

    def test_refuses_duplicate_task(self):
        self.assert_refused(
            epic_text(rows=(("1", "a", "-"), ("2", "a", "a"))), "repeats"
        )

    def test_refuses_invalid_task_id(self):
        self.assert_refused(epic_text(rows=(("1", "A_b", "-"),)), "invalid task id")

    def test_refuses_forward_dependency(self):
        self.assert_refused(
            epic_text(rows=(("1", "a", "b"), ("2", "b", "-"))),
            "not an earlier row",
        )

    def test_refuses_dangling_dependency(self):
        self.assert_refused(epic_text(rows=(("1", "a", "zzz"),)), "not an earlier row")

    def test_refuses_multiple_parents_with_hint(self):
        message = self.assert_refused_message(
            epic_text(rows=(("1", "a", "-"), ("2", "b", "-"), ("3", "c", "a, b")))
        )
        self.assertIn("at most one parent", message)
        self.assertIn("separate integration design", message)

    def assert_refused_message(self, text, epic_id="demo"):
        with self.assertRaises(epics.EpicError) as caught:
            epics.parse_epic(text, epic_id, root=self.root)
        return str(caught.exception)

    def test_refuses_missing_task_with_root(self):
        self.task("a")
        self.assert_refused(epic_text(), "unknown task 'b'")

    def test_root_check_passes_when_tasks_exist(self):
        self.task("a")
        self.task("b")
        parsed = epics.parse_epic(epic_text(), "demo", root=self.root)
        self.assertEqual(len(parsed.tasks), 2)


class CreationTest(Fixture):
    def test_new_goal_only_creates_empty_pending_epic(self):
        out = self.out(
            "new", ".", "demo", "--title", "Demo", "--goal", "Do it.", "--base", "main"
        )
        self.assertIn("created: demo (0 task(s))", out)
        parsed = epics.load_epic(self.root, "demo")
        self.assertEqual(parsed.tasks, [])
        self.assertEqual(parsed.state, "pending")
        self.assertEqual(parsed.active_run, "none")

    def test_new_with_tasks_chains_them(self):
        self.task("a", "new")
        self.task("b", "planned", plan=True)
        self.task("c", "approved", plan=True)
        out = self.out(
            "new",
            ".",
            "demo",
            "--title",
            "T",
            "--goal",
            "G",
            "--base",
            "main",
            "--tasks",
            "a,b,c",
        )
        self.assertIn("created: demo (3 task(s))", out)
        parsed = epics.load_epic(self.root, "demo")
        self.assertEqual(
            [(t.task_id, t.depends) for t in parsed.tasks],
            [("a", None), ("b", "a"), ("c", "b")],
        )

    def test_new_refuses_collision_with_task_folder(self):
        self.task("demo")
        err = self.refused(
            "new", ".", "demo", "--title", "T", "--goal", "G", "--base", "main"
        )
        self.assertIn("collides with the existing task folder", err)
        self.assertFalse((self.root / ".work" / "_epics" / "demo").exists())

    def test_new_refuses_existing_epic(self):
        self.out("new", ".", "demo", "--title", "T", "--goal", "G", "--base", "main")
        err = self.refused(
            "new", ".", "demo", "--title", "T", "--goal", "G", "--base", "main"
        )
        self.assertIn("already exists", err)

    def test_new_refuses_ineligible_task(self):
        self.task("a", "escalated")
        err = self.refused(
            "new",
            ".",
            "demo",
            "--title",
            "T",
            "--goal",
            "G",
            "--base",
            "main",
            "--tasks",
            "a",
        )
        self.assertIn("failed (phase escalated)", err)

    def test_new_refuses_task_that_is_an_epic_id(self):
        self.out("new", ".", "other", "--title", "T", "--goal", "G", "--base", "main")
        self.task("a")
        err = self.refused(
            "new",
            ".",
            "demo",
            "--title",
            "T",
            "--goal",
            "G",
            "--base",
            "main",
            "--tasks",
            "other",
        )
        self.assertIn("collides with the existing epic", err)

    def test_failed_new_writes_nothing(self):
        self.task("demo")
        before = sorted(p.name for p in (self.root / ".work").iterdir())
        self.refused(
            "new", ".", "demo", "--title", "T", "--goal", "G", "--base", "main"
        )
        after = sorted(p.name for p in (self.root / ".work").iterdir())
        self.assertEqual(before, after)

    def test_is_epic_and_list_and_epic_of(self):
        self.task("a")
        self.out(
            "new",
            ".",
            "demo",
            "--title",
            "T",
            "--goal",
            "G",
            "--base",
            "main",
            "--tasks",
            "a",
        )
        self.assertEqual(self.out("is-epic", ".", "demo"), "yes")
        self.assertEqual(self.out("is-epic", ".", "a"), "no")
        self.assertEqual(self.out("list", "."), "demo")
        self.assertEqual(self.out("epic-of", ".", "a"), "demo")
        self.assertEqual(self.out("epic-of", ".", "zzz"), "none")


class MembershipTest(Fixture):
    def make_epic(
        self, epic_id="demo", tasks=("a", "b"), state="pending", active_run="none"
    ):
        for t in tasks:
            self.task(t)
        rows = tuple(
            (str(i + 1), t, tasks[i - 1] if i else "-") for i, t in enumerate(tasks)
        )
        return self.epic(
            epic_text(epic_id=epic_id, rows=rows, state=state, active_run=active_run),
            epic_id=epic_id,
        )

    def test_add_defaults_to_chain_on_last_row(self):
        self.make_epic()
        self.task("c")
        out = self.out("add", ".", "demo", "--tasks", "c")
        self.assertIn("added: c", out)
        parsed = epics.load_epic(self.root, "demo")
        self.assertEqual(parsed.tasks[-1].depends, "b")

    def test_add_with_explicit_parent_and_root(self):
        self.make_epic()
        self.task("c")
        self.out("add", ".", "demo", "--tasks", "c", "--depends-on", "a")
        parsed = epics.load_epic(self.root, "demo")
        self.assertEqual(parsed.tasks[-1].depends, "a")
        self.task("d")
        self.out("add", ".", "demo", "--tasks", "d", "--depends-on", "-")
        parsed = epics.load_epic(self.root, "demo")
        self.assertIsNone(parsed.tasks[-1].depends)

    def test_add_refuses_unknown_parent(self):
        self.make_epic()
        self.task("c")
        err = self.refused("add", ".", "demo", "--tasks", "c", "--depends-on", "zzz")
        self.assertIn("not an earlier row", err)

    def test_add_refuses_duplicate_row(self):
        self.make_epic()
        err = self.refused("add", ".", "demo", "--tasks", "a")
        self.assertIn("already a row", err)

    def test_add_refuses_task_of_another_open_epic(self):
        self.make_epic("one", ("a",))
        self.make_epic("two", ("b",))
        err = self.refused("add", ".", "two", "--tasks", "a")
        self.assertIn("at most one non-completed epic", err)

    def test_membership_in_completed_epic_is_free_afterwards(self):
        self.make_epic("done-epic", ("a",), state="completed")
        for t in ("a",):
            self.task(t, "done")
        self.task("b")
        self.out(
            "new",
            ".",
            "second",
            "--title",
            "T",
            "--goal",
            "G",
            "--base",
            "main",
            "--tasks",
            "a,b",
        )
        parsed = epics.load_epic(self.root, "second")
        self.assertEqual(parsed.task_ids(), ["a", "b"])

    def test_add_refuses_while_active(self):
        self.make_epic(state="active", active_run="r1")
        self.run_md()
        self.task("c")
        before = self.epic_bytes()
        err = self.refused("add", ".", "demo", "--tasks", "c")
        self.assertIn("frozen while execution runs", err)
        self.assertEqual(self.epic_bytes(), before)

    def test_add_reopens_completed_epic(self):
        self.make_epic(state="completed")
        self.task("a", "done")
        self.task("b", "done")
        self.task("c")
        self.out("add", ".", "demo", "--tasks", "c")
        parsed = epics.load_epic(self.root, "demo")
        self.assertEqual(parsed.state, "pending")

    def test_remove_refuses_started_task(self):
        self.make_epic()
        self.task("b", "implementing")
        before = self.epic_bytes()
        err = self.refused("remove", ".", "demo", "--tasks", "b")
        self.assertIn("has started", err)
        self.assertEqual(self.epic_bytes(), before)

    def test_remove_refuses_dangling_dependency(self):
        self.make_epic()
        err = self.refused("remove", ".", "demo", "--tasks", "a")
        self.assertIn("dangling", err)

    def test_remove_renumbers_rows(self):
        self.make_epic("demo", ("a", "b", "c"))
        self.task("c")
        out = self.out("remove", ".", "demo", "--tasks", "c")
        self.assertIn("removed: c", out)
        parsed = epics.load_epic(self.root, "demo")
        self.assertEqual([t.number for t in parsed.tasks], [1, 2])

    def test_remove_refuses_unknown_row(self):
        self.make_epic()
        err = self.refused("remove", ".", "demo", "--tasks", "zzz")
        self.assertIn("not a row", err)

    def test_failed_mutation_preserves_original_bytes(self):
        self.make_epic()
        self.task("c", "escalated")
        before = self.epic_bytes()
        self.refused("add", ".", "demo", "--tasks", "c")
        self.assertEqual(self.epic_bytes(), before)


class LinkTest(Fixture):
    def setUp(self):
        super().setUp()
        self.task("a")
        self.task("b")
        self.epic(epic_text())

    def test_link_requires_epic_source_run(self):
        self.run_md(source="tasks", epic=None)
        err = self.refused("link", ".", "demo", "r1")
        self.assertIn("source: epic", err)

    def test_link_requires_matching_epic_field(self):
        self.run_md(epic="other")
        err = self.refused("link", ".", "demo", "r1")
        self.assertIn("not linked to epic", err)

    def test_link_sets_active_run_and_state(self):
        self.run_md()
        out = self.out("link", ".", "demo", "r1")
        self.assertIn("linked: demo -> run r1", out)
        parsed = epics.load_epic(self.root, "demo")
        self.assertEqual(parsed.active_run, "r1")
        self.assertEqual(parsed.state, "active")

    def test_link_refuses_second_run(self):
        self.run_md()
        self.run_md(run_id="r2")
        self.out("link", ".", "demo", "r1")
        err = self.refused("link", ".", "demo", "r2")
        self.assertIn("already linked to run 'r1'", err)

    def test_unlink_clears_active_run(self):
        self.run_md()
        self.out("link", ".", "demo", "r1")
        self.out("unlink", ".", "demo")
        parsed = epics.load_epic(self.root, "demo")
        self.assertEqual(parsed.active_run, "none")
        self.assertEqual(parsed.state, "pending")

    def test_link_same_run_is_an_idempotent_noop(self):
        self.run_md()
        self.out("link", ".", "demo", "r1")
        after_first = self.epic_bytes()
        out = self.out("link", ".", "demo", "r1")
        self.assertIn("unchanged: demo is already linked to run r1", out)
        self.assertEqual(self.epic_bytes(), after_first)

    def test_status_is_deterministic_and_never_mutates_the_epic(self):
        self.run_md()
        self.out("link", ".", "demo", "r1")
        before = self.epic_bytes()
        first = self.out("status", ".", "demo")
        second = self.out("status", ".", "demo")
        self.assertEqual(first, second)
        self.assertEqual(self.epic_bytes(), before)

    def test_validate_ok_and_stale_state(self):
        out = self.out("validate", ".", "demo")
        self.assertIn("ok: demo", out)
        self.epic(epic_text(state="active"))
        err = self.refused("validate", ".", "demo")
        self.assertIn("derived state", err)


class StatusReportTest(Fixture):
    def setUp(self):
        super().setUp()
        self.task("a", "done", round_n="3", verdict="APPROVED")
        self.task("b", "escalated", round_n="3", verdict="CHANGES_REQUESTED")
        self.task("c", "approved", plan=True)
        self.epic(
            epic_text(
                rows=(("1", "a", "-"), ("2", "b", "-"), ("3", "c", "b")),
                state="paused",
                active_run="r1",
            )
        )
        self.run_md(
            state="paused",
            rows=(
                ("1", "a", "-", "1000"),
                ("2", "b", "-", "unknown"),
                ("3", "c", "b", "0"),
            ),
        )

    def test_status_shows_phases_blockers_and_actions(self):
        out = self.out("status", ".", "demo")
        self.assertIn("- state: paused (stored: paused)", out)
        self.assertIn("| 3 | c | b | approved | 0 | none |", out)
        self.assertIn("Blocked by", out)
        self.assertIn("b (escalated)", out)
        self.assertIn("unavailable (no merged risk schema)", out)
        self.assertIn("at least 0 (unknown)", out)
        self.assertIn("retry", out)
        self.assertIn("runs in run r1", out)

    def test_status_spend_labels_unknowns(self):
        out = self.out("status", ".", "demo")
        self.assertIn("- tokens: at least 1000", out)

    def test_status_mermaid_is_escaped_and_coloured(self):
        out = self.out("status", ".", "demo")
        self.assertIn("```mermaid", out)
        self.assertIn("flowchart TD", out)
        self.assertIn('t1["a — done"]', out)
        self.assertIn("t2 --> t3", out)
        self.assertIn("classDef done fill:#d4f8d4", out)
        self.assertIn("classDef failed fill:#ffd7d5", out)
        self.assertIn("class t1 done", out)
        self.assertIn("class t2 failed", out)
        self.assertIn("class t3 open", out)

    def test_mermaid_escape(self):
        self.assertEqual(epics._mermaid_escape('a"b'), "a#quot;b")

    def test_report_written_only_when_completed(self):
        err = self.refused("report", ".", "demo")
        self.assertIn("not completed", err)
        self.assertFalse(
            (self.root / ".work" / "_epics" / "demo" / "report.md").exists()
        )

    def test_report_lists_all_branches_and_spend(self):
        for t in ("a", "b", "c"):
            self.task(t, "done", round_n="3", verdict="APPROVED")
        self.epic(
            epic_text(
                rows=(("1", "a", "-"), ("2", "b", "-"), ("3", "c", "b")),
                state="completed",
                active_run="r1",
            )
        )
        out = self.out("report", ".", "demo")
        self.assertIn("wrote:", out)
        report = (self.root / ".work" / "_epics" / "demo" / "report.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("# Epic report: demo", report)
        self.assertIn("- task/a", report)
        self.assertIn("- task/b", report)
        self.assertIn("- task/c", report)
        self.assertIn("intent only", report)
        self.assertIn("partial completion is never reported as success", report)
        self.assertIn("- tokens: at least 1000", report)


class RetryTest(Fixture):
    def setUp(self):
        super().setUp()
        self.task("a", "escalated", round_n="3", verdict="CHANGES_REQUESTED")
        self.task("b")
        self.epic(epic_text(state="paused", active_run="r1"))
        task_dir = self.root / ".work" / "a"
        self.write(task_dir / "checks-3.md", "# Checks: round 3\n")
        self.write(task_dir / "review-3.md", "# Review: round 3\n")
        self.write(task_dir / "impl-3.json", "{}")
        self.write(task_dir / "summary.md", "# Summary: a\n")
        self.run_md(
            state="paused",
            rows=(("1", "a", "-", "5000"), ("2", "b", "a", "0")),
        )

    def status_bytes(self, task_id="a"):
        return (self.root / ".work" / task_id / "status.md").read_bytes()

    def test_routed_retry_gets_new_pin_with_archived_round_files(self) -> None:
        import models

        task = self.root / ".work/a"
        evidence = {
            "schema": "implementer-routing/1",
            "band": "S",
            "selected_profile": "small",
            "rule_digest": "old",
        }
        initial = dict(evidence, state="pinned", attempt="initial")
        models.publish_attempt(task, initial)
        self.out("retry", ".", "demo", "a", "--authorized")
        fresh = models.attempt_record(
            self.root, task, dict(evidence, rule_digest="new")
        )
        self.assertEqual(fresh["attempt"], "demo/1")
        self.assertEqual(fresh["rule_digest"], "new")
        models.publish_attempt(task, fresh)
        self.assertEqual(
            models.attempt_record(self.root, task, dict(evidence, rule_digest="new")),
            fresh,
        )
        self.assertIn('"attempt": "initial"', self.status_bytes().decode("utf-8"))
        self.assertIn('"attempt": "demo/1"', self.status_bytes().decode("utf-8"))

    def test_retry_needs_authorization_and_changes_nothing(self):
        before = self.epic_bytes()
        status_before = self.status_bytes()
        err = self.refused("retry", ".", "demo", "a")
        self.assertIn("explicit", err)
        self.assertIn("--auto does not authorize", err)
        self.assertEqual(self.epic_bytes(), before)
        self.assertEqual(self.status_bytes(), status_before)
        self.assertFalse(
            (self.root / ".work" / "_epics" / "demo" / "attempts").exists()
        )

    def test_retry_archives_artifacts_and_records_history(self):
        out = self.out("retry", ".", "demo", "a", "--authorized")
        self.assertIn("archived: attempt 1 of a (4 file(s)", out)
        dest = self.root / ".work" / "_epics" / "demo" / "attempts" / "a" / "attempt-1"
        self.assertEqual(
            sorted(p.name for p in dest.iterdir()),
            ["checks-3.md", "impl-3.json", "review-3.md", "spend.md", "summary.md"],
        )
        parsed = epics.load_epic(self.root, "demo")
        self.assertTrue(any(" retry a " in line for line in parsed.history))

    def test_retry_authorized_resets_task_status_for_a_new_attempt(self):
        out = self.out("retry", ".", "demo", "a", "--authorized")
        self.assertIn("reset to phase approved, round 0 (previous round 3)", out)
        status = self.status_bytes().decode("utf-8")
        self.assertIn("- phase: approved\n", status)
        self.assertIn("- round: 0\n", status)
        self.assertIn("round reset to 0 for attempt 1 of epic demo", status)
        self.assertNotIn("- phase: escalated\n", status)
        parsed = epics.load_epic(self.root, "demo")
        self.assertTrue(any("round 3 reset to 0" in line for line in parsed.history))

    def test_retry_archives_the_spend_snapshot(self):
        self.out("retry", ".", "demo", "a", "--authorized")
        spend = (
            self.root
            / ".work"
            / "_epics"
            / "demo"
            / "attempts"
            / "a"
            / "attempt-1"
            / "spend.md"
        )
        text = spend.read_text(encoding="utf-8")
        self.assertIn("# Attempt 1 spend snapshot: a", text)
        self.assertIn("- tokens: 5000", text)
        self.assertIn("- external USD: 0", text)

    def test_retry_is_idempotent(self):
        self.out("retry", ".", "demo", "a", "--authorized")
        status_after_first = self.status_bytes()
        out = self.out("retry", ".", "demo", "a", "--authorized")
        self.assertIn("unchanged: attempt 1", out)
        attempts = self.root / ".work" / "_epics" / "demo" / "attempts" / "a"
        self.assertEqual([p.name for p in attempts.iterdir()], ["attempt-1"])
        # the idempotent re-run does not reset the task a second time
        self.assertEqual(self.status_bytes(), status_after_first)

    def test_second_failed_attempt_archives_attempt_two_and_resets_again(self):
        self.out("retry", ".", "demo", "a", "--authorized")
        task_dir = self.root / ".work" / "a"
        self.write(task_dir / "checks-3.md", "# Checks: round 3 (attempt 2)\n")
        self.write(
            task_dir / "status.md",
            status_text("a", "escalated", "3", "CHANGES_REQUESTED"),
        )
        out = self.out("retry", ".", "demo", "a", "--authorized")
        self.assertIn("archived: attempt 2 of a", out)
        attempts = self.root / ".work" / "_epics" / "demo" / "attempts" / "a"
        self.assertEqual(
            [p.name for p in attempts.iterdir()], ["attempt-1", "attempt-2"]
        )
        parsed = epics.load_epic(self.root, "demo")
        retries = [line for line in parsed.history if " retry a " in line]
        self.assertEqual(len(retries), 2)
        status = self.status_bytes().decode("utf-8")
        self.assertIn("- round: 0\n", status)

    def test_retry_refuses_non_failed_task(self):
        err = self.refused("retry", ".", "demo", "b", "--authorized")
        self.assertIn("has not failed", err)

    def test_retry_refuses_task_outside_epic(self):
        self.task("zzz", "escalated")
        err = self.refused("retry", ".", "demo", "zzz", "--authorized")
        self.assertIn("not a row", err)


class ResumeTest(Fixture):
    """AC6: the linked run resumes unchanged; idle additions keep done state/totals."""

    def setUp(self):
        super().setUp()
        self.task("a", "done", round_n="3", verdict="APPROVED")
        self.task("b")
        self.epic(epic_text(state="paused", active_run="r1"))
        self.run_md(state="stopped", rows=(("1", "a", "-", "700"),))

    def test_same_epic_id_resumes_the_same_linked_run_unchanged(self):
        out = self.out("link", ".", "demo", "r1")
        self.assertIn("unchanged: demo is already linked to run r1", out)
        parsed = epics.load_epic(self.root, "demo")
        self.assertEqual(parsed.active_run, "r1")
        self.assertEqual(parsed.task_ids(), ["a", "b"])
        linked = [line for line in parsed.history if " linked " in line]
        self.assertEqual(len(linked), 0)  # the fixture history has none; no dup added

    def test_idle_addition_after_a_run_keeps_done_tasks_and_totals(self):
        before = self.out("status", ".", "demo")
        self.assertIn("- tokens: at least 700", before)
        self.task("c")
        out = self.out("add", ".", "demo", "--tasks", "c")
        self.assertIn("added: c", out)
        parsed = epics.load_epic(self.root, "demo")
        self.assertEqual(parsed.task_ids(), ["a", "b", "c"])
        self.assertEqual(parsed.tasks[-1].depends, "b")
        after = self.out("status", ".", "demo")
        self.assertIn("| 1 | a | - | done | 3 | APPROVED |", after)
        # the linked run's table was regenerated: a keeps its 700, the fresh rows are 0
        self.assertIn("- tokens: 700 (planning-tokens 0", after)
        self.assertIn(
            "| 3 | c | b | new | 0 | none | unavailable (no merged risk schema) | 0 | 0 |",
            after,
        )


class RunSyncTest(Fixture):
    """AC6: an idle add/remove regenerates the linked run's table so autopilot accepts it."""

    def setUp(self):
        super().setUp()
        self.task("a", "done", plan=True, round_n="3", verdict="APPROVED")
        self.task("b", "approved", plan=True)
        self.task("c", "approved", plan=True)
        self.task("d", "approved", plan=True)
        self.epic(
            epic_text(
                state="paused",
                active_run="r1",
                rows=(("1", "a", "-"), ("2", "b", "a"), ("3", "c", "b")),
            )
        )
        self.run_md(
            state="stopped",
            rows=(("1", "a", "-", "700"), ("2", "b", "a", "0"), ("3", "c", "b", "0")),
        )

    def run_md_path(self) -> Path:
        return self.root / ".work" / "_autopilot" / "r1" / "run.md"

    def autopilot(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "autopilot.py"), *args],
            capture_output=True,
            check=False,
            cwd=self.root,
        )

    def next_line(self):
        result = self.autopilot("next", str(self.run_md_path().parent))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode("utf-8").strip()

    def test_add_regenerates_the_linked_run_table_and_next_accepts_it(self):
        run_before = self.run_md_path().read_bytes()
        out = self.out("add", ".", "demo", "--tasks", "d")
        self.assertIn("added: d", out)
        self.assertIn("run r1 table regenerated from epic demo", out)
        text = self.run_md_path().read_text(encoding="utf-8")
        self.assertNotEqual(text.encode("utf-8"), run_before)
        self.assertIn("- state: planning", text)  # the start gate runs again
        self.assertIn("- planning-tokens: 0", text)  # the header is kept
        self.assertIn("- source: epic", text)
        self.assertIn("- epic: demo", text)
        self.assertIn("| 1 | a | - | 700 |", text)  # done row and its totals unchanged
        self.assertIn("| 2 | b | a | 0 |", text)
        self.assertIn("| 3 | c | b | 0 |", text)
        self.assertIn("| 4 | d | c | 0 |", text)  # the new row is fresh (0)
        self.assertIn("regenerated", text.split("## History")[1])  # one History line
        self.assertEqual(self.next_line(), "run b")  # autopilot.py accepts the run
        parsed = epics.load_epic(self.root, "demo")
        self.assertEqual(parsed.active_run, "r1")
        self.assertEqual(parsed.task_ids(), ["a", "b", "c", "d"])

    def test_add_keeps_the_external_usd_column_and_cells(self):
        self.run_md(
            state="stopped",
            rows=(
                ("1", "a", "-", "700", "1.50"),
                ("2", "b", "a", "0", "0.25"),
                ("3", "c", "b", "0", "unknown"),
            ),
        )
        self.out("add", ".", "demo", "--tasks", "d")
        text = self.run_md_path().read_text(encoding="utf-8")
        self.assertIn("| # | Task | Depends on | Tokens used | External USD |", text)
        self.assertIn("| 1 | a | - | 700 | 1.50 |", text)
        self.assertIn("| 3 | c | b | 0 | unknown |", text)  # sticky cell kept verbatim
        self.assertIn("| 4 | d | c | 0 | 0 |", text)

    def test_remove_regenerates_the_linked_run_table(self):
        out = self.out("remove", ".", "demo", "--tasks", "c")
        self.assertIn("removed: c", out)
        self.assertIn("run r1 table regenerated from epic demo", out)
        text = self.run_md_path().read_text(encoding="utf-8")
        self.assertIn("- state: planning", text)
        self.assertIn("| 1 | a | - | 700 |", text)
        self.assertIn("| 2 | b | a | 0 |", text)
        self.assertNotIn("| c |", text)
        self.assertEqual(self.next_line(), "run b")
        parsed = epics.load_epic(self.root, "demo")
        self.assertEqual(parsed.task_ids(), ["a", "b"])

    def test_sync_run_after_a_hand_edit_of_epic_md(self):
        self.epic(
            epic_text(
                state="paused",
                active_run="r1",
                rows=(
                    ("1", "a", "-"),
                    ("2", "b", "a"),
                    ("3", "c", "b"),
                    ("4", "d", "-"),
                ),
            )
        )
        # before the sync the run table does not match: autopilot refuses
        result = self.autopilot("next", str(self.run_md_path().parent))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(b"do not match the epic", result.stderr)
        out = self.out("sync-run", ".", "demo")
        self.assertIn("synced: run r1 table regenerated from epic demo", out)
        text = self.run_md_path().read_text(encoding="utf-8")
        self.assertIn("- state: planning", text)
        self.assertIn("| 1 | a | - | 700 |", text)
        self.assertIn("| 4 | d | - | 0 |", text)  # the hand-added root is a fresh row
        self.assertEqual(self.next_line(), "run b")
        parsed = epics.load_epic(self.root, "demo")
        self.assertTrue(any(" synced " in line for line in parsed.history))

    def test_sync_run_refuses_without_a_linked_run(self):
        self.epic(epic_text(state="pending", active_run="none"))
        err = self.refused("sync-run", ".", "demo")
        self.assertIn("no linked run", err)

    def test_sync_run_refuses_while_active(self):
        self.run_md(
            state="running",
            rows=(("1", "a", "-", "700"), ("2", "b", "a", "0"), ("3", "c", "b", "0")),
        )
        run_before = self.run_md_path().read_bytes()
        err = self.refused("sync-run", ".", "demo")
        self.assertIn("active", err)
        self.assertEqual(self.run_md_path().read_bytes(), run_before)

    def test_refusal_leaves_the_run_and_the_epic_unchanged(self):
        self.run_md_path().unlink()
        epic_before = self.epic_bytes()
        err = self.refused("add", ".", "demo", "--tasks", "d")
        self.assertIn("no autopilot run 'r1'", err)
        self.assertEqual(self.epic_bytes(), epic_before)
        self.assertFalse(self.run_md_path().exists())

    def test_add_without_a_linked_run_leaves_run_md_untouched(self):
        self.epic(
            epic_text(
                state="pending",
                active_run="none",
                rows=(("1", "a", "-"), ("2", "b", "a"), ("3", "c", "b")),
            )
        )
        run_before = self.run_md_path().read_bytes()
        out = self.out("add", ".", "demo", "--tasks", "d")
        self.assertIn("added: d", out)
        self.assertNotIn("regenerated", out)
        self.assertEqual(self.run_md_path().read_bytes(), run_before)


class SpendTest(Fixture):
    """AC7/AC8: cumulative spend includes planning, stays sticky, never double counts."""

    def setUp(self):
        super().setUp()
        self.task("a", "done", round_n="3", verdict="APPROVED")
        self.task("b", "escalated", round_n="3", verdict="CHANGES_REQUESTED")
        self.epic(epic_text(state="paused", active_run="r1"))

    def test_status_totals_include_planning_tokens(self):
        self.run_md(
            state="paused",
            planning="40000",
            rows=(("1", "a", "-", "1000"), ("2", "b", "-", "unknown")),
        )
        out = self.out("status", ".", "demo")
        self.assertIn("- tokens: at least 41000", out)
        self.assertIn("planning-tokens 40000 plus the task rows of run r1", out)

    def test_status_planning_unknown_stays_sticky(self):
        self.run_md(
            state="paused",
            planning="unknown",
            rows=(("1", "a", "-", "1000"), ("2", "b", "-", "500")),
        )
        out = self.out("status", ".", "demo")
        self.assertIn("- tokens: at least 1500", out)
        self.assertIn("planning-tokens unknown", out)

    def test_status_usd_absent_column_counts_as_zero(self):
        self.run_md(
            state="paused", rows=(("1", "a", "-", "1000"), ("2", "b", "-", "500"))
        )
        out = self.out("status", ".", "demo")
        self.assertIn("- external USD: 0.00", out)

    def test_status_usd_column_sums_and_stays_sticky(self):
        self.run_md(
            state="paused",
            rows=(("1", "a", "-", "1000", "1.50"), ("2", "b", "-", "500", "unknown")),
        )
        out = self.out("status", ".", "demo")
        self.assertIn("- external USD: at least 1.50", out)

    def test_status_usd_recomputed_from_invocation_ids_planner_included(self):
        import json

        self.run_md(
            state="paused",
            rows=(("1", "a", "-", "1000", "0"), ("2", "b", "a", "0", "0")),
        )
        task_dir = self.root / ".work" / "a"
        # the planner's role-*.json counts into the task's External USD next to
        # the implementer's impl-<n>.json, recomputed from the invocation ids
        (task_dir / "role-planner-2026-01-01T00-00-00Z-ab12cd34.json").write_text(
            json.dumps(
                {"schema": "role-metrics/1", "role": "planner", "cost_usd": 0.5}
            ),
            encoding="utf-8",
        )
        (task_dir / "impl-1.json").write_text(
            json.dumps({"profile": "p", "cost_usd": 1.5}), encoding="utf-8"
        )
        out = self.out("status", ".", "demo")
        self.assertIn("- external USD: 2.00", out)
        # idempotent across repeated status calls (a resume never double counts)
        self.assertIn("- external USD: 2.00", self.out("status", ".", "demo"))

    def test_status_usd_invocation_null_stays_unknown(self):
        import json

        self.run_md(
            state="paused",
            rows=(("1", "a", "-", "1000", "3.00"), ("2", "b", "a", "0", "0")),
        )
        (self.root / ".work" / "a" / "role-planner-x.json").write_text(
            json.dumps(
                {"schema": "role-metrics/1", "role": "planner", "cost_usd": None}
            ),
            encoding="utf-8",
        )
        out = self.out("status", ".", "demo")
        self.assertIn("- external USD: at least 0.00", out)

    def test_retry_spend_is_counted_once_and_survives_unlink(self):
        self.run_md(
            state="paused",
            planning="100",
            rows=(("1", "a", "-", "5000"), ("2", "b", "a", "0")),
        )
        before = self.out("status", ".", "demo")
        self.assertIn("- tokens: 5100 (planning-tokens 100", before)
        self.write(
            self.root / ".work" / "a" / "status.md",
            status_text("a", "escalated", "3", "CHANGES_REQUESTED"),
        )
        self.out("retry", ".", "demo", "a", "--authorized")
        linked = self.out("status", ".", "demo")
        self.assertIn("- tokens: 5100 (planning-tokens 100", linked)  # no double count
        self.out("unlink", ".", "demo")
        unlinked = self.out("status", ".", "demo")
        self.assertIn("- tokens: at least 5000", unlinked)  # archived spend survives
        self.assertIn("no run linked", unlinked)

    def test_report_totals_include_planning_tokens(self):
        for task_id in ("a", "b"):
            self.task(task_id, "done", round_n="3", verdict="APPROVED")
        self.epic(epic_text(state="completed", active_run="r1"))
        self.run_md(
            state="done",
            planning="40000",
            rows=(("1", "a", "-", "1000"), ("2", "b", "a", "2000")),
        )
        self.out("report", ".", "demo")
        report = (self.root / ".work" / "_epics" / "demo" / "report.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("- tokens: 43000 (planning-tokens 40000", report)


class ContainmentTest(Fixture):
    def make_dir_link(self, link: Path, target: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=True)
            return
        except OSError:
            if sys.platform == "win32":
                import _winapi

                _winapi.CreateJunction(str(target), str(link))
                return
            raise

    def test_symlink_escape_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.write(outside / "epic.md", epic_text())
        base = self.root / ".work" / "_epics"
        base.mkdir(parents=True)
        try:
            self.make_dir_link(base / "demo", outside)
        except OSError:
            self.skipTest("symlinks/junctions unavailable")
        with self.assertRaises(epics.EpicError) as caught:
            epics.load_epic(self.root, "demo")
        self.assertIn("symlink", str(caught.exception))
        result = self.cli("validate", ".", "demo")
        self.assertEqual(result.returncode, 2)

    def test_traversal_id_refused(self):
        err = self.refused("validate", ".", "../demo")
        self.assertIn("invalid epic id", err)


if __name__ == "__main__":
    unittest.main()
