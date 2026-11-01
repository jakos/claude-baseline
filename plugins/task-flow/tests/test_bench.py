# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for bench/bench.py, the benchmark harness.

Each test builds a throwaway source git repository (a base commit with a broken
`calc.py` and visible check scripts, a result commit adding `hidden_check.py` with a
marker string), a case folder, and a project `.claude/task-flow.models.toml` pointing
at a FakeServer imported from test_implement.py. bench.py runs by subprocess with
HOME/USERPROFILE pointed at a temp dir, `--out` and `--results-dir` always in the temp
dir. No real keys, no network beyond 127.0.0.1, no real `.work/<id>/` and no
repository state is touched.

Run: uv run --script plugins/task-flow/tests/test_bench.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tomllib

TESTS = Path(__file__).resolve().parent
PLUGIN = TESTS.parent
BENCH_DIR = PLUGIN / "bench"
BENCH = BENCH_DIR / "bench.py"
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(TESTS))

import bench
from test_implement import (
    FakeServer,
    command,
    reply,
    toml_value,
    tool_call,
)

CALC_BASE = "def add(a, b):\n    return a + b - 1\n"
CALC_WRONG2 = "def add(a, b):\n    return a + b + 5\n"
CALC_WRONG3 = "def add(a, b):\n    return a + b + 2\n"
CALC_OK = "def add(a, b):\n    return a + b\n"
MUL_BASE = "def mul(a, b):\n    return a * b - 1\n"
MUL_OK = "def mul(a, b):\n    return a * b\n"
MUL_BAD = "def mul(a, b):\n    return a * b + 94\n"

CHECK_PY = """import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import calc
got = calc.add(2, 3)
if got != 5:
    print("FAIL: calc.add(2, 3) returned %d, expected 5" % got)
    sys.exit(1)
print("OK")
"""

CHECK2_PY = """import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import mul
got = mul.mul(2, 3)
if got != 6:
    print("FAIL: mul(2, 3) returned %d, expected 6" % got)
    sys.exit(1)
print("OK")
"""

HIDDEN_MARKER = "hidden-marker-9f3a2b"
HIDDEN_PY = (
    "import sys\n"
    f"MARKER = {HIDDEN_MARKER!r}\n"
    "import calc\n"
    "got = calc.add(2, 3)\n"
    "if got != 5:\n"
    '    print("FAIL: hidden check failed: calc.add(2, 3) returned %d" % got)\n'
    "    sys.exit(1)\n"
    'print("HIDDEN OK")\n'
)
HIDDEN_FAIL_PY = 'print("FAIL: hidden check failed: always")\nsys.exit(1)\n'

