# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for bench/publish.py, the publisher of saved benchmark runs.

Each test writes canned run.json + results.jsonl folders into a temp results dir and a
temp README with the bench:latest markers, then runs `bench.py publish` by subprocess
with every path pointed at the temp tree (--project, --results-dir, --docs-dir,
--readme). No git, no network, no real .work/<id>/, this repository's README, docs/ or
bench/results/ is touched — except test_run_records_task_flow_version, which reuses
test_bench.py's throwaway-repo fixture (FakeServer from test_implement.py) for one real
`bench.py run`.

Run: uv run --script plugins/task-flow/tests/test_bench_publish.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PLUGIN = TESTS.parent
BENCH_DIR = PLUGIN / "bench"
BENCH = BENCH_DIR / "bench.py"
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(TESTS))

import bench
import publish
from test_bench import BenchFixture

FP1 = "sha256:" + "cd" * 32
FP2 = "sha256:" + "ef" * 32

README_TEXT = (
    "# Test repo\n"
    "\n"
    "Some intro with zażółć gęślą jaźń.\n"
    "\n"
    "<!-- bench:latest:start -->\n"
    "<!-- bench:latest:end -->\n"
    "\n"
    "After the block.\n"
)


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def make_tree(root: Path) -> tuple[Path, Path, Path]:
    """A fresh temp tree: results dir, docs dir (absent), README with markers."""
    root.mkdir(parents=True, exist_ok=True)
    results = root / "results"
    results.mkdir()
    docs = root / "docs" / "task-flow"
    readme = root / "README.md"
    write_file(readme, README_TEXT)
    return results, docs, readme


def cli_publish(root, results, docs, readme, run_id=None, extra=(), timeout=120):
    args = [
        sys.executable,
        str(BENCH),
        "publish",
        "--project",
        str(root),
        "--results-dir",
        str(results),
        "--docs-dir",
        str(docs),
        "--readme",
        str(readme),
    ]
    if run_id is not None:
        args += ["--run", run_id]
    args += list(extra)
    return subprocess.run(
        args, capture_output=True, encoding="utf-8", timeout=timeout, check=False
    )


def canned_row(
    run_id,
    n,
    case,
    size,
    profile,
    *,
    outcome="accepted",
    reason="",
    rounds=1,
    accepted_round=None,
    hidden="pass",
    tokens=(1000, 200, 300),
    review_tokens=(0, 0, 0),
    cost=0.004,
    review_cost=0.0,
    cost_known=True,
    billing="api",
    model=None,
    settings=None,
    fingerprint=FP1,
    repeat=1,
):
    if accepted_round is None:
        accepted_round = 1 if outcome == "accepted" else None
    return {
        "run_id": run_id,
        "n": n,
        "case": case,
        "size": size,
        "profile": profile,
        "repeat": repeat,
        "outcome": outcome,
        "reason": reason,
        "rounds": rounds,
        "accepted_round": accepted_round,
        "green": outcome == "accepted",
        "hidden": hidden,
        "out_of_scope": [],
        "tokens": {"input": tokens[0], "cached": tokens[1], "output": tokens[2]},
        "cache_tokens": {"read": 0, "write": 0},
        "cost_usd": cost,
        "review_cost_usd": review_cost,
        "review_tokens": {
            "input": review_tokens[0],
            "cached": review_tokens[1],
            "output": review_tokens[2],
        },
        "cost_known": cost_known,
        "billing": billing,
        "model": model or f"{profile}-model",
        "settings": settings if settings is not None else {},
        "seconds": 1.0,
        "implement_seconds": 1.0,
        "started_at": "2026-03-04T00:00:00Z",
        "fingerprint": fingerprint,
        "harness_version": 1,
    }


