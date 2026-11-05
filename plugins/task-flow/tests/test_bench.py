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
import tomllib
import unittest
from pathlib import Path
from typing import ClassVar

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
        root = Path(tmp.name).resolve()  # expand Windows 8.3 short names
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
        # The fake model rewrites calc.py with a same-length body within the same second as
        # the gates imported it; a stale .pyc would then look valid and hide the change.
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
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


class SettingsForTest(BenchFixture):
    """settings_for: the generated per-run models.toml must validate again (D4)."""

    def write_and_load(self, name, settings):
        gen = self.root / "gen"
        (gen / ".claude").mkdir(parents=True, exist_ok=True)
        lines = [f"[profiles.{name}]"]
        lines += [f"{k} = {toml_value(v)}" for k, v in settings.items()]
        (gen / ".claude" / "task-flow.models.toml").write_text(
            "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
        )
        return bench.models.load(gen)[0][name]

    def test_fixed_experiment_replaces_user_routing(self) -> None:
        from unittest import mock

        server = self.server([])
        self.add_profile("fake", server)
        profiles, _ = bench.models.load(self.project)
        stub = type(
            "Stub",
            (),
            {
                "settings_for": lambda _self, _name, _remaining: (
                    bench.Runner.settings_for(
                        type("Profiles", (), {"profiles": profiles})(), "fake", None
                    )
                )
            },
        )()
        run = self.root / "fixed"
        bench.Runner.write_models(stub, run, "fake", None)
        user = self.root / "user.toml"
        # Invalid/unavailable routing is irrelevant once the experiment's explicit
        # scalar replaces it, and cannot change this fixed profile selection.
        user.write_text(
            '[roles.implementer]\ndefault = "missing"\n[roles.implementer.bands]\nS = "missing"\n',
            encoding="utf-8",
        )
        with mock.patch.object(bench.models, "user_file", return_value=user):
            self.assertEqual(bench.models.resolve_implementer(run)["value"], "fake")
            self.assertNotIn("routing", bench.models.resolve_implementer(run))

    def test_api_profile_drops_cli_only_keys(self):
        server = self.server([reply(content="done")])
        self.add_profile("fake", server)
        profiles, _roles = bench.models.load(self.project)
        settings = bench.Runner.settings_for(
            type("Stub", (), {"profiles": profiles})(), "fake", None
        )
        for key in bench.models.CLI_ONLY_KEYS:
            self.assertNotIn(key, settings, key)
        reloaded = self.write_and_load("fake", settings)
        self.assertEqual(reloaded["driver"], "openai-loop")
        self.assertEqual(reloaded["base_url"], server.url)

    def test_cli_profile_drops_api_loop_keys(self):
        with open(self.models_path, "a", encoding="utf-8", newline="") as handle:
            handle.write(
                "[profiles.codex]\n"
                'driver = "cli"\n'
                'model = "codex-model"\n'
                'billing = "subscription"\n'
                'command = ["codex", "exec", "-"]\n'
                'prompt_via = "stdin"\n'
                'usage_format = "codex-jsonl"\n'
                "timeout_seconds = 123\n"
            )
        profiles, _roles = bench.models.load(self.project)
        settings = bench.Runner.settings_for(
            type("Stub", (), {"profiles": profiles})(), "codex", None
        )
        for key in bench.models.CLI_INCOMPATIBLE_KEYS:
            self.assertNotIn(key, settings, key)
        self.assertEqual(settings["command"], ["codex", "exec", "-"])
        self.assertEqual(settings["usage_format"], "codex-jsonl")
        self.assertEqual(settings["timeout_seconds"], 123)
        reloaded = self.write_and_load("codex", settings)
        self.assertEqual(reloaded["driver"], "cli")
        self.assertEqual(reloaded["command"], ["codex", "exec", "-"])


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
        all_section = report.split("## All", 1)[1].split("## Notes", 1)[0]
        table_lines = [ln for ln in all_section.splitlines() if ln.startswith("|")]
        self.assertGreater(len(table_lines), 2, "the All table has no profile rows")
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
        # both cost columns, in the S table and again in the All table
        self.assertEqual(report.count("| subscription |"), 4)
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


# ---------------------------------------------------------------------------- add-case (AC1-AC3)