U1 = {
    "prompt_tokens": 1000,
    "completion_tokens": 500,
    "prompt_tokens_details": {"cached_tokens": 400},
}
U2 = {"prompt_tokens": 100, "completion_tokens": 50}


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class BenchFixture(unittest.TestCase):
    """A throwaway source repo, a case folder, a project models file and helpers."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        self.src = root / "src-repo"
        self.src.mkdir()
        self.git(self.src, "init", "-q", "-b", "main")
        self.git(self.src, "config", "user.email", "t@example.com")
        self.git(self.src, "config", "user.name", "t")
        self.git(self.src, "config", "core.autocrlf", "false")
        write_file(self.src / "calc.py", CALC_BASE)
        write_file(self.src / "mul.py", MUL_BASE)
        write_file(self.src / "tools" / "check.py", CHECK_PY)
        write_file(self.src / "tools" / "check2.py", CHECK2_PY)
        self.git(self.src, "add", "-A")
        self.git(self.src, "commit", "-q", "-m", "base")
        self.base_sha = self.git(self.src, "rev-parse", "HEAD").stdout.strip()
        write_file(self.src / "hidden_check.py", HIDDEN_PY)
        self.git(self.src, "add", "-A")
        self.git(self.src, "commit", "-q", "-m", "result")
        self.result_sha = self.git(self.src, "rev-parse", "HEAD").stdout.strip()
        self.cases = root / "cases"
        self.cases.mkdir()
        self.out = root / "out"
        self.results_dir = root / "results"
        self.project = root / "project"
        (self.project / ".claude").mkdir(parents=True)
        self.models_path = self.project / ".claude" / "task-flow.models.toml"
        self.models_path.write_text("", encoding="utf-8", newline="\n")
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.close()

    def git(self, cwd, *args, check=True):
        done = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            encoding="utf-8",
            timeout=60,
            check=check,
        )
        return done

    def server(self, responses):
        server = FakeServer(responses)
        self.servers.append(server)
        return server

    def add_profile(self, name, server, **overrides):
        settings = {
            "driver": "openai-loop",
            "base_url": server.url,
            "model": f"{name}-model",
            "api_key_env": "FAKE_KEY",
            "billing": "api",
            "retries": 0,
            "retry_base_seconds": 0,
            "request_timeout_seconds": 30,
            "price_input_per_mtok": 2.0,
            "price_cached_input_per_mtok": 0.5,
            "price_output_per_mtok": 6.0,
        }
        settings.update(overrides)
        lines = [f"[profiles.{name}]"]
        lines += [f"{k} = {toml_value(v)}" for k, v in settings.items()]
        with open(self.models_path, "a", encoding="utf-8", newline="") as handle:
            handle.write("\n".join(lines) + "\n")

    def plan_text(self, commands):
        return "\n".join(
            [
                "# Plan: fix calc",
                "## Goal",
                "Make the verification commands pass.",
                "## Profiles",
                "python: `calc.py`, `tools/`",
                "## Steps",
                "1. Fix `calc.py` so `tools/check.py` passes.",
                "## Verification commands",
                "```",
                *[str(c) for c in commands],
                "```",
                "",
            ]
        )

    def make_case(
        self,
        cid="c1",
        size="S",
        commands=None,
        hidden_tests=None,
        hidden_commands=None,
        hidden_body=None,
        repo=None,
        base=None,
        result=None,
        plan=None,
        extra_toml="",
    ):
        case_dir = self.cases / cid
        case_dir.mkdir(parents=True, exist_ok=True)
        if commands is None:
            commands = [command(sys.executable, "tools/check.py")]
        if hidden_tests is None:
            hidden_tests = ["hidden_check.py"]
        if hidden_commands is None:
            hidden_commands = [command(sys.executable, "hidden_check.py")]
        if hidden_body is not None:
            # a second result commit carrying the given hidden file
            write_file(self.src / "hidden_check.py", hidden_body)
            self.git(self.src, "add", "-A")
            self.git(self.src, "commit", "-q", "-m", "result2")
            result = self.git(self.src, "rev-parse", "HEAD").stdout.strip()
        if repo is None:
            repo = str(self.src)
        if base is None:
            base = self.base_sha
        if result is None:
            result = self.result_sha
        if plan is None:
            plan = self.plan_text(commands)
        write_file(case_dir / "plan.md", plan)
        toml = {
            "id": cid,
            "size": size,
            "repo": repo,
            "base": base,
            "result": result,
            "hidden_tests": hidden_tests,
            "commands": [str(c) for c in hidden_commands],
        }
        lines = [f"{k} = {toml_value(v)}" for k, v in toml.items()]
        write_file(case_dir / "case.toml", "\n".join(lines) + "\n" + extra_toml)
        return case_dir

    # ------------------------------------------------------------------ bench CLI

    def env(self, extra=None):
        environment = dict(os.environ)
        environment["HOME"] = str(self.home)
        environment["USERPROFILE"] = str(self.home)
        environment["FAKE_KEY"] = "fake-key"
        environment["NO_PROXY"] = "127.0.0.1"
        environment["no_proxy"] = "127.0.0.1"
        environment.update(extra or {})
        return environment

    def bench(self, *args, extra_env=None):
        cmd = [sys.executable, str(BENCH), *args]
        return subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            env=self.env(extra_env),
            timeout=900,
            check=False,
        )

    def run_args(self, run_id="r1", profiles="fake", extra=()):
        args = [
            "run",
            "--profiles",
            profiles,
            "--run-id",
            run_id,
            "--out",
            str(self.out),
            "--cases-dir",
            str(self.cases),
            "--project",
            str(self.project),
            "--timeout-seconds",
            "120",
        ]
        return args + list(extra)

    def run_bench(self, *args, **kwargs):
        return self.bench(*self.run_args(*args, **kwargs))

    def rows(self, run_id="r1"):
        path = self.out / run_id / "results.jsonl"
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def report(self, run_id="r1"):
        return (self.out / run_id / "report.md").read_text(encoding="utf-8")

    def fix_calc_server(self, usage=U1):
        """A server whose model fixes calc.py in round 1 and finishes."""
        return self.server(
            [
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_OK}),
                    usage=usage,
                ),
                reply(content="done", usage=U2),
            ]
        )

    def tree(self, path):
        if not path.exists():
            return None
        return sorted(p.relative_to(path).as_posix() for p in Path(path).rglob("*"))


# ---------------------------------------------------------------------------- units


class ParseChecksTest(unittest.TestCase):
    CHECKS0 = (
        "# Checks: round 0\n"
        "\n"
        "| # | Command | Result | Exit | Duration |\n"
        "| --- | --- | --- | --- | --- |\n"
        '| 1 | `"py" x.py` | FAIL | 1 | <1s |\n'
        '| 2 | `"py" y.py` | PASS | 0 | <1s |\n'
        "\n"
        "## Failures\n"
        '### 1. `"py" x.py`\n'
        "```\n"
        "FAIL: boom 4\n"
        "```\n"
        "\n"
        "## Not run\n"
        '- 3. `"py" z.py` — program not found: z\n'
    )
    CHECKS1 = CHECKS0.replace("round 0", "round 1").replace(
        "FAIL: boom 4", "FAIL: boom 10"
    )

    def test_parse_checks(self):
        parsed = bench.parse_checks(self.CHECKS0)
        self.assertEqual(parsed[1]["result"], "FAIL")
        self.assertEqual(parsed[1]["command"], '"py" x.py')
        self.assertEqual(parsed[1]["exit"], "1")
        self.assertEqual(parsed[1]["excerpt"], ["FAIL: boom 4"])
        self.assertEqual(parsed[2]["result"], "PASS")
        self.assertEqual(parsed[3]["result"], "NOTRUN")
        self.assertEqual(parsed[3]["excerpt"], ["program not found: z"])

    def test_introduced(self):
        checks0 = bench.parse_checks(self.CHECKS0)
        same = bench.parse_checks(self.CHECKS0.replace("round 0", "round 1"))
        self.assertEqual(bench.introduced(checks0, same), [])
        changed = bench.parse_checks(self.CHECKS1)
        self.assertEqual(bench.introduced(checks0, changed), [1])

    def test_fix_list_review(self):
        checks1 = bench.parse_checks(self.CHECKS1)
        text = bench.fix_list_review(1, checks1, [1])
        self.assertIn("## Verdict: CHANGES_REQUESTED", text)
        self.assertIn('Make `"py" x.py` pass (checks-1.md #1, FAIL, exit 1):', text)
        self.assertIn("FAIL: boom 10", text)
        self.assertIn("## Pre-existing failures", text)

    def test_in_scope_rules(self):
        plan = """# Plan: x