def canned_run(
    results_dir,
    run_id,
    created,
    profiles,
    rows,
    *,
    cases=None,
    repeat=1,
    review=False,
    fingerprint=FP1,
    task_flow_version="0.11.0",
):
    run_dir = Path(results_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if cases is None:
        cases = sorted({r["case"] for r in rows})
    meta = {
        "run_id": run_id,
        "created": created,
        "harness_version": 1,
        "repeat": repeat,
        "review": review,
        "cases": cases,
        "case_entries": {},
        "profiles": list(profiles),
        "fingerprint": fingerprint,
    }
    if task_flow_version is not None:
        meta["task_flow_version"] = task_flow_version
    write_file(run_dir / "run.json", json.dumps(meta, indent=2, sort_keys=True) + "\n")
    write_file(
        run_dir / "results.jsonl",
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
    )
    return run_dir


A_SETTINGS = {
    "price_input_per_mtok": 2.0,
    "price_cached_input_per_mtok": 0.5,
    "price_output_per_mtok": 6.0,
}


def small_rows(run_id="r1", fingerprint=FP1):
    """2 profiles (a with prices, b subscription), cases c1 (S) and c2 (M), repeat 2
    (rows carry the repeat index 1, 2), one failed run for profile a on c2."""
    return [
        canned_row(
            run_id,
            1,
            "c1",
            "S",
            "a",
            repeat=1,
            tokens=(1000, 200, 300),
            review_tokens=(100, 0, 50),
            cost=0.004,
            review_cost=0.001,
            settings=A_SETTINGS,
            fingerprint=fingerprint,
        ),
        canned_row(
            run_id,
            2,
            "c1",
            "S",
            "a",
            repeat=2,
            tokens=(1400, 200, 500),
            review_tokens=(100, 0, 50),
            cost=0.006,
            review_cost=0.001,
            settings=A_SETTINGS,
            fingerprint=fingerprint,
        ),
        canned_row(
            run_id,
            3,
            "c1",
            "S",
            "b",
            repeat=1,
            billing="subscription",
            cost_known=False,
            cost=0.0,
            tokens=(2000, 0, 800),
            fingerprint=fingerprint,
        ),
        canned_row(
            run_id,
            4,
            "c1",
            "S",
            "b",
            repeat=2,
            billing="subscription",
            cost_known=False,
            cost=0.0,
            tokens=(2000, 0, 800),
            fingerprint=fingerprint,
        ),
        canned_row(
            run_id,
            5,
            "c2",
            "M",
            "a",
            repeat=1,
            tokens=(500, 100, 100),
            cost=0.002,
            review_cost=0.0,
            settings=A_SETTINGS,
            fingerprint=fingerprint,
        ),
        canned_row(
            run_id,
            6,
            "c2",
            "M",
            "a",
            repeat=2,
            outcome="failed",
            reason="gates failed in round 3",
            hidden="not_run",
            tokens=(300, 0, 0),
            cost=0.001,
            settings=A_SETTINGS,
            fingerprint=fingerprint,
        ),
        canned_row(
            run_id,
            7,
            "c2",
            "M",
            "b",
            repeat=1,
            billing="subscription",
            cost_known=False,
            cost=0.0,
            tokens=(600, 50, 150),
            fingerprint=fingerprint,
        ),
        canned_row(
            run_id,
            8,
            "c2",
            "M",
            "b",
            repeat=2,
            billing="subscription",
            cost_known=False,
            cost=0.0,
            tokens=(600, 50, 150),
            fingerprint=fingerprint,
        ),
    ]


def small_run(
    results_dir, run_id="r1", created="2026-03-04T05:06:07Z", fingerprint=FP1
):
    return canned_run(
        results_dir,
        run_id,
        created,
        ["a", "b"],
        small_rows(run_id, fingerprint),
        cases=["c1", "c2"],
        repeat=2,
        fingerprint=fingerprint,
    )


class PublishFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()  # expand Windows 8.3 short names
        self.results, self.docs, self.readme = make_tree(self.root)

    def bench(self, *args):
        return cli_publish(self.root, self.results, self.docs, self.readme, extra=args)

    def latest_md(self):
        return (self.docs / "benchmark" / "latest.md").read_text(encoding="utf-8")

    def latest_svg(self):
        return (self.docs / "benchmark" / "latest.svg").read_text(encoding="utf-8")

    def fresh(self, name):
        root = self.root / name
        return (root, *make_tree(root))


# ---------------------------------------------------------------------------- AC1


class SmallRunTest(PublishFixture):
    def test_small_run_table_and_chart(self):
        small_run(self.results)
        done = self.bench()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, done.stdout)
        self.assertTrue(lines[0].startswith("published r1 -> "), lines[0])
        self.assertIn(str(self.docs / "benchmark" / "latest.md"), lines[0])

        page = self.latest_md()
        self.assertEqual(
            page.splitlines()[0],
            f"<!-- bench:page run=r1 date=2026-03-04 created=2026-03-04T05:06:07Z "
            f"fingerprint={FP1} profiles=a,b -->",
        )
        self.assertIn("# Benchmark: latest result", page)
        self.assertIn("[All benchmark results](../benchmark.md)", page)
        # setup: run, cases with sizes, repeats, review, models with billing and prices,
        # date, harness version, fingerprint, task-flow version
        self.assertIn("- run: r1, 2026-03-04", page)
        self.assertIn("- cases: c1 (S), c2 (M)", page)
        self.assertIn("each case x profile ran 2 times", page)
        self.assertIn("- review: off", page)
        self.assertIn(
            "model a-model, billing api, prices $2.0000/M input, $0.5000/M cached, "
            "$6.0000/M output",
            page,
        )
        self.assertIn("model b-model, billing subscription", page)
        self.assertIn("- harness version: 1", page)
        self.assertIn(f"- fingerprint: `{FP1}`", page)
        self.assertIn("- task-flow version: 0.11.0", page)
        # one table per size plus All, with hand-computed figures
        self.assertIn("## S\n", page)
        self.assertIn("## M\n", page)
        self.assertIn("## All\n", page)
        self.assertIn(
            "| a | a-model | 2 | 2/2 (100%) | 1 | 2/2 | 1,300 / 200 / 450 | "
            "$0.0060 ($0.0050–$0.0070) | $0.0060 |",
            page,
        )
        self.assertIn(
            "| a | a-model | 2 | 1/2 (50%) | 1 | 1/2 | 800 / 100 / 100 | "
            "$0.0015 ($0.0010–$0.0020) | $0.0030 |",
            page,
        )
        self.assertIn(
            "| a | a-model | 4 | 3/4 (75%) | 1 | 3/4 | 1,133 / 167 / 333 | "
            "$0.0035 ($0.0010–$0.0070) | $0.0050 |",
            page,
        )
        self.assertIn(
            "| b | b-model | 4 | 4/4 (100%) | 1 | 4/4 | 1,300 / 25 / 475 | "
            "subscription | subscription |",
            page,
        )
        # the chart link and the failure list
        self.assertIn("](latest.svg)", page)
        failures = page.split("## Failures", 1)[1]
        self.assertIn("outcome=failed", failures)
        self.assertIn("gates failed in round 3", failures)

        svg = self.latest_svg()
        root = ET.fromstring(svg)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertEqual(root.get("role"), "img")

        def local(tag):
            return tag.rsplit("}", 1)[-1]

        rects = [el for el in root.iter() if local(el.tag) == "rect"]
        self.assertTrue(any(r.get("fill") == "#ffffff" for r in rects))  # background
        self.assertIsNotNone(root.find(".//{*}title"))
        self.assertIsNotNone(root.find(".//{*}desc"))
        groups = [el for el in root.iter() if local(el.tag) == "g"]
        self.assertEqual(len(groups), 4)  # one bar group per profile in each panel
        texts = [el.text or "" for el in root.iter() if local(el.tag) == "text"]
        self.assertIn("in 1,133 · cached 167 · out 333", texts)
        self.assertIn("in 1,300 · cached 25 · out 475", texts)
        self.assertIn("$0.0050", texts)
        self.assertIn("subscription", texts)
        for color in ("#0969da", "#54aeff", "#bf8700"):  # the three legend entries
            self.assertTrue(any(r.get("fill") == color for r in rects), color)