class AddCaseTest(BenchFixture):
    def commit_result_with_test(self):
        """A result commit that fixes calc.py and adds tests/test_calc.py."""
        write_file(self.src / "calc.py", CALC_OK)
        write_file(
            self.src / "tests" / "test_calc.py",
            "import calc\nassert calc.add(2, 3) == 5\n",
        )
        self.git(self.src, "add", "-A")
        self.git(self.src, "commit", "-q", "-m", "result with test")
        return self.git(self.src, "rev-parse", "HEAD").stdout.strip()

    def plan_file(self, text=None):
        """A plan file with CRLF endings, to prove add-case normalizes to LF."""
        path = self.root / "plan-in.md"
        body = (
            text
            if text is not None
            else self.plan_text([command(sys.executable, "tools/check.py")])
        )
        path.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))
        return path

    def add_case_args(self, result, plan_path, *extra):
        return [
            "add-case",
            "--repo",
            str(self.src),
            "--base",
            self.base_sha,
            "--result",
            result,
            "--plan",
            str(plan_path),
            "--size",
            "S",
            "--id",
            "newcase",
            "--command",
            command(sys.executable, "tests/test_calc.py"),
            "--cases-dir",
            str(self.cases),
            *extra,
        ]

    def test_add_case_writes_case_folder(self):
        result = self.commit_result_with_test()
        plan_path = self.plan_file()
        done = self.bench(*self.add_case_args(result, plan_path))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("candidate: tests/test_calc.py", done.stdout)
        self.assertIn("case newcase written ->", done.stdout)
        self.assertIn("check-case newcase", done.stdout)
        case_dir = self.cases / "newcase"
        toml = tomllib.loads((case_dir / "case.toml").read_text(encoding="utf-8"))
        self.assertEqual(toml["id"], "newcase")
        self.assertEqual(toml["size"], "S")
        self.assertEqual(toml["plan"], "plan.md")
        self.assertEqual(toml["base"], self.base_sha)
        self.assertEqual(toml["result"], result)
        self.assertRegex(toml["base"], r"^[0-9a-f]{40}$")
        self.assertRegex(toml["result"], r"^[0-9a-f]{40}$")
        self.assertEqual(
            toml["commands"], [command(sys.executable, "tests/test_calc.py")]
        )
        self.assertEqual(toml["hidden_tests"], ["tests/test_calc.py"])
        # plan.md is byte-equal to the input with LF endings
        self.assertEqual(
            (case_dir / "plan.md").read_bytes(),
            plan_path.read_bytes().replace(b"\r\n", b"\n"),
        )
        self.assertTrue((case_dir / "NOTES.md").is_file())
        notes = (case_dir / "NOTES.md").read_text(encoding="utf-8")
        self.assertIn("adaptations: none", notes)
        case = bench.load_case(case_dir)
        self.assertEqual(case["repo"].resolve(), self.src.resolve())
        done = self.bench("list", "--cases-dir", str(self.cases))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("newcase  S", done.stdout)
        self.assertIn(self.base_sha, done.stdout)

    def test_add_case_refusals(self):
        result = self.commit_result_with_test()
        plan_path = self.plan_file()
        leaky_plan = self.plan_file(
            self.plan_text([command(sys.executable, "tools/check.py")])
            + '\n    print("FAIL: hidden check failed: calc.add(2, 3) returned %d" % got)\n'
        )
        self.make_case("c1")
        before = self.tree(self.cases)

        def refusal(label, *args):
            done = self.bench(*args)
            self.assertEqual(done.returncode, 2, f"{label}: {done.stdout}{done.stderr}")
            self.assertTrue(done.stderr.strip(), label)
            self.assertEqual(self.tree(self.cases), before, label)
            return done

        done = refusal("unknown commit", *self.add_case_args("nosuchrev", plan_path))
        self.assertIn("cannot resolve", done.stderr)
        # base = result commit, result = base commit: not a descendant
        args = self.add_case_args(result, plan_path)
        base_i = args.index(self.base_sha)
        result_i = args.index(result)
        args[base_i] = result
        args[result_i] = self.base_sha
        done = refusal("not a descendant", *args)
        self.assertIn("not a descendant", done.stderr)
        done = refusal(
            "existing id",
            *[
                a if a != "newcase" else "c1"
                for a in self.add_case_args(result, plan_path)
            ],
        )
        self.assertIn("already exists", done.stderr)
        done = refusal(
            "invalid id",
            *[
                a if a != "newcase" else "Bad_ID"
                for a in self.add_case_args(result, plan_path)
            ],
        )
        self.assertIn("invalid id", done.stderr)
        done = refusal(
            "bad size",
            *[a if a != "S" else "XL" for a in self.add_case_args(result, plan_path)],
        )
        self.assertIn("invalid size", done.stderr)
        args = self.add_case_args(result, plan_path)
        idx = args.index("--command")
        no_command = args[:idx] + args[idx + 2 :]
        done = refusal("no --command", *no_command)
        self.assertIn("--command", done.stderr)
        done = refusal(
            "no candidates",
            *self.add_case_args(self.result_sha, plan_path),
        )
        self.assertIn("no hidden test candidate", done.stderr)
        done = refusal(
            "hidden test absent at result",
            *self.add_case_args(result, plan_path, "--hidden-test", "nope.py"),
        )
        self.assertIn("not present at result", done.stderr)
        done = refusal(
            "plan leaks hidden code",
            *self.add_case_args(result, leaky_plan, "--hidden-test", "hidden_check.py"),
        )
        self.assertIn("leaks hidden test code", done.stderr)

    def test_add_case_repo_path(self):
        result = self.commit_result_with_test()
        plan_path = self.plan_file()
        # the cases dir inside the source repo: repo is relative to the case folder
        inside = self.src / "cases"
        args = self.add_case_args(result, plan_path)
        args[args.index(str(self.cases))] = str(inside)
        done = self.bench(*args)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        case_dir = inside / "newcase"
        toml = tomllib.loads((case_dir / "case.toml").read_text(encoding="utf-8"))
        self.assertEqual(toml["repo"], "../..")
        case = bench.load_case(case_dir)
        self.assertEqual(case["repo"].resolve(), self.src.resolve())
        # the cases dir outside the source repo: repo is an absolute POSIX path
        done = self.bench(*self.add_case_args(result, plan_path))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        toml = tomllib.loads(
            (self.cases / "newcase" / "case.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(toml["repo"], self.src.as_posix())
        case = bench.load_case(self.cases / "newcase")
        self.assertEqual(case["repo"].resolve(), self.src.resolve())


# ---------------------------------------------------------------------------- check-case (AC4-AC6)


class CheckCaseTest(BenchFixture):
    def commit_fixed_result(self):
        """A result commit that fixes calc.py and keeps hidden_check.py (idempotent)."""
        write_file(self.src / "calc.py", CALC_OK)
        self.git(self.src, "add", "-A")
        self.git(self.src, "commit", "-q", "-m", "fix calc", check=False)
        return self.git(self.src, "rev-parse", "HEAD").stdout.strip()

    def good_case(self, cid="c1", **kwargs):
        result = kwargs.pop("result", None) or self.commit_fixed_result()
        visible = kwargs.pop("visible", [command(sys.executable, "tools/check.py")])
        hidden = kwargs.pop(
            "hidden_commands", [command(sys.executable, "hidden_check.py")]
        )
        plan = kwargs.pop("plan", None) or self.plan_text(visible)
        return self.make_case(
            cid,
            commands=visible,
            hidden_commands=hidden,
            plan=plan,
            result=result,
            **kwargs,
        )

    def test_check_case_ok_and_failures(self):
        self.good_case("good")
        done = self.bench("check-case", "good", "--cases-dir", str(self.cases))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(
            len([l for l in lines if l.startswith("PASS ")]), 7, done.stdout
        )
        for name in (
            "load",
            "commits",
            "hidden-files",
            "no-leak",
            "visible-on-result",
            "hidden-on-result",
            "hidden-on-base",
        ):
            self.assertIn(f"PASS {name}:", done.stdout)
        self.assertEqual(lines[-1], "check-case good: ok")

        # a hidden command that also passes on the base -> hidden-on-base FAIL
        self.good_case(
            "passbase",
            hidden_commands=[
                command(sys.executable, "hidden_check.py"),
                command(sys.executable, "-c", "pass"),
            ],
        )
        done = self.bench("check-case", "passbase", "--cases-dir", str(self.cases))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("FAIL hidden-on-base:", done.stdout)
        self.assertIn("command 2", done.stdout)
        self.assertIn("failed (1 checks)", done.stdout)

        # a visible command failing on the result -> visible-on-result FAIL, number named
        result = self.commit_fixed_result()
        visible = [
            command(sys.executable, "tools/check.py"),
            command(sys.executable, "tools/check2.py"),
        ]
        self.make_case(
            "visfail",
            commands=visible,
            hidden_commands=[command(sys.executable, "hidden_check.py")],
            plan=self.plan_text(visible),
            result=result,
        )
        done = self.bench("check-case", "visfail", "--cases-dir", str(self.cases))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("FAIL visible-on-result:", done.stdout)
        self.assertIn("command 2", done.stdout)

        # a plan line equal to an added hidden line -> no-leak FAIL
        result = self.commit_fixed_result()
        leaky = self.plan_text([command(sys.executable, "tools/check.py")]) + (
            '\n    print("FAIL: hidden check failed: calc.add(2, 3) returned %d" % got)\n'
        )
        self.make_case(
            "leaky",
            commands=[command(sys.executable, "tools/check.py")],
            hidden_commands=[command(sys.executable, "hidden_check.py")],
            plan=leaky,
            result=result,
        )
        done = self.bench("check-case", "leaky", "--cases-dir", str(self.cases))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("FAIL no-leak:", done.stdout)

        # a missing hidden file -> hidden-files FAIL, later checks SKIP
        self.good_case("nofile", hidden_tests=["missing.py"])
        done = self.bench("check-case", "nofile", "--cases-dir", str(self.cases))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("FAIL hidden-files:", done.stdout)
        for name in ("visible-on-result", "hidden-on-result", "hidden-on-base"):
            self.assertIn(f"FAIL {name}: SKIP", done.stdout)

        # an unknown case -> exit 2
        done = self.bench("check-case", "nope", "--cases-dir", str(self.cases))
        self.assertEqual(done.returncode, 2)
        self.assertIn("unknown case", done.stderr)

    def test_check_case_leaves_no_trace(self):
        self.good_case("c1")

        def snapshot():
            return (
                self.git(self.src, "status", "--porcelain").stdout,
                self.git(self.src, "worktree", "list").stdout,
                self.git(self.src, "for-each-ref").stdout,
                self.git(self.src, "rev-parse", "HEAD").stdout,
            )

        before = snapshot()
        cases_before = self.tree(self.cases)
        done = self.bench("check-case", "c1", "--cases-dir", str(self.cases))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.tree(self.cases), cases_before)
        self.assertFalse(self.out.exists())


class NoLeakTest(BenchFixture):
    def test_no_leak_rule(self):
        shared = 'SHARED_LINE = "present at the base as well, long enough"'
        spaced = 'SPACED = "same modulo surrounding whitespace, long"'
        hidden = 'NEW_HIDDEN_LINE = "leaks into the plan, long enough"'
        write_file(self.src / "h.py", f"{shared}\n{spaced}\nx = 1\n")
        self.git(self.src, "add", "-A")
        self.git(self.src, "commit", "-q", "-m", "base h")
        base = self.git(self.src, "rev-parse", "HEAD").stdout.strip()
        write_file(
            self.src / "h.py",
            f"{shared}\n    {spaced}   \nSHORT_NEW = 1\n{hidden}\n",
        )
        self.git(self.src, "add", "-A")
        self.git(self.src, "commit", "-q", "-m", "result h")
        result = self.git(self.src, "rev-parse", "HEAD").stdout.strip()

        leaking_plan = (
            f"# Plan\n## Verification commands\n```\ntrue\n```\n\n"
            f"{shared}\nSHORT_NEW = 1\n   {hidden}   \n"
        )
        leaks = bench.no_leak(self.src, base, result, ["h.py"], leaking_plan)
        # only the new, long line leaks; the shared and the whitespace variants do not
        self.assertEqual(leaks, [hidden])
        clean_plan = f"# Plan\n{shared}\nSHORT_NEW = 1\n"
        self.assertEqual(
            bench.no_leak(self.src, base, result, ["h.py"], clean_plan), []
        )
        # a file that is new at the result: every long line is hidden
        write_file(self.src / "new.py", f"{hidden}\n")
        self.git(self.src, "add", "-A")
        self.git(self.src, "commit", "-q", "-m", "new file")
        result2 = self.git(self.src, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(
            bench.no_leak(self.src, base, result2, ["new.py"], hidden), [hidden]
        )


# ---------------------------------------------------------------------------- results log (AC7, AC8)


def canned_row(
    run_id,
    n,
    profile,
    outcome="accepted",
    billing="api",
    cost_known=True,
    hidden="pass",
    cost=0.004,
    tokens=(1000, 400, 500),
):
    return {
        "run_id": run_id,
        "n": n,
        "case": "c1",
        "size": "S",
        "profile": profile,
        "repeat": 1,
        "outcome": outcome,
        "reason": "",
        "rounds": 1,
        "accepted_round": 1 if outcome == "accepted" else None,
        "green": outcome == "accepted",
        "hidden": hidden,
        "out_of_scope": [],
        "tokens": {"input": tokens[0], "cached": tokens[1], "output": tokens[2]},
        "cache_tokens": {"read": 0, "write": 0},
        "cost_usd": cost,
        "review_cost_usd": 0.0,
        "review_tokens": {"input": 0, "cached": 0, "output": 0},
        "cost_known": cost_known,
        "billing": billing,
        "model": f"{profile}-model",
        "settings": {},
        "seconds": 1.0,
        "implement_seconds": 1.0,
        "started_at": "2026-01-01T00:00:00Z",
        "fingerprint": "sha256:" + "ab" * 32,
        "harness_version": 1,
    }


def canned_run(results_dir, run_id, created, profiles, rows):
    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True)
    meta = {
        "run_id": run_id,
        "created": created,
        "harness_version": 1,
        "repeat": 1,
        "review": False,
        "cases": ["c1"],
        "case_entries": {},
        "profiles": profiles,
        "fingerprint": "sha256:" + "ab" * 32,
    }
    write_file(run_dir / "run.json", json.dumps(meta, indent=2, sort_keys=True) + "\n")
    write_file(
        run_dir / "results.jsonl",
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
    )
    return run_dir


class MergeTest(BenchFixture):
    ENTRY: ClassVar[dict[str, str]] = {
        "case_toml_sha256": "a",
        "plan_sha256": "b",
        "base": "c",
        "result": "d",
    }

    def canned(self, run_id, profiles, rows, entry=None):
        run_dir = canned_run(self.out, run_id, "2026-01-01T00:00:00Z", profiles, rows)
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        meta["case_entries"] = {"c1": entry or self.ENTRY}
        write_file(
            run_dir / "run.json", json.dumps(meta, indent=2, sort_keys=True) + "\n"
        )

    def merge(self, *sources, into="m1"):
        return self.bench(
            "merge",
            *sources,
            "--into",
            into,
            "--out",
            str(self.out),
            "--project",
            str(self.project),
        )

    def test_later_run_replaces_its_cell_and_keeps_the_rest(self):
        self.canned(
            "a1",
            ["p1", "p2"],
            [
                canned_row("a1", 1, "p1", outcome="failed", hidden="not_run"),
                canned_row("a1", 2, "p2", cost=0.01),
            ],
        )
        self.canned("a2", ["p1"], [canned_row("a2", 1, "p1", cost=0.02)])
        done = self.merge("a1", "a2")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.rows("m1")
        self.assertEqual(
            [(r["n"], r["profile"], r["outcome"]) for r in rows],
            [(1, "p1", "accepted"), (2, "p2", "accepted")],
        )
        self.assertEqual([r["source_run"] for r in rows], ["a2", "a1"])
        self.assertEqual({r["run_id"] for r in rows}, {"m1"})
        meta = json.loads((self.out / "m1" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["merged_from"], ["a1", "a2"])
        self.assertEqual(meta["profiles"], ["p1", "p2"])
        self.assertEqual({r["fingerprint"] for r in rows}, {meta["fingerprint"]})
        report = self.report("m1")
        all_section = report.split("## All", 1)[1].split("## Notes", 1)[0]
        self.assertIn("| p1 |", all_section)

    def test_refuses_sources_whose_case_changed(self):
        self.canned("a1", ["p1"], [canned_row("a1", 1, "p1")])
        self.canned(
            "a2",
            ["p1"],
            [canned_row("a2", 1, "p1")],
            entry={**self.ENTRY, "plan_sha256": "x"},
        )
        done = self.merge("a1", "a2")
        self.assertEqual(done.returncode, 2)
        self.assertIn("case c1 differs", done.stderr)
        self.assertFalse((self.out / "m1").exists())

    def test_refuses_existing_target_and_single_source(self):
        self.canned("a1", ["p1"], [canned_row("a1", 1, "p1")])
        self.canned("a2", ["p1"], [canned_row("a2", 1, "p1")])
        self.assertEqual(self.merge("a1").returncode, 2)
        self.assertEqual(self.merge("a1", "a2").returncode, 0)
        again = self.merge("a1", "a2")
        self.assertEqual(again.returncode, 2)
        self.assertIn("already exists", again.stderr)


class LogTest(BenchFixture):
    def test_log_deterministic(self):
        self.results_dir.mkdir()
        rows1 = [
            canned_row("20260101T000000Z", 1, "p1", cost=0.004),
            canned_row(
                "20260101T000000Z", 2, "p1", cost=0.006, tokens=(2000, 800, 900)
            ),
        ]
        canned_run(
            self.results_dir, "20260101T000000Z", "2026-01-01T00:00:00Z", ["p1"], rows1
        )
        rows2 = [
            canned_row(
                "20260202T000000Z",
                1,
                "sub",
                outcome="skipped",
                billing="subscription",
                cost_known=False,
                hidden="not_run",
                cost=0.0,
            ),
            canned_row(
                "20260202T000000Z", 2, "sub", billing="subscription", cost_known=False
            ),
        ]
        canned_run(
            self.results_dir, "20260202T000000Z", "2026-02-02T00:00:00Z", ["sub"], rows2
        )
        text = bench.render_log(self.results_dir)
        self.assertEqual(text, bench.render_log(self.results_dir))
        lines = text.splitlines()
        header = next(
            i
            for i, line in enumerate(lines)
            if line.startswith("| Run | Date | Harness |")
        )
        rows = [l for l in lines[header + 2 :] if l.startswith("| ")]
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].startswith("| 20260101T000000Z | 2026-01-01 |"))
        self.assertTrue(rows[1].startswith("| 20260202T000000Z | 2026-02-02 |"))
        self.assertIn("sha256:abababababab", rows[0])  # fingerprint shortened to 12 hex
        self.assertIn(
            "| p1 | p1-model | 2 | 2 | 2 | 2/2 | 1500 / 600 / 700 | $0.0050 |", rows[0]
        )
        self.assertIn("| sub (partial) | sub-model | 2 | 1 | 1 | 1/1 |", rows[1])
        self.assertIn("| subscription |", rows[1])  # cost per accepted, D10 rule
        # the same cost per accepted as report.md computes
        report = bench.render_report(
            rows1,
            json.loads(
                (self.results_dir / "20260101T000000Z" / "run.json").read_text(
                    encoding="utf-8"
                )
            ),
        )
        self.assertIn("$0.0050", report)
        self.assertIn("$0.0050", text)
        # the CLI rewrites the same bytes
        done = self.bench("log", "--results-dir", str(self.results_dir))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            (self.results_dir / "log.md").read_text(encoding="utf-8"), text
        )
        # an empty results dir
        empty = self.root / "empty-results"
        empty.mkdir()
        self.assertIn("No saved runs yet.", bench.render_log(empty))

    def test_save_refreshes_log_and_replace(self):
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
        log = (self.results_dir / "log.md").read_text(encoding="utf-8")
        self.assertIn("| r1 | ", log)
        self.assertIn("$0.0049", log)
        log_bytes = (self.results_dir / "log.md").read_bytes()
        target_bytes = (target / "results.jsonl").read_bytes()

        # a second save refuses and changes nothing
        done = self.bench(
            "save", "r1", "--out", str(self.out), "--results-dir", str(self.results_dir)
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn("exists", done.stderr)
        self.assertEqual((target / "results.jsonl").read_bytes(), target_bytes)
        self.assertEqual((self.results_dir / "log.md").read_bytes(), log_bytes)

        # after a changed results.jsonl (as run --extend leaves it), --replace re-saves
        rows = self.rows("r1")
        rows[0]["cost_usd"] = 0.02
        write_file(
            self.out / "r1" / "results.jsonl",
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
        )
        done = self.bench(
            "save",
            "r1",
            "--replace",
            "--out",
            str(self.out),
            "--results-dir",
            str(self.results_dir),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            (target / "results.jsonl").read_bytes(),
            (self.out / "r1" / "results.jsonl").read_bytes(),
        )
        log = (self.results_dir / "log.md").read_text(encoding="utf-8")
        self.assertIn("$0.0200", log)
        self.assertNotIn("$0.0049", log)


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


if __name__ == "__main__":
    unittest.main()