## Profiles
python: `calc.py`, `tools/`, `docs/*.md`
## Steps
1. Change `src/app.py` and see `README.md`.
## Verification commands
```
true
```
"""
        self.assertTrue(bench.in_scope("calc.py", plan))  # exact token
        self.assertTrue(bench.in_scope("tools/check.py", plan))  # parent directory
        self.assertTrue(bench.in_scope("docs/x.md", plan))  # glob
        self.assertTrue(bench.in_scope("src/app.py", plan))  # backtick span in Steps
        self.assertTrue(bench.in_scope("README.md", plan))
        self.assertFalse(bench.in_scope("other.py", plan))
        self.assertFalse(bench.in_scope("toolsx/check.py", plan))


# ---------------------------------------------------------------------------- cases (AC1, AC2)


class CaseTest(BenchFixture):
    def test_case_toml_validation(self):
        good = self.make_case()
        case = bench.load_case(good)
        self.assertEqual(case["id"], "c1")
        self.assertEqual(case["size"], "S")

        def bad(cid, **overrides):
            toml = {
                "id": cid,
                "size": "S",
                "repo": str(self.src),
                "base": self.base_sha,
                "result": self.result_sha,
                "hidden_tests": ["hidden_check.py"],
                "commands": ["true"],
            }
            toml.update(overrides)
            case_dir = self.cases / cid
            case_dir.mkdir(parents=True, exist_ok=True)
            write_file(case_dir / "plan.md", "# Plan\n")
            write_file(
                case_dir / "case.toml",
                "\n".join(f"{k} = {toml_value(v)}" for k, v in toml.items()) + "\n",
            )
            with self.assertRaises(bench.CaseError) as ctx:
                bench.load_case(case_dir)
            message = str(ctx.exception)
            self.assertIn("case.toml", message, message)
            return message

        message = bad("b1", zzz=1)
        self.assertIn("unknown key", message)
        message = bad("b2", size="XL")
        self.assertIn("size", message)
        message = bad("b3", id="other")
        self.assertIn("folder name", message)
        message = bad("b4", hidden_tests=[])
        self.assertIn("hidden_tests", message)
        message = bad("b5", hidden_tests=["../secret.py"])
        self.assertIn("hidden_tests", message)
        message = bad("b6", hidden_tests=["/abs/path.py"])
        self.assertIn("hidden_tests", message)
        message = bad("b7", commands=[])
        self.assertIn("commands", message)

    def test_list_command(self):
        self.make_case("c1", size="M")
        done = self.bench("list", "--cases-dir", str(self.cases))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout, f"c1  M  {self.src}  {self.base_sha}  {self.result_sha}\n"
        )
        (self.cases / "broken").mkdir()
        write_file(self.cases / "broken" / "case.toml", 'id = "broken"\nsize = "Q"\n')
        done = self.bench("list", "--cases-dir", str(self.cases))
        self.assertEqual(done.returncode, 2)
        self.assertIn("case.toml", done.stderr)
        empty = self.root / "empty-cases"
        empty.mkdir()
        done = self.bench("list", "--cases-dir", str(empty))
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout, f"no cases in {empty}\n")

    def test_fingerprint_stable_and_sensitive(self):
        case_dir = self.make_case()
        cases = {"c1": bench.load_case(case_dir)}
        fp1, _payload1 = bench.fingerprint(cases, 1, False)
        fp2, _ = bench.fingerprint({"c1": bench.load_case(case_dir)}, 1, False)
        self.assertEqual(fp1, fp2)
        self.assertTrue(fp1.startswith("sha256:"))
        # a CRLF copy of the plan gives the same fingerprint
        plan = case_dir / "plan.md"
        original = plan.read_text(encoding="utf-8")
        plan.write_bytes(original.replace("\n", "\r\n").encode("utf-8"))
        fp3, _ = bench.fingerprint({"c1": bench.load_case(case_dir)}, 1, False)
        self.assertEqual(fp1, fp3)
        plan.write_text(original, encoding="utf-8", newline="\n")
        # a changed plan
        plan.write_text(original + "\nExtra step.\n", encoding="utf-8", newline="\n")
        fp4, _ = bench.fingerprint({"c1": bench.load_case(case_dir)}, 1, False)
        self.assertNotEqual(fp1, fp4)
        plan.write_text(original, encoding="utf-8", newline="\n")
        # a changed case.toml
        toml_path = case_dir / "case.toml"
        toml_original = toml_path.read_text(encoding="utf-8")
        toml_path.write_text(
            toml_original.replace('size = "S"', 'size = "M"'),
            encoding="utf-8",
            newline="\n",
        )
        fp5, _ = bench.fingerprint({"c1": bench.load_case(case_dir)}, 1, False)
        self.assertNotEqual(fp1, fp5)
        toml_path.write_text(toml_original, encoding="utf-8", newline="\n")
        # a changed base / result / repeat / review
        fp6, _ = bench.fingerprint({"c1": bench.load_case(case_dir)}, 1, False)
        self.assertEqual(fp1, fp6)
        cases_b = {"c1": bench.load_case(case_dir)}
        cases_b["c1"]["base"] = self.result_sha
        fp7, _ = bench.fingerprint(cases_b, 1, False)
        self.assertNotEqual(fp1, fp7)
        cases_r = {"c1": bench.load_case(case_dir)}
        cases_r["c1"]["result"] = self.base_sha
        fp8, _ = bench.fingerprint(cases_r, 1, False)
        self.assertNotEqual(fp1, fp8)
        fp9, _ = bench.fingerprint({"c1": bench.load_case(case_dir)}, 2, False)
        self.assertNotEqual(fp1, fp9)
        fp10, _ = bench.fingerprint({"c1": bench.load_case(case_dir)}, 1, True)
        self.assertNotEqual(fp1, fp10)


# ---------------------------------------------------------------------------- runs


class RunTest(BenchFixture):
    def test_run_accepted_first_round(self):
        server = self.fix_calc_server()
        self.add_profile("fake", server)
        self.make_case()
        done = self.run_bench("r1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, done.stdout)
        self.assertEqual(
            lines[0],
            f"bench r1: 1 runs, 1 accepted, $0.0049 -> {self.out / 'r1' / 'report.md'}",
        )
        rows = self.rows("r1")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["outcome"], "accepted")
        self.assertEqual(row["rounds"], 1)
        self.assertEqual(row["accepted_round"], 1)
        self.assertTrue(row["green"])
        self.assertEqual(row["hidden"], "pass")
        self.assertEqual(row["tokens"], {"input": 1100, "cached": 400, "output": 550})
        self.assertEqual(row["cache_tokens"], {"read": 400, "write": 0})
        self.assertEqual(row["cost_usd"], 0.0049)
        self.assertEqual(row["settings"]["price_input_per_mtok"], 2.0)
        self.assertEqual(row["settings"]["price_output_per_mtok"], 6.0)
        self.assertEqual(row["model"], "fake-model")
        self.assertEqual(row["billing"], "api")
        self.assertTrue(row["cost_known"])
        self.assertTrue(row["fingerprint"].startswith("sha256:"))
        self.assertEqual(row["harness_version"], 1)
        self.assertEqual(row["n"], 1)
        self.assertEqual(row["case"], "c1")
        self.assertEqual(row["profile"], "fake")
        self.assertEqual(row["repeat"], 1)
        task = self.out / "r1" / "runs" / "1" / ".work" / "c1"
        for name in (
            "checks-0.md",
            "checks-1.md",
            "impl-1.json",
            "plan.md",
            "status.md",
        ):
            self.assertTrue((task / name).is_file(), name)
        self.assertFalse((self.out / "r1" / "runs" / "1" / "code").exists())
        self.assertTrue((self.out / "r1" / "run.json").is_file())
        self.assertIn("# Benchmark report: r1", self.report("r1"))

    def test_red_gates_feed_fix_list_next_round(self):
        server = self.server(
            [
                reply(
                    tool_call(
                        "write_file", {"path": "calc.py", "content": CALC_WRONG2}
                    ),
                    usage=U1,
                ),
                reply(content="done", usage=U2),
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_OK}),
                    usage=U1,
                ),
                reply(content="done", usage=U2),
            ]
        )
        self.add_profile("fake", server)
        self.make_case()
        done = self.run_bench("r1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows("r1")[0]
        self.assertEqual(row["outcome"], "accepted")
        self.assertEqual(row["accepted_round"], 2)
        self.assertEqual(row["rounds"], 2)
        task = self.out / "r1" / "runs" / "1" / ".work" / "c1"
        review1 = (task / "review-1.md").read_text(encoding="utf-8")
        self.assertIn("## Verdict: CHANGES_REQUESTED", review1)
        self.assertIn("## Fix list", review1)
        self.assertIn("tools/check.py", review1)
        self.assertIn("FAIL: calc.add(2, 3) returned 10, expected 5", review1)
        self.assertEqual(len(server.requests), 4)
        round2_body = json.dumps(server.requests[2]["body"])
        self.assertIn("checks-1.md #1", round2_body)
        self.assertIn("returned 10", round2_body)

    def test_no_progress_and_round_cap(self):
        # no progress: the same command fails (introduced) in rounds 1 and 2
        server = self.server(
            [
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_WRONG2})
                ),
                reply(content="done"),
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_WRONG3})
                ),
                reply(content="done"),
            ]
        )
        self.add_profile("fake", server)
        self.make_case()
        done = self.run_bench("np")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows("np")[0]
        self.assertEqual(row["outcome"], "no_progress")
        self.assertEqual(row["rounds"], 2)
        self.assertIsNone(row["accepted_round"])
        self.assertEqual(len(server.requests), 4)  # no round 3 request

        # round cap: alternating failing commands over 3 rounds
        server2 = self.server(
            [
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_WRONG2})
                ),
                reply(content="done"),
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_OK}),
                    tool_call(
                        "write_file", {"path": "mul.py", "content": MUL_BAD}, "c2"
                    ),
                ),
                reply(content="done"),
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_WRONG3})
                ),
                reply(content="done"),
            ]
        )
        self.add_profile("fake2", server2)
        self.make_case(
            "c2",
            commands=[
                command(sys.executable, "tools/check.py"),
                command(sys.executable, "tools/check2.py"),
            ],
        )
        done = self.run_bench("rc", profiles="fake2", extra=["--cases", "c2"])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows("rc")[0]
        self.assertEqual(row["outcome"], "round_cap")
        self.assertEqual(row["rounds"], 3)
        self.assertEqual(row["hidden"], "not_run")
        self.assertEqual(len(server2.requests), 6)

    def test_failed_round_counts_tokens_and_cost(self):
        # round 1 writes a wrong calc.py (gates red, tokens spent), round 2
        # spends more tokens and then dies on a non-retryable 400: implement.py
        # exits 1 but still writes impl-2.json, and the row must count it.
        server = self.server(
            [
                reply(
                    tool_call(
                        "write_file", {"path": "calc.py", "content": CALC_WRONG2}
                    ),
                    usage=U1,
                ),
                reply(content="done", usage=U2),
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_OK}),
                    usage=U1,
                ),
                400,
            ]
        )
        self.add_profile("fake", server)
        self.make_case()
        done = self.run_bench("fc")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows("fc")[0]
        self.assertEqual(row["outcome"], "failed")
        self.assertEqual(row["rounds"], 2)
        self.assertIsNone(row["accepted_round"])
        self.assertFalse(row["green"])
        self.assertEqual(row["hidden"], "not_run")
        self.assertIn("HTTP 400", row["reason"])
        task = self.out / "fc" / "runs" / "1" / ".work" / "c1"
        impl2 = json.loads((task / "impl-2.json").read_text(encoding="utf-8"))
        self.assertEqual(impl2["status"], "failed")
        # round 1 (U1+U2) plus the failed round 2 (U1): the failed round's
        # tokens and cost are included in the row.
        self.assertEqual(
            row["tokens"],
            {"input": 1000 + 100 + 1000, "cached": 800, "output": 550 + 500},
        )
        self.assertEqual(row["cache_tokens"], {"read": 800, "write": 0})
        self.assertEqual(row["cost_usd"], round(0.0049 + 0.0044, 6))
        self.assertGreater(row["cost_usd"], 0.0049)
        self.assertGreater(row["implement_seconds"], 0.0)
        failures = self.report("fc").split("## Failures", 1)[1]
        self.assertIn("n=1", failures)
        self.assertIn("failed", failures)

    def test_hidden_tests_never_visible(self):
        server = self.server(
            [
                reply(tool_call("read_file", {"path": "hidden_check.py"})),
                reply(content="done"),
            ]
        )
        self.add_profile("fake", server)
        self.make_case()
        done = self.run_bench("r1", extra=["--keep-code"])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        results = server.tool_results(1)
        self.assertTrue(any("does not exist" in r for r in results), results)
        code = self.out / "r1" / "runs" / "1" / "code"
        self.assertTrue(code.is_dir())
        check = subprocess.run(
            ["git", "-C", str(code), "cat-file", "-e", self.result_sha],
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(check.returncode, 0)
        all_requests = json.dumps([r["body"] for r in server.requests])
        self.assertNotIn(HIDDEN_MARKER, all_requests)
        work = self.out / "r1" / "runs" / "1" / ".work"
        for path in work.rglob("*"):
            if path.is_file():
                self.assertNotIn(
                    HIDDEN_MARKER, path.read_bytes().decode("utf-8", "replace")
                )

    def test_hidden_fail_recorded(self):
        server = self.fix_calc_server()
        self.add_profile("fake", server)
        self.make_case(hidden_body=HIDDEN_FAIL_PY)
        done = self.run_bench("r1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows("r1")[0]
        self.assertEqual(row["outcome"], "accepted")
        self.assertEqual(row["hidden"], "fail")
        report = self.report("r1")
        failures = report.split("## Failures", 1)[1]
        self.assertIn("n=1", failures)
        self.assertIn("hidden=fail", failures)

    def test_out_of_scope_counted(self):
        server = self.server(
            [
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_OK}),
                    tool_call(
                        "write_file", {"path": "notes.md", "content": "hi\n"}, "c2"
                    ),
                ),
                reply(content="done"),
            ]
        )
        self.add_profile("fake", server)
        self.make_case()
        done = self.run_bench("r1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows("r1")[0]
        self.assertEqual(row["outcome"], "accepted")
        self.assertEqual(row["out_of_scope"], ["notes.md"])

    def test_source_repo_untouched(self):
        server = self.fix_calc_server()
        self.add_profile("fake", server)
        self.make_case()

        def snapshot():
            return {
                "status": self.git(self.src, "status", "--porcelain").stdout,
                "worktrees": self.git(self.src, "worktree", "list").stdout,
                "refs": self.git(self.src, "for-each-ref").stdout,
                "head": self.git(self.src, "rev-parse", "HEAD").stdout,
            }

        before = snapshot()
        done = self.run_bench("r1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(snapshot(), before)
        self.assertFalse((self.src / ".work").exists())

    def test_outcomes_blocked_failed_unavailable(self):
        blocked_server = self.server(
            [reply(tool_call("blocked", {"reason": "plan is wrong"}))]
        )
        self.add_profile("pblocked", blocked_server)
        self.make_case()
        done = self.run_bench("b", profiles="pblocked")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows("b")[0]
        self.assertEqual(row["outcome"], "blocked")
        self.assertIn("plan is wrong", row["reason"])

        bad_server = self.server([400])
        self.add_profile("pbad", bad_server)
        done = self.run_bench("f", profiles="pbad")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows("f")[0]
        self.assertEqual(row["outcome"], "failed")
        self.assertIn("HTTP 400", row["reason"])

        down = self.server([503])
        up = self.server(
            [
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_OK}),
                    usage=U1,
                ),
                reply(content="done", usage=U2),
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_OK}),
                    usage=U1,
                ),
                reply(content="done", usage=U2),
            ]
        )
        self.add_profile("pdown", down)
        self.add_profile("pup", up)
        done = self.run_bench("u", profiles="pdown,pup", extra=["--repeat", "2"])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.rows("u")
        self.assertEqual(len(rows), 4)
        by_n = {r["n"]: r for r in rows}
        self.assertEqual(by_n[1]["outcome"], "unavailable")
        self.assertEqual(by_n[2]["outcome"], "accepted")
        self.assertEqual(by_n[3]["outcome"], "skipped")
        self.assertIn("profile unavailable in run 1", by_n[3]["reason"])
        self.assertEqual(by_n[4]["outcome"], "accepted")
        report = self.report("u")
        self.assertIn("pdown (partial)", report)
        self.assertIn("pup", report)
        self.assertNotIn("pup (partial)", report)

    def test_cost_cap_skips_remaining_runs(self):
        server = self.server(
            [
                reply(
                    content="done", usage={"prompt_tokens": 10, "completion_tokens": 0}
                ),
                reply(
                    content="done", usage={"prompt_tokens": 10, "completion_tokens": 0}
                ),
                reply(
                    content="done", usage={"prompt_tokens": 10, "completion_tokens": 0}
                ),
            ]
        )
        self.add_profile(
            "fake", server, price_input_per_mtok=1000000, price_output_per_mtok=0
        )
        self.make_case()
        done = self.run_bench("r1", extra=["--repeat", "3", "--max-cost-usd", "15"])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.rows("r1")
        self.assertEqual(
            [r["outcome"] for r in rows], ["accepted", "cost_cap", "skipped"]
        )
        self.assertEqual(rows[2]["reason"], "cost cap")
        self.assertEqual(rows[1]["rounds"], 1)
        models_text = (
            self.out / "r1" / "runs" / "2" / ".claude" / "task-flow.models.toml"
        ).read_text(encoding="utf-8")
        parsed = tomllib.loads(models_text)
        self.assertEqual(parsed["profiles"]["fake"]["fallback"], [])
        self.assertLessEqual(parsed["profiles"]["fake"]["max_cost_usd"], 15)
        self.assertEqual(parsed["profiles"]["fake"]["max_cost_usd"], 5)
        report = self.report("r1")
        self.assertIn("fake (partial)", report)
        self.assertIn("cost cap", report)

    def test_repeat_report_median_spread_deterministic(self):
        usages = [
            {"prompt_tokens": 1000, "completion_tokens": 100},
            {"prompt_tokens": 2000, "completion_tokens": 200},
            {"prompt_tokens": 3000, "completion_tokens": 300},
        ]
        responses = []
        for usage in usages:
            responses += [
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_OK}),
                    usage=usage,
                ),
                reply(content="done", usage=usage),
            ]
        server = self.server(responses)
        self.add_profile(
            "fake",
            server,
            price_input_per_mtok=1.0,
            price_output_per_mtok=2.0,
            price_cached_input_per_mtok=0,
        )
        self.make_case()
        done = self.run_bench("r1", extra=["--repeat", "3"])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.rows("r1")
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["outcome"] for r in rows], ["accepted"] * 3)
        costs = sorted(r["cost_usd"] for r in rows)
        self.assertEqual(costs, [0.0024, 0.0048, 0.0072])
        report = self.report("r1")
        self.assertIn("## S", report)
        self.assertIn("## All", report)
        self.assertIn(
            "$0.0048 ($0.0024–$0.0072)", report
        )  # cost per run median (min–max)
        self.assertIn("$0.0048", report)  # cost per accepted = total / 3
        self.assertIn("4000 / 0 / 400", report)  # tokens median of the middle run
        self.assertRegex(
            report, r"\d+\.\ds \(\d+\.\ds–\d+\.\ds\) \|"
        )  # seconds median (min–max)
        # report <run-id> regenerates report.md byte-identically
        before = (self.out / "r1" / "report.md").read_bytes()
        done = self.bench("report", "r1", "--out", str(self.out))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.out / "r1" / "report.md").read_bytes(), before)

    def test_subscription_profile_cost_not_compared(self):
        server = self.fix_calc_server()
        self.add_profile("sub", server, billing="subscription", price_input_per_mtok=0)
        self.make_case()
        done = self.run_bench("r1", profiles="sub")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows("r1")[0]
        self.assertEqual(row["billing"], "subscription")
        self.assertFalse(row["cost_known"])
        report = self.report("r1")
        self.assertIn("| sub | subscription | 1 | 1 | 1 | 1 |", report)
        self.assertEqual(report.count("| subscription |"), 2)  # both cost columns
        self.assertIn("cost per token is unknown", report)
        self.assertIn("tokens, rounds, time and acceptance", report)

    def test_extend_adds_profile_and_refuses_mismatch(self):
        server1 = self.fix_calc_server()
        self.add_profile("p1", server1)
        case_dir = self.make_case()
        done = self.run_bench("r1", profiles="p1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(self.rows("r1")), 1)

        server2 = self.fix_calc_server()
        self.add_profile("p2", server2)
        done = self.run_bench("r1", profiles="p2", extra=["--extend", "r1"])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.rows("r1")
        self.assertEqual([r["n"] for r in rows], [1, 2])
        self.assertEqual([r["profile"] for r in rows], ["p1", "p2"])
        meta = json.loads((self.out / "r1" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["profiles"], ["p1", "p2"])
        self.assertIn("p2", self.report("r1"))

        # an existing profile is refused
        before = (self.out / "r1" / "results.jsonl").read_bytes()
        done = self.run_bench("r1", profiles="p1", extra=["--extend", "r1"])
        self.assertEqual(done.returncode, 2)
        self.assertIn("already in run", done.stderr)
        self.assertEqual((self.out / "r1" / "results.jsonl").read_bytes(), before)

        # a different --repeat is refused
        done = self.run_bench(
            "r1", profiles="p2", extra=["--extend", "r1", "--repeat", "2"]
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn("--repeat", done.stderr)

        # a changed case plan is refused, naming the case and plan
        plan = case_dir / "plan.md"
        original = plan.read_text(encoding="utf-8")
        plan.write_text(original + "\nChanged.\n", encoding="utf-8", newline="\n")
        server3 = self.fix_calc_server()
        self.add_profile("p3", server3)
        done = self.run_bench("r1", profiles="p3", extra=["--extend", "r1"])
        self.assertEqual(done.returncode, 2)
        self.assertIn("c1", done.stderr)
        self.assertIn("plan", done.stderr)
        self.assertEqual((self.out / "r1" / "results.jsonl").read_bytes(), before)
        plan.write_text(original, encoding="utf-8", newline="\n")

    def test_review_flag_uses_claude_command(self):
        fake_claude = self.root / "fake_claude.py"
        write_file(
            fake_claude,
            r"""import json, re, sys