# ---------------------------------------------------------------------------- AC2, AC3


class DeterminismTest(PublishFixture):
    def test_deterministic_output(self):
        # two fresh trees at the same depth, so the generated relative links match
        root1, results1, docs1, readme1 = self.fresh("t1")
        root2, results2, docs2, readme2 = self.fresh("t2")
        small_run(results1)
        small_run(results2)
        first = cli_publish(root1, results1, docs1, readme1)
        second = cli_publish(root2, results2, docs2, readme2)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        for rel in (
            "benchmark/latest.md",
            "benchmark/latest.svg",
            "benchmark.md",
        ):
            self.assertEqual(
                (docs1 / rel).read_bytes(), (docs2 / rel).read_bytes(), rel
            )
        self.assertEqual(readme1.read_bytes(), readme2.read_bytes())

        def snapshot(base):
            return {
                str(p.relative_to(base)): (p.stat().st_mtime_ns, p.read_bytes())
                for p in base.rglob("*")
                if p.is_file()
            }

        before = snapshot(root1)
        again = cli_publish(root1, results1, docs1, readme1)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(snapshot(root1), before)  # no file changed at all
        for path in root1.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"\r", path.read_bytes(), str(path))


class ExtendTest(PublishFixture):
    def test_extend_adds_profile(self):
        rows_a = [r for r in small_rows() if r["profile"] == "a"]
        canned_run(
            self.results,
            "r1",
            "2026-03-04T05:06:07Z",
            ["a"],
            rows_a,
            cases=["c1", "c2"],
            repeat=2,
        )
        done = self.bench()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("b-model", self.latest_md())
        self.assertNotIn("rotated", done.stdout)

        # as `run --extend` + `save --replace` leave it: b's rows appended, profiles updated
        run_dir = self.results / "r1"
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        meta["profiles"] = ["a", "b"]
        write_file(
            run_dir / "run.json", json.dumps(meta, indent=2, sort_keys=True) + "\n"
        )
        with open(
            run_dir / "results.jsonl", "a", encoding="utf-8", newline="\n"
        ) as handle:
            handle.writelines(
                json.dumps(row, sort_keys=True) + "\n"
                for row in [r for r in small_rows() if r["profile"] == "b"]
            )

        done = self.bench()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("rotated", done.stdout)  # same run id: no history page
        self.assertFalse((self.docs / "benchmark" / "history").exists())
        page = self.latest_md()
        self.assertIn("| b | b-model | 4 | 4/4 (100%) |", page)
        self.assertIn("profiles=a,b -->", page.splitlines()[0])
        svg = self.latest_svg()
        self.assertIn(">b</text>", svg)
        self.assertIn("in 1,300 · cached 25 · out 475", svg)
        readme = self.readme.read_text(encoding="utf-8")
        self.assertIn("| b | b-model | 4/4 (100%) |", readme)


# ---------------------------------------------------------------------------- AC4, AC5, AC6


class RefusalTest(PublishFixture):
    def test_refuses_incomparable_mix(self):
        rows = small_rows()
        rows[0]["fingerprint"] = FP2  # two fingerprints inside results.jsonl
        small_run(self.results)
        run_dir = self.results / "r1"
        write_file(
            run_dir / "results.jsonl",
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
        )
        readme_before = self.readme.read_bytes()
        done = self.bench()
        self.assertEqual(done.returncode, 2)
        self.assertIn(FP1, done.stderr)
        self.assertIn(FP2, done.stderr)
        self.assertIn("new baseline", done.stderr)
        self.assertFalse((self.docs / "benchmark" / "latest.md").exists())
        self.assertFalse((self.docs / "benchmark.md").exists())
        self.assertEqual(self.readme.read_bytes(), readme_before)

        # a row fingerprint differing from run.json's
        root, results, docs, readme = self.fresh("meta-mix")
        canned_run(
            results,
            "r1",
            "2026-03-04T05:06:07Z",
            ["a", "b"],
            small_rows("r1"),
            cases=["c1", "c2"],
            repeat=2,
            fingerprint=FP2,  # rows stay FP1, run.json is FP2
        )
        done = cli_publish(root, results, docs, readme)
        self.assertEqual(done.returncode, 2)
        self.assertIn(FP1, done.stderr)
        self.assertIn(FP2, done.stderr)
        self.assertIn("new baseline", done.stderr)
        self.assertFalse((docs / "benchmark" / "latest.md").exists())
        self.assertEqual(readme.read_bytes(), (root / "README.md").read_bytes())

    def test_new_baseline_labelled(self):
        small_run(self.results, "R1", "2026-01-01T00:00:00Z", fingerprint=FP1)
        small_run(self.results, "R2", "2026-02-02T00:00:00Z", fingerprint=FP2)
        done = self.bench("--run", "R1")  # publish the older run first
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        done = self.bench()  # no --run: the newest saved run, R2
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(done.stdout.strip().endswith("; new baseline"), done.stdout)
        self.assertIn("rotated R1", done.stdout)
        self.assertIn(
            "New baseline: the suite changed since run R1; results are not comparable "
            "with earlier runs.",
            self.latest_md(),
        )
        readme = self.readme.read_text(encoding="utf-8")
        self.assertIn("New baseline: the suite changed since run R1", readme)
        index = (self.docs / "benchmark.md").read_text(encoding="utf-8")
        r2_line = next(
            l for l in index.splitlines() if l.startswith("| 2026-02-02 | R2 |")
        )
        self.assertIn("new baseline", r2_line)
        self.assertTrue(
            (self.docs / "benchmark" / "history" / "2026-01-01-R1.md").is_file()
        )

    def test_readme_without_markers_refused(self):
        small_run(self.results)
        variants = {
            "no markers": "# T\n\nno block here\n",
            "only start": "# T\n\n<!-- bench:latest:start -->\n\ntext\n",
            "only end": "# T\n\n<!-- bench:latest:end -->\n\ntext\n",
            "duplicated start": (
                "# T\n\n<!-- bench:latest:start -->\n"
                "<!-- bench:latest:start -->\n<!-- bench:latest:end -->\n"
            ),
            "end before start": (
                "# T\n\n<!-- bench:latest:end -->\n<!-- bench:latest:start -->\n"
            ),
        }
        for name, text in variants.items():
            with self.subTest(name):
                self.readme.write_bytes(text.encode("utf-8"))
                done = self.bench()
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn(str(self.readme), done.stderr)
                self.assertIn("<!-- bench:latest:start -->", done.stderr)
                self.assertIn("<!-- bench:latest:end -->", done.stderr)
                self.assertFalse((self.docs / "benchmark" / "latest.md").exists())
                self.assertFalse((self.docs / "benchmark.md").exists())
                self.assertEqual(self.readme.read_bytes(), text.encode("utf-8"))


# ---------------------------------------------------------------------------- AC7, AC8