from pathlib import Path
argv = sys.argv[1:]
task = Path(argv[argv.index('--add-dir') + 1])
prompt = argv[argv.index('-p') + 1]
round_n = re.search(r'review-(\d+)\.md', prompt).group(1)
here = Path(__file__).resolve().parent
verdicts = (here / 'verdicts.txt').read_text().splitlines()
count_file = here / 'calls.txt'
count = int(count_file.read_text()) if count_file.exists() else 0
count_file.write_text(str(count + 1))
verdict = verdicts[min(count, len(verdicts) - 1)]
(task / f'review-{round_n}.md').write_text(f'# Review: round {round_n}\n## Verdict: {verdict}\n')
print(json.dumps({'total_cost_usd': 0.5, 'usage': {
    'input_tokens': 100, 'cache_read_input_tokens': 10, 'output_tokens': 20}}))
""",
        )
        write_file(self.root / "verdicts.txt", "APPROVED\n")
        server = self.fix_calc_server()
        self.add_profile("fake", server)
        self.make_case()
        claude_command = json.dumps([sys.executable, str(fake_claude)])
        done = self.run_bench(
            "r1", extra=["--review", "--claude-command", claude_command]
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows("r1")[0]
        self.assertEqual(row["outcome"], "accepted")
        self.assertEqual(row["accepted_round"], 1)
        self.assertEqual(row["review_cost_usd"], 0.5)
        self.assertEqual(
            row["review_tokens"], {"input": 100, "cached": 10, "output": 20}
        )
        task = self.out / "r1" / "runs" / "1" / ".work" / "c1"
        self.assertIn(
            "## Verdict: APPROVED", (task / "review-1.md").read_text(encoding="utf-8")
        )
        report = self.report("r1")
        # cost per accepted includes the review cost: (0.0049 + 0.5) / 1
        self.assertIn("$0.5049", report)

        # a CHANGES_REQUESTED verdict feeds round 2
        write_file(self.root / "verdicts.txt", "CHANGES_REQUESTED\nAPPROVED\n")
        for path in (self.root / "calls.txt",):
            if path.exists():
                path.unlink()
        server2 = self.server(
            [
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_OK}),
                    usage=U1,
                ),
                reply(content="done", usage=U2),
                reply(
                    tool_call("write_file", {"path": "calc.py", "content": CALC_OK}),
                    usage=U1,
                ),
                reply(content="done", usage=U2),
            ]
        )
        self.add_profile("fake2", server2)
        done = self.run_bench(
            "r2",
            profiles="fake2",
            extra=["--review", "--claude-command", claude_command],
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows("r2")[0]
        self.assertEqual(row["outcome"], "accepted")
        self.assertEqual(row["accepted_round"], 2)
        task2 = self.out / "r2" / "runs" / "1" / ".work" / "c1"
        self.assertIn(
            "## Verdict: CHANGES_REQUESTED",
            (task2 / "review-1.md").read_text(encoding="utf-8"),
        )

        # without --review the fake is never started
        for path in (self.root / "calls.txt",):
            if path.exists():
                path.unlink()
        server3 = self.fix_calc_server()
        self.add_profile("fake3", server3)
        done = self.run_bench("r3", profiles="fake3")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse((self.root / "calls.txt").exists())
        self.assertEqual(self.rows("r3")[0]["review_cost_usd"], 0.0)

    def test_refusals_create_nothing(self):
        server = self.fix_calc_server()
        self.add_profile("fake", server)
        down = self.server([503])
        self.add_profile("off", down, enabled=False)
        self.add_profile("keyless", down, api_key_env="BENCH_UNSET_KEY")
        self.make_case()
        self.make_case("c2", base="nosuchrev")
        before = self.tree(self.out)

        def refusal(label, *args, **kwargs):
            extra = list(kwargs.pop("extra", []))
            if not any(a == "--cases" for a in extra):
                extra += ["--cases", "c1"]
            done = self.run_bench(*args, extra=extra, **kwargs)
            self.assertEqual(done.returncode, 2, f"{label}: {done.stdout}{done.stderr}")
            self.assertEqual(done.stdout, "", label)
            self.assertTrue(done.stderr.strip(), label)
            self.assertEqual(self.tree(self.out), before, label)
            return done

        refusal("unknown profile", "x1", profiles="nope")
        refusal("disabled profile", "x2", profiles="off")
        refusal("keyless profile", "x3", profiles="keyless")
        refusal("unknown case", "x4", profiles="fake", extra=["--cases", "nope"])
        refusal("missing base commit", "x5", profiles="fake", extra=["--cases", "c2"])
        done = self.run_bench("r1", profiles="fake", extra=["--cases", "c1"])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        before = self.tree(self.out)
        refusal("existing run id", "r1", profiles="fake")

    def test_save_copies_run(self):
        server = self.fix_calc_server()
        self.add_profile("fake", server)
        self.make_case()
        done = self.run_bench("r1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        done = self.bench(
            "save", "r1", "--out", str(self.out), "--results-dir", str(self.results_dir)
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        target = self.results_dir / "r1"
        for name in ("results.jsonl", "run.json", "report.md"):
            self.assertEqual(
                (target / name).read_bytes(),
                (self.out / "r1" / name).read_bytes(),
                name,
            )
        done = self.bench(
            "save", "r1", "--out", str(self.out), "--results-dir", str(self.results_dir)
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn("exists", done.stderr)


if __name__ == "__main__":
    unittest.main()