class MarkerBlockTest(PublishFixture):
    def test_rewrite_between_markers_only(self):
        small_run(self.results)
        original = (
            "# Repo\n"
            "\r\n"
            "Zażółć gęślą jaźń — intro.\r\n"
            "\r\n"
            "<!-- bench:latest:start -->\r\n"
            "old block\r\n"
            "<!-- bench:latest:end -->\r\n"
            "\r\n"
            "After — ünicode tail.\r\n"
        ).encode()
        self.readme.write_bytes(original)
        done = self.bench()
        self.assertEqual(done.returncode, 0, done.stderr)
        new = self.readme.read_bytes()
        start = b"<!-- bench:latest:start -->"
        end = b"<!-- bench:latest:end -->"
        i = original.index(start) + len(start)
        if original[i : i + 2] == b"\r\n":
            i += 2
        elif original[i : i + 1] in (b"\n", b"\r"):
            i += 1
        prefix = original[:i]  # up to and including the start-marker line
        suffix = original[original.index(end) :]  # from the end-marker line on
        self.assertTrue(new.startswith(prefix))
        self.assertTrue(new.endswith(suffix))
        self.assertNotIn(b"old block", new)
        self.assertIn(b"Latest benchmark:", new)

        # the replace_block unit cases cover the same rules
        self.assertEqual(
            publish.replace_block("a\nS\nold\nE\nb\n", "S", "E", "X\n"),
            "a\nS\nX\nE\nb\n",
        )
        self.assertEqual(
            publish.replace_block(
                "a\nS\nold\nE\nb\n", "S", "E", "X"
            ),  # body gets a newline
            "a\nS\nX\nE\nb\n",
        )
        self.assertEqual(
            publish.replace_block("a\r\nS\r\nold\r\nE\r\nb\r\n", "S", "E", "X\n"),
            "a\r\nS\r\nX\r\nE\r\nb\r\n",
        )
        for bad in ("a\nS\nS\nE\n", "a\nS\n", "a\nE\n", "a\nE\nS\n"):
            with self.assertRaises(publish.MarkerError):
                publish.replace_block(bad, "S", "E", "X\n")


class HistoryTest(PublishFixture):
    def test_history_rotation(self):
        rows_a = [r for r in small_rows("R1") if r["profile"] == "a"]
        canned_run(
            self.results,
            "R1",
            "2026-01-01T00:00:00Z",
            ["a"],
            rows_a,
            cases=["c1", "c2"],
            repeat=2,
            fingerprint=FP1,
        )
        small_run(self.results, "R2", "2026-02-02T00:00:00Z", fingerprint=FP1)
        write_file(
            self.docs / "benchmark.md",
            "# Benchmark\n"
            "\n"
            "Hand-written prose.\n"
            "\n"
            "<!-- bench:history:start -->\n"
            "<!-- bench:history:end -->\n"
            "\n"
            "More prose.\n",
        )
        done = self.bench("--run", "R1")
        self.assertEqual(done.returncode, 0, done.stderr)
        r1_latest = (self.docs / "benchmark" / "latest.md").read_bytes()
        r1_svg = (self.docs / "benchmark" / "latest.svg").read_bytes()
        index_before = (self.docs / "benchmark.md").read_bytes()

        done = self.bench("--run", "R2")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("rotated R1 -> ", done.stdout)

        history = self.docs / "benchmark" / "history"
        expected = (
            r1_latest.decode("utf-8")
            .replace("](latest.svg)", "](2026-01-01-R1.svg)")
            .replace("](../benchmark.md)", "](../../benchmark.md)")
        )
        rotated = (history / "2026-01-01-R1.md").read_text(encoding="utf-8")
        self.assertEqual(rotated, expected)
        self.assertNotEqual(rotated, r1_latest.decode("utf-8"))  # links rewritten
        self.assertEqual((history / "2026-01-01-R1.svg").read_bytes(), r1_svg)

        index = (self.docs / "benchmark.md").read_text(encoding="utf-8")
        r2_line = next(
            l for l in index.splitlines() if l.startswith("| 2026-02-02 | R2 |")
        )
        r1_line = next(
            l for l in index.splitlines() if l.startswith("| 2026-01-01 | R1 |")
        )
        self.assertIn("latest", r2_line)
        self.assertIn("history/2026-01-01-R1.md", r1_line)
        self.assertLess(index.index(r2_line), index.index(r1_line))  # newest first
        # the text outside the history markers is unchanged
        start = b"<!-- bench:history:start -->"
        end = b"<!-- bench:history:end -->"
        i = index_before.index(start) + len(start)
        if index_before[i : i + 2] == b"\r\n":
            i += 2
        elif index_before[i : i + 1] == b"\n":
            i += 1
        self.assertTrue(index.encode("utf-8").startswith(index_before[:i]))
        self.assertTrue(
            index.encode("utf-8").endswith(index_before[index_before.index(end) :])
        )

        # an existing benchmark.md without markers is refused
        root, results, docs, readme = self.fresh("no-history-markers")
        small_run(results)
        write_file(docs / "benchmark.md", "# Benchmark\n\nno markers here\n")
        done = cli_publish(root, results, docs, readme)
        self.assertEqual(done.returncode, 2)
        self.assertIn(str(docs / "benchmark.md"), done.stderr)
        self.assertIn("<!-- bench:history:start -->", done.stderr)
        self.assertIn("<!-- bench:history:end -->", done.stderr)
        self.assertFalse((docs / "benchmark" / "latest.md").exists())

        # publishing the older R1 again after R2 is refused
        done = self.bench("--run", "R1")
        self.assertEqual(done.returncode, 2)
        self.assertIn("older", done.stderr)


# ---------------------------------------------------------------------------- AC9, AC10


class HonestyTest(PublishFixture):
    def test_honesty_rules(self):
        # a profile missing a case is (partial) in the tables, the chart and the README
        root, results, docs, readme = self.fresh("missing-case")
        rows = [
            canned_row("p", 1, "c1", "S", "a"),
            canned_row("p", 2, "c1", "S", "a", tokens=(1100, 200, 300)),
        ]
        canned_run(
            results, "p", "2026-05-05T00:00:00Z", ["a"], rows, cases=["c1", "c2"]
        )
        done = cli_publish(root, results, docs, readme)
        self.assertEqual(done.returncode, 0, done.stderr)
        page = (docs / "benchmark" / "latest.md").read_text(encoding="utf-8")
        self.assertIn("| a (partial) |", page)
        self.assertIn("missing 1 of 2 (case, repeat) runs", page)
        self.assertIn(
            "a (partial)", (docs / "benchmark" / "latest.svg").read_text("utf-8")
        )
        self.assertIn("a (partial)", readme.read_text(encoding="utf-8"))

        # a skipped row makes the profile (partial) with the reason named
        root, results, docs, readme = self.fresh("skipped")
        rows = [
            canned_row("s", 1, "c1", "S", "b"),
            canned_row(
                "s",
                2,
                "c1",
                "S",
                "b",
                outcome="skipped",
                reason="cost cap",
                hidden="not_run",
            ),
        ]
        canned_run(results, "s", "2026-05-06T00:00:00Z", ["b"], rows, cases=["c1"])
        done = cli_publish(root, results, docs, readme)
        self.assertEqual(done.returncode, 0, done.stderr)
        page = (docs / "benchmark" / "latest.md").read_text(encoding="utf-8")
        self.assertIn("| b (partial) |", page)
        self.assertIn("skipped: cost cap", page)

        # 199 accepted of 200 shows 99%, never 100%
        root, results, docs, readme = self.fresh("percent")
        rows = [
            canned_row(
                "big",
                i,
                "c1",
                "S",
                "p",
                outcome="accepted" if i < 200 else "failed",
                reason="" if i < 200 else "never green",
                hidden="pass" if i < 200 else "not_run",
            )
            for i in range(1, 201)
        ]
        canned_run(results, "big", "2026-05-07T00:00:00Z", ["p"], rows, cases=["c1"])
        done = cli_publish(root, results, docs, readme)
        self.assertEqual(done.returncode, 0, done.stderr)
        page = (docs / "benchmark" / "latest.md").read_text(encoding="utf-8")
        self.assertIn("199/200 (99%)", page)
        self.assertNotIn("199/200 (100%)", page)

        # a subscription profile: `subscription` in the cost columns and the chart
        # panel, with no cost bar
        root, results, docs, readme = self.fresh("subscription")
        small_run(results)
        done = cli_publish(root, results, docs, readme)
        self.assertEqual(done.returncode, 0, done.stderr)
        page = (docs / "benchmark" / "latest.md").read_text(encoding="utf-8")
        self.assertIn("| subscription | subscription |", page)
        svg = (docs / "benchmark" / "latest.svg").read_text(encoding="utf-8")
        self.assertIn("subscription", svg)
        svg_root = ET.fromstring(svg)
        cost_rects = [
            el
            for el in svg_root.iter()
            if el.tag.endswith("rect") and el.get("fill") == "#1a7f37"
        ]
        self.assertEqual(len(cost_rects), 1)  # only profile a has a cost bar

        # 0 accepted: `n/a (0 accepted)` and no bars at all
        root, results, docs, readme = self.fresh("zero")
        rows = [
            canned_row(
                "z",
                1,
                "c1",
                "S",
                "z",
                outcome="failed",
                reason="never green",
                hidden="not_run",
                cost=0.001,
            )
        ]
        canned_run(results, "z", "2026-05-08T00:00:00Z", ["z"], rows, cases=["c1"])
        done = cli_publish(root, results, docs, readme)
        self.assertEqual(done.returncode, 0, done.stderr)
        page = (docs / "benchmark" / "latest.md").read_text(encoding="utf-8")
        self.assertIn("n/a (0 accepted)", page)
        svg = (docs / "benchmark" / "latest.svg").read_text(encoding="utf-8")
        self.assertIn("n/a (0 accepted)", svg)
        svg_root = ET.fromstring(svg)
        rects = [el for el in svg_root.iter() if el.tag.endswith("rect")]
        self.assertEqual(len(rects), 4)  # background + 3 legend swatches, no bars

        # the repeat count and the min–max spread appear
        root, results, docs, readme = self.fresh("spread")
        small_run(results)
        done = cli_publish(root, results, docs, readme)
        self.assertEqual(done.returncode, 0, done.stderr)
        page = (docs / "benchmark" / "latest.md").read_text(encoding="utf-8")
        self.assertIn("each case x profile ran 2 times", page)
        self.assertIn("$0.0050–$0.0070", page)
        self.assertIn("medians with min–max", page)


class MergedRunNotesTest(PublishFixture):
    def test_merge_and_turn_limit_notes_and_hidden_column(self):
        run_dir = small_run(self.results)
        nl = chr(10)
        rows = [
            json.loads(line)
            for line in (run_dir / "results.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        for i, row in enumerate(rows):
            if row["profile"] == "a":
                row["settings"] = {
                    **row.get("settings", {}),
                    "max_turns": 60 if i % 2 else 100,
                }
        write_file(
            run_dir / "results.jsonl",
            "".join(json.dumps(r, sort_keys=True) + nl for r in rows),
        )
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        meta["merged_from"] = ["first", "second"]
        write_file(
            run_dir / "run.json", json.dumps(meta, indent=2, sort_keys=True) + nl
        )

        done = self.bench()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        page = self.latest_md()
        self.assertIn("this run merges rows of the runs `first`, `second`", page)
        self.assertIn("profile `a` ran with different turn limits", page)
        self.assertNotIn("profile `b` ran with different turn limits", page)
        block = self.readme.read_text(encoding="utf-8")
        self.assertIn("| Profile | Model | Accepted | Hidden pass |", block)
        self.assertIn("Note: this run merges rows of the runs", block)
        self.assertIn("Note: profile `a` ran with different turn limits", block)

    def test_plain_run_has_no_merge_or_turn_notes(self):
        small_run(self.results)
        self.assertEqual(self.bench().returncode, 0)
        page = self.latest_md()
        self.assertNotIn("this run merges", page)
        self.assertNotIn("different turn limits", page)


class SelectionTest(PublishFixture):
    def test_newest_run_selection_and_refusals(self):
        rows_a = [
            canned_row("R1", 1, "c1", "S", "a"),
            canned_row("R1", 2, "c1", "S", "a"),
        ]
        canned_run(
            self.results, "R1", "2026-01-01T00:00:00Z", ["a"], rows_a, cases=["c1"]
        )
        canned_run(
            self.results, "R2", "2026-03-03T00:00:00Z", ["a"], rows_a, cases=["c1"]
        )
        done = self.bench()  # no --run: the run with the latest `created`
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, done.stdout)
        self.assertTrue(lines[0].startswith("published R2 -> "), lines[0])

        root, results, docs, readme = self.fresh("empty")
        done = cli_publish(root, results, docs, readme)
        self.assertEqual(done.returncode, 2)
        self.assertIn("no saved runs", done.stderr)

        done = self.bench("--run", "nope")
        self.assertEqual(done.returncode, 2)
        self.assertIn("nope", done.stderr)

        run_dir = self.results / "R3"
        run_dir.mkdir()
        write_file(
            run_dir / "run.json",
            json.dumps(
                {
                    "run_id": "R3",
                    "created": "2026-04-04T00:00:00Z",
                    "harness_version": 1,
                    "repeat": 1,
                    "review": False,
                    "cases": ["c1"],
                    "case_entries": {},
                    "profiles": ["a"],
                    "fingerprint": FP1,
                }
            )
            + "\n",
        )
        write_file(run_dir / "results.jsonl", '{"n": 1}\nnot json\n')
        done = self.bench("--run", "R3")
        self.assertEqual(done.returncode, 2)
        self.assertIn("invalid JSON line", done.stderr)

    def test_same_day_runs_order_by_created(self):
        # two runs on the same day: the lower id has the later `created`, so ordering
        # by date plus run id (as the old check did) disagrees with `newest_run`
        canned_run(
            self.results,
            "zz",
            "2026-03-04T05:00:00Z",
            ["a"],
            [canned_row("zz", 1, "c1", "S", "a")],
            cases=["c1"],
        )
        canned_run(
            self.results,
            "aa",
            "2026-03-04T09:00:00Z",
            ["a"],
            [canned_row("aa", 1, "c1", "S", "a")],
            cases=["c1"],
        )
        done = self.bench("--run", "zz")  # publish the earlier run first
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        first = self.latest_md().splitlines()[0]
        self.assertIn("created=2026-03-04T05:00:00Z", first)
        meta = publish.parse_page_meta(self.latest_md())
        self.assertEqual(meta["run"], "zz")
        self.assertEqual(meta["date"], "2026-03-04")
        self.assertEqual(meta["created"], "2026-03-04T05:00:00Z")
        # the parse still accepts a page written without created=
        legacy = publish.parse_page_meta(
            f"<!-- bench:page run=x date=2026-01-01 fingerprint={FP1} profiles=a -->"
        )
        self.assertEqual(legacy["created"], "2026-01-01")

        done = self.bench()  # no --run: newest by full created is aa, not refused
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith("published aa -> "), done.stdout)
        self.assertIn("rotated zz -> ", done.stdout)
        self.assertIn("created=2026-03-04T09:00:00Z", self.latest_md().splitlines()[0])

        # publishing the earlier zz after aa is refused even though zz > aa by id
        done = self.bench("--run", "zz")
        self.assertEqual(done.returncode, 2)
        self.assertIn("older", done.stderr)
        self.assertIn("aa", done.stderr)
        self.assertIn("2026-03-04T09:00:00Z", done.stderr)


# ---------------------------------------------------------------------------- AC11


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class RunVersionTest(BenchFixture):
    def test_run_records_task_flow_version(self):
        server = self.fix_calc_server()
        self.add_profile("fake", server)
        self.make_case()
        done = self.run_bench("r1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        meta = json.loads((self.out / "r1" / "run.json").read_text(encoding="utf-8"))
        plugin = json.loads(
            (PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(meta["task_flow_version"], plugin["version"])
        # the fingerprint of the same inputs is unchanged by the new key
        cases = bench.select_cases(self.cases, None)
        fp, _payload = bench.fingerprint(cases, 1, False)
        self.assertEqual(meta["fingerprint"], fp)
        self.assertNotIn("task_flow_version", meta["case_entries"])


class RoutingEvidenceTest(unittest.TestCase):
    def test_routing_groups_preserve_protocol_rules_and_unknown_cost(self) -> None:
        evidence = {
            "schema": "implementer-routing/1",
            "band": "S",
            "selected_profile": "small",
            "rule_digest": "rules-a",
        }
        rows = [
            {
                "n": i,
                "case": "large-case",
                "size": "L",
                "profile": "small",
                "repeat": i,
                "routing": evidence,
                "fingerprint": "protocol-a",
                "review": False,
                "outcome": "accepted" if i < 3 else "failed",
                "hidden": "pass" if i < 3 else "fail",
                "cost_usd": float(i),
                "cost_known": True,
            }
            for i in range(1, 4)
        ]
        groups = bench.routing_summary(rows, {})
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["band"], "S")
        self.assertEqual(groups[0]["repeats"], 3)
        self.assertEqual(groups[0]["accepted"], 2)
        self.assertEqual(groups[0]["hidden_pass"], 2)
        self.assertEqual(groups[0]["cost_median"], 2.0)
        self.assertEqual(groups[0]["cost_spread"], [1.0, 3.0])
        for change in [
            {"fingerprint": "protocol-b"},
            {"review": True},
            {"routing": dict(evidence, rule_digest="rules-b")},
        ]:
            self.assertEqual(
                len(bench.routing_summary(rows + [dict(rows[0], **change)], {})), 2
            )
        unknown = bench.routing_summary(
            rows + [dict(rows[0], cost_usd=None, cost_known=False)], {}
        )[0]
        self.assertIsNone(unknown["cost_median"])
        legacy = bench.routing_summary([dict(rows[0], routing=None)], {})[0]
        self.assertIsNone(legacy["band"])
        self.assertIsNone(legacy["selected_profile"])
        self.assertIn(
            "missing routing evidence is unknown",
            "\n".join(bench.routing_report(rows, {})),
        )

    def test_published_summary_contains_computed_evidence(self) -> None:
        # Existing row fixtures exercise all ordinary publisher fields.
        rows = small_rows()
        meta = {
            "run_id": "r1",
            "cases": ["c1", "c2"],
            "profiles": ["a", "b"],
            "repeat": 2,
            "fingerprint": FP1,
            "review": False,
        }
        rows[0]["routing"] = {
            "schema": "implementer-routing/1",
            "band": "S",
            "selected_profile": "routed",
            "rule_digest": "rules",
        }
        summary = publish.summarize(meta, rows)
        self.assertTrue(
            any(group["selected_profile"] == "routed" for group in summary["routing"])
        )
        self.assertIn("Computed routing evidence", publish.render_latest(summary, None))


if __name__ == "__main__":
    unittest.main()
