# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/role_run.py and scripts/role_runtime.py: the external planner.

Run: uv run --script plugins/task-flow/tests/test_role_run.py

Every fixture is a temporary repository: a fake `.work/<id>/` task folder, a fake
`.claude/task-flow.md` gate, a models.toml with fake profiles, and either a fake HTTP
API (an injected urlopen) or a fake CLI (a small Python program emitting JSONL, whose
final message the host captures through -o {last_message_file}). No real task artifact,
this repository's git state, a real model, a real credential or a login is involved.
"""

import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import models
import role_run
import role_runtime

VALID_PLAN = """# Plan: Fake task
## Goal
Do the fake thing.
## Context
A temporary repository.
## Profiles
python: src/x.py
## Steps
1. Change src/x.py.
## Acceptance criteria
AC1: The suite passes. Verify: uv run --script plugins/task-flow/tests/test_x.py
## Verification commands
```
uv run --script plugins/task-flow/tests/test_x.py
uv run --script plugins/task-flow/tests/test_inherited.py
```
## Project rules
- No version bumps.
## Out of scope
Nothing else.
## Risks / open questions
None.
"""

BLOCKED_PLAN = VALID_PLAN.replace(
    "## Steps\n1. Change src/x.py.\n## Acceptance criteria\n"
    "AC1: The suite passes. Verify: uv run --script plugins/task-flow/tests/test_x.py\n",
    "## Steps\n## Acceptance criteria\n",
).replace(
    "## Risks / open questions\nNone.",
    "## Risks / open questions\nBLOCKED: yes\nWhich design?",
)

# A genuine BLOCKED: yes plan that also leaves the verification list empty: the
# inherited gate (test_inherited.py in the fixture's .claude/task-flow.md) is not
# enforced against an empty list, and the runner still publishes it with exit 3.
BLOCKED_EMPTY_PLAN = BLOCKED_PLAN.replace(
    "## Verification commands\n"
    "```\n"
    "uv run --script plugins/task-flow/tests/test_x.py\n"
    "uv run --script plugins/task-flow/tests/test_inherited.py\n"
    "```\n",
    "## Verification commands\n",
)

INVALID_PLAN = VALID_PLAN.replace(
    "AC1: The suite passes. Verify: uv run --script plugins/task-flow/tests/test_x.py",
    "- AC1: The suite passes. Verify: tests",
)

GATE_MD = """# task-flow configuration

## Verification commands

```
uv run --script plugins/task-flow/tests/test_inherited.py
```
"""

API_TOML = """
[profiles.fake]
driver = "openai-loop"
base_url = "https://api.fake.example/v1"
model = "fake-model"
api_key_env = "FAKE_API_KEY"
billing = "api"
price_input_per_mtok = 1.0
price_output_per_mtok = 2.0
max_turns = 10

[profiles.fallback]
driver = "openai-loop"
base_url = "https://api.fallback.example/v1"
model = "fallback-model"
api_key_env = "FALLBACK_API_KEY"
billing = "api"

[roles]
planner = "fake"
"""

# The fake CLI's argv: a "verified" program (the test patches the verified set to
# include python), exec, an explicit read-only sandbox, and a host-captured final
# message via -o {last_message_file}.
CLI_TOML = """
[profiles.fakecli]
driver = "cli"
model = "fake-cli-model"
billing = "subscription"
command = ["{python}", "-c", "print('implementer')"]
read_only_command = ["{python}", "{runner}", "exec", "--sandbox", "read-only", "-spec", "{spec}", "-o", "{last_message_file}", "-"]
prompt_via = "stdin"
usage_format = "codex-jsonl"
timeout_seconds = 30

[roles]
planner = "fakecli"
"""

FAKE_CLI_RUNNER = r"""
import json
import sys

argv = sys.argv[1:]
spec = json.load(open(argv[argv.index("-spec") + 1], encoding="utf-8"))
prompt = sys.stdin.read()
out = argv[argv.index("-o") + 1]
text = spec[1] if ("Fix exactly these problems" in prompt and len(spec) > 1) else spec[0]
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 50, "cached_input_tokens": 5, "output_tokens": 10}}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
open(out, "w", encoding="utf-8").write(text)
"""


def api_payload(text, with_tools=False, usage=None):
    """One openai-loop response payload; `text` is the assistant content."""
    message = {"role": "assistant", "content": text}
    if with_tools:
        message["tool_calls"] = [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": "README.md", "limit": 5}),
                },
            }
        ]
    return {
        "choices": [{"message": message}],
        "usage": usage
        or {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 10},
        },
    }


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_urlopen(script, calls):
    """An urlopen that plays back `script` (payloads or exceptions), recording calls."""

    def urlopen(request, timeout=None):
        calls.append(request)
        item = script.pop(0) if script else api_payload("done")
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    return urlopen


def patch_post(urlopen):
    """Patch role_runtime.post to use the fake urlopen and no real waiting."""
    original = role_runtime.post

    def patched(*args, **kwargs):
        kwargs["urlopen"] = urlopen
        kwargs["sleep"] = lambda seconds: None
        return original(*args, **kwargs)

    return mock.patch.object(role_runtime, "post", patched)


class Repo:
    """A temporary repository (root = repo, task folder = repo/.work/task-x)."""

    def __init__(self, tmp: Path, toml: str):
        self.root = tmp / "repo"
        self.task = self.root / ".work" / "task-x"
        (self.root / ".claude").mkdir(parents=True)
        (self.root / ".claude" / "task-flow.md").write_text(GATE_MD, encoding="utf-8")
        (self.root / ".claude" / "task-flow.models.toml").write_text(
            toml, encoding="utf-8"
        )
        self.task.mkdir(parents=True)
        (self.task / "task.md").write_text("# Task\n- goal: fake\n", encoding="utf-8")
        (self.task / "status.md").write_text(
            "# Status: task-x\n- phase: new\n- base: task-base\n- branch: task/x\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text("# readme\n", encoding="utf-8")
        self.home = tmp / "home"
        (self.home / ".claude" / "task-flow").mkdir(parents=True)

    def argv(self, *extra):
        return [
            "--role",
            "planner",
            "--task-dir",
            str(self.task),
            "--project",
            str(self.root),
            "--worktree",
            str(self.root),
            "--heartbeat",
            "0",
            *extra,
        ]

    def run_subprocess(self, env=None):
        full_env = dict(os.environ)
        full_env["HOME"] = str(self.home)
        full_env["USERPROFILE"] = str(self.home)
        full_env.pop("FAKE_API_KEY", None)
        full_env.pop("FALLBACK_API_KEY", None)
        if env:
            full_env.update(env)
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "role_run.py"), *self.argv()],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=full_env,
            cwd=self.root,
            timeout=120,
            check=False,
        )


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        patcher = mock.patch.object(
            models,
            "user_file",
            lambda: self.tmp / "home" / ".claude/task-flow/models.toml",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.count = 0

    def repo(self, toml=API_TOML):
        self.count += 1
        return Repo(self.tmp / f"r{self.count}", toml)


class PreflightTest(Base):
    def test_unsupported_role_refused(self):
        repo = self.repo()
        code = role_run.main(
            [
                "--role",
                "reviewer",
                "--task-dir",
                str(repo.task),
                "--project",
                str(repo.root),
                "--worktree",
                str(repo.root),
            ]
        )
        self.assertEqual(code, 2)

    def test_missing_task_dir_and_inputs_refused(self):
        repo = self.repo()
        code = role_run.main(
            [
                "--role",
                "planner",
                "--task-dir",
                str(self.tmp / "nope"),
                "--project",
                str(repo.root),
                "--worktree",
                str(repo.root),
            ]
        )
        self.assertEqual(code, 2)
        (repo.task / "task.md").unlink()
        self.assertEqual(role_run.main(repo.argv()), 2)

    def test_claude_planner_refused_nothing_to_run(self):
        repo = self.repo('[roles]\nplanner = "claude:opus"\n')
        self.assertEqual(role_run.main(repo.argv()), 2)

    def test_override_rules(self):
        repo = self.repo(API_TOML.replace('planner = "fake"', 'planner = "claude"'))
        self.assertEqual(role_run.main(repo.argv("--profile", "nope")), 2)
        self.assertEqual(role_run.main(repo.argv("--profile", "claude:opus")), 2)
        self.assertEqual(role_run.main(repo.argv("--profile", "script")), 2)
        disabled = API_TOML.replace(
            "max_turns = 10", "max_turns = 10\nenabled = false"
        ).replace('planner = "fake"', 'planner = "claude"')
        repo2 = self.repo(disabled)
        self.assertEqual(role_run.main(repo2.argv("--profile", "fake")), 2)
        # an enabled override with its key resolves (run fails later without a fake
        # urlopen, but not at preflight: the refusal is the missing key, not the role)
        with (
            mock.patch.dict(os.environ, {"FAKE_API_KEY": "sk-fake-123456"}),
            patch_post(make_urlopen([api_payload(VALID_PLAN)], [])),
        ):
            self.assertEqual(role_run.main(repo.argv("--profile", "fake")), 0)

    def test_missing_key_refused_subprocess(self):
        repo = self.repo()
        done = repo.run_subprocess()
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("FAKE_API_KEY", done.stderr)
        self.assertFalse((repo.task / "plan.md").exists())
        self.assertEqual(done.stdout, "", "stdout stays empty on a refusal")

    def test_cli_producer_requires_read_only_command(self):
        toml = CLI_TOML.replace(
            'read_only_command = ["{python}", "{runner}", "exec", "--sandbox", '
            '"read-only", "-spec", "{spec}", "-o", "{last_message_file}", "-"]\n',
            "",
        ).replace('"{python}"', json.dumps(sys.executable))
        repo = self.repo(toml)
        self.assertEqual(role_run.main(repo.argv()), 2)

    def test_stack_manifest_file(self):
        repo = self.repo()
        manifest = self.tmp / "stack.json"
        manifest.write_text(
            json.dumps({"satisfied": ["cli-implementer-driver"]}), encoding="utf-8"
        )
        with (
            mock.patch.dict(os.environ, {"FAKE_API_KEY": "sk-fake-123456"}),
            patch_post(make_urlopen([api_payload(VALID_PLAN)], [])),
        ):
            code = role_run.main(repo.argv("--stack-manifest", str(manifest)))
        self.assertEqual(code, 0)
        record = role_runtime.load_role_metrics(repo.task)[0]
        # the manifest reached the validator through the prompt, not the record
        self.assertEqual(record["result"], "ok")
        self.assertEqual(
            role_run.main(repo.argv("--stack-manifest", str(self.tmp / "none.json"))), 2
        )


class ApiRunTest(Base):
    """End to end through the configured role path with a fake API driver."""

    def run_with(self, repo, script, env=None):
        calls = []
        urlopen = make_urlopen(list(script), calls)
        with (
            mock.patch.dict(
                os.environ, {"FAKE_API_KEY": "sk-fake-123456", **(env or {})}
            ),
            patch_post(urlopen),
        ):
            code = role_run.main(repo.argv())
        return code, calls

    def test_valid_plan_published_end_to_end(self):
        repo = self.repo()
        code, calls = self.run_with(repo, [api_payload(VALID_PLAN)])
        self.assertEqual(code, 0, "exit 0 for a published valid artifact")
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            (repo.task / "plan.md").read_text(encoding="utf-8"), VALID_PLAN
        )
        records = role_runtime.load_role_metrics(repo.task)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["schema"], "role-metrics/1")
        self.assertEqual(record["role"], "planner")
        self.assertEqual(record["result"], "ok")
        self.assertEqual(record["tokens"], {"input": 100, "cached": 10, "output": 20})
        self.assertAlmostEqual(record["cost_usd"], (90 * 1.0 + 20 * 2.0) / 1e6)
        self.assertNotIn("sk-fake-123456", json.dumps(record))
        self.assertEqual(record["repair_attempts"], 0)
        self.assertFalse(list(repo.task.glob("impl-*.json")), "impl-*.json untouched")
        self.assertIn(
            "- phase: new", (repo.task / "status.md").read_text(encoding="utf-8")
        )

    def test_tools_run_and_blocked_plan_exits_3(self):
        repo = self.repo()
        script = [
            api_payload("let me look", with_tools=True),
            api_payload(BLOCKED_PLAN),
        ]
        code, calls = self.run_with(repo, script)
        self.assertEqual(code, 3, "a valid blocked artifact publishes and exits 3")
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            (repo.task / "plan.md").read_text(encoding="utf-8"), BLOCKED_PLAN
        )
        self.assertEqual(
            role_runtime.load_role_metrics(repo.task)[0]["result"], "blocked"
        )

    def test_blocked_plan_with_empty_verification_list_exits_3(self):
        # The repo fixture's gate is real (test_inherited.py), but a blocked plan
        # with no verification list is valid: exit 3 and the artifact publishes.
        repo = self.repo()
        code, _calls = self.run_with(repo, [api_payload(BLOCKED_EMPTY_PLAN)])
        self.assertEqual(code, 3, "a valid blocked artifact publishes and exits 3")
        self.assertEqual(
            (repo.task / "plan.md").read_text(encoding="utf-8"), BLOCKED_EMPTY_PLAN
        )
        self.assertEqual(
            role_runtime.load_role_metrics(repo.task)[0]["result"], "blocked"
        )

    def test_one_repair_attempt_then_fail_preserves_artifact(self):
        repo = self.repo()
        (repo.task / "plan.md").write_text("PRIOR BYTES\n", encoding="utf-8")
        script = [api_payload(INVALID_PLAN), api_payload(INVALID_PLAN)]
        code, calls = self.run_with(repo, script)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 2, "exactly one repair attempt")
        self.assertEqual(
            (repo.task / "plan.md").read_text(encoding="utf-8"),
            "PRIOR BYTES\n",
            "prior artifact bytes preserved on failure",
        )
        record = role_runtime.load_role_metrics(repo.task)[0]
        self.assertEqual(record["result"], "failed")
        self.assertEqual(record["repair_attempts"], 1, "the repair is counted once")

    def test_repair_then_valid_publishes(self):
        repo = self.repo()
        code, calls = self.run_with(
            repo, [api_payload(INVALID_PLAN), api_payload(VALID_PLAN)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            (repo.task / "plan.md").read_text(encoding="utf-8"), VALID_PLAN
        )

    def test_provider_unavailable_retries_bounded_then_fails(self):
        repo = self.repo()
        script = [urllib.error.URLError("boom")] * 4
        code, calls = self.run_with(repo, script)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 4, "retries stay bounded (default 3 + 1)")
        self.assertFalse((repo.task / "plan.md").exists())
        record = role_runtime.load_role_metrics(repo.task)[0]
        self.assertEqual(record["result"], "failed")
        self.assertEqual(record["profiles"][0]["outcome"], "unavailable")
        self.assertEqual(len(record["profiles"][0]["retries"]), 3)

    def test_fallback_profile_used_never_claude(self):
        toml = API_TOML.replace(
            "max_turns = 10\n", 'max_turns = 10\nfallback = ["fallback"]\n'
        )
        repo = self.repo(toml)
        script = [urllib.error.URLError("primary down")] * 4 + [api_payload(VALID_PLAN)]
        code, _calls = self.run_with(
            repo, script, env={"FALLBACK_API_KEY": "sk-fallback-9999"}
        )
        self.assertEqual(code, 0)
        record = role_runtime.load_role_metrics(repo.task)[0]
        self.assertEqual(
            [p["outcome"] for p in record["profiles"]], ["unavailable", "ok"]
        )
        self.assertEqual(record["selection"]["profile"], "fake")
        self.assertNotIn("claude", json.dumps(record["selection"]).lower())

    def test_http_400_is_fatal(self):
        import io

        repo = self.repo()
        body = io.BytesIO(json.dumps({"error": "bad"}).encode("utf-8"))

        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, body)

        with (
            mock.patch.dict(os.environ, {"FAKE_API_KEY": "sk-fake-123456"}),
            patch_post(urlopen),
        ):
            code = role_run.main(repo.argv())
        self.assertEqual(code, 1)
        record = role_runtime.load_role_metrics(repo.task)[0]
        self.assertIn("HTTP 400", record["profiles"][0]["reason"])
        self.assertFalse((repo.task / "plan.md").exists())

    def test_gate_omission_fails_validation(self):
        repo = self.repo()
        plan = VALID_PLAN.replace(
            "uv run --script plugins/task-flow/tests/test_inherited.py\n", ""
        )
        code, _calls = self.run_with(repo, [api_payload(plan), api_payload(plan)])
        self.assertEqual(code, 1, "a plan dropping the inherited gate check is invalid")
        self.assertFalse((repo.task / "plan.md").exists())


class CliRunTest(Base):
    """The CLI transport: a fake read-only CLI program, host-captured final message."""

    def setUp(self):
        super().setUp()
        self.runner = self.tmp / "fake-cli-runner.py"
        self.runner.write_text(FAKE_CLI_RUNNER, encoding="utf-8")
        # the runner mechanics are tested with python as the "verified" program; the
        # real codex-only rule is pinned in test_models.py
        patcher = mock.patch.object(models, "VERIFIED_READ_ONLY_PROGRAMS", {"python"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def repo(self, outputs):
        spec = self.tmp / f"spec{self.count}.json"
        spec.write_text(json.dumps(outputs), encoding="utf-8")
        toml = (
            CLI_TOML.replace('"{python}"', json.dumps(sys.executable))
            .replace('"{runner}"', json.dumps(str(self.runner)))
            .replace('"{spec}"', json.dumps(str(spec)))
        )
        return super().repo(toml)

    def run_cli(self, repo):
        return role_run.main(repo.argv())

    def test_cli_valid_plan_published_and_host_captures_final_message(self):
        repo = self.repo([VALID_PLAN])
        code = self.run_cli(repo)
        self.assertEqual(code, 0)
        self.assertEqual(
            (repo.task / "plan.md").read_text(encoding="utf-8"), VALID_PLAN
        )
        record = role_runtime.load_role_metrics(repo.task)[0]
        self.assertEqual(record["transport"], "cli")
        self.assertEqual(record["result"], "ok")
        self.assertIsNone(record["cost_usd"], "subscription: cost unknown, not zero")
        self.assertEqual(
            record["tokens"],
            {"input": 50, "cached": 5, "output": 10},
            "codex-jsonl usage is summed from turn.completed",
        )

    def test_cli_invalid_then_one_repair_then_fail(self):
        repo = self.repo([INVALID_PLAN, INVALID_PLAN])
        (repo.task / "plan.md").write_text("PRIOR\n", encoding="utf-8")
        code = self.run_cli(repo)
        self.assertEqual(code, 1)
        self.assertEqual((repo.task / "plan.md").read_text(encoding="utf-8"), "PRIOR\n")
        record = role_runtime.load_role_metrics(repo.task)[0]
        self.assertEqual(record["repair_attempts"], 1)
        self.assertEqual(record["result"], "failed")
        self.assertEqual(record["profiles"][0]["outcome"], "failed")

    def test_cli_repair_then_valid(self):
        repo = self.repo([INVALID_PLAN, VALID_PLAN])
        code = self.run_cli(repo)
        self.assertEqual(code, 0)
        self.assertEqual(
            (repo.task / "plan.md").read_text(encoding="utf-8"), VALID_PLAN
        )

    def test_cli_blocked_exits_3(self):
        repo = self.repo([BLOCKED_PLAN])
        code = self.run_cli(repo)
        self.assertEqual(code, 3)
        self.assertEqual(
            (repo.task / "plan.md").read_text(encoding="utf-8"), BLOCKED_PLAN
        )

    def test_cli_process_failure_fails_without_publish(self):
        self.repo([VALID_PLAN])
        # a spec file that makes the runner crash: the CLI exits non-zero
        repo_spec = self.tmp / f"spec{self.count - 1}.json"
        runner = self.tmp / "crash-runner.py"
        runner.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
        toml = (
            CLI_TOML.replace('"{python}"', json.dumps(sys.executable))
            .replace('"{runner}"', json.dumps(str(runner)))
            .replace('"{spec}"', json.dumps(str(repo_spec)))
        )
        repo2 = Base.repo(self, toml)
        code = self.run_cli(repo2)
        self.assertEqual(code, 1)
        self.assertFalse((repo2.task / "plan.md").exists())
        record = role_runtime.load_role_metrics(repo2.task)[0]
        self.assertEqual(record["result"], "failed")


class ToolsTest(unittest.TestCase):
    """The planner API tools: read-only, contained, argv-allowlisted."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "x.py").write_text("print('hi')\n", encoding="utf-8")
        (self.root / "README.md").write_text("# readme\n", encoding="utf-8")
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
        self.outside = outside
        self.link = None
        try:
            link = self.root / "link.py"
            link.symlink_to(outside / "secret.txt")
            self.link = link
        except OSError as exc:
            self.skip_symlink = f"cannot create symlinks: {exc}"
        else:
            self.skip_symlink = None
        self.tools = role_runtime.ProducerTools(
            self.root, [self.root], "external-planner", 10
        )

    def test_read_list_grep(self):
        self.assertIn(
            "print('hi')", self.tools.execute("read_file", {"path": "src/x.py"})
        )
        listing = self.tools.execute("list_files", {"path": ".", "pattern": "**/*.py"})
        self.assertIn("src/x.py", listing)
        if self.link is not None:
            self.assertNotIn(
                "link.py", listing, "a symlink escaping the root is skipped"
            )
        grep = self.tools.execute("grep", {"pattern": "readme", "path": "."})
        self.assertIn("README.md:1:# readme", grep)

    def test_path_and_symlink_escapes_denied(self):
        if self.link is None:
            self.skipTest(self.skip_symlink)
        outside = self.outside / "secret.txt"
        result = self.tools.execute("read_file", {"path": str(outside)})
        self.assertIn("outside the selected repository roots", result)
        result = self.tools.execute("read_file", {"path": "link.py"})
        self.assertIn("outside the selected repository roots", result)
        result = self.tools.execute("read_file", {"path": "../outside/secret.txt"})
        self.assertIn("denied", result)

    def test_no_write_tools(self):
        result = self.tools.execute("write_file", {"path": "src/x.py", "content": "x"})
        self.assertIn("no write or edit tools", result)
        result = self.tools.execute(
            "edit_file", {"path": "src/x.py", "old": "a", "new": "b"}
        )
        self.assertIn("no write or edit tools", result)

    def test_run_command_argv_allowlist(self):
        result = self.tools.execute(
            "run_command", {"argv": ["python", "-c", "print(1)"]}
        )
        self.assertIn("allowlist", result)
        result = self.tools.execute("run_command", {"argv": ["git", "add", "."]})
        self.assertIn("read-only", result)
        result = self.tools.execute("run_command", {"argv": ["ls", "a>b"]})
        self.assertIn("shell syntax", result)
        result = self.tools.execute(
            "run_command", {"argv": ["git", "-C", "/etc", "log"]}
        )
        self.assertIn("outside the selected repository", result)
        result = self.tools.execute("run_command", {"argv": []})
        self.assertIn("denied", result)
        # an allowlisted read-only program really runs and its output comes back
        result = self.tools.execute("run_command", {"argv": ["git", "--version"]})
        self.assertIn("exit code: 0", result)
        self.assertIn("git version", result)

    def test_run_command_read_only_holes_denied(self):
        """review round 1 finding 1: the allowlist itself must not mutate."""
        denied = [
            (["find", ".", "-delete"], "denied"),
            (["find", ".", "-exec", "rm", "{}", "+"], "denied"),
            (["find", ".", "-fprint", "x.txt"], "denied"),
            (["rg", "--pre", "python", "x", "."], "denied"),
            (["rg", "--pre-glob", "*.py", "--pre", "x", "."], "denied"),
            (["git", "diff", "--output=out.txt"], "denied"),
            (["git", "log", "--output", "o"], "denied"),
            (["git", "grep", "-O", "sh", "x"], "denied"),
            (["git", "-ccore.pager=sh", "log"], "denied"),
            (["git", "config", "--list"], "denied"),
            (["file", "-C", "x"], "denied"),
            (["jq", "--rawfile", "s", "/etc/passwd", "."], "denied"),
        ]
        for argv, needle in denied:
            with self.subTest(argv=argv):
                result = self.tools.execute("run_command", {"argv": argv})
                self.assertIn(needle, result, argv)
                self.assertNotIn("exit code:", result, f"nothing ran: {argv}")

    def test_run_command_argv_paths_contained(self):
        """review round 1 finding 1: path-looking argv words stay inside the roots."""
        denied = [
            (["cat", "/etc/passwd"], "outside the selected repository roots"),
            (["cat", "../x"], "outside the selected repository roots"),
            (["cat", "../outside/secret.txt"], "outside the selected repository roots"),
            (["ls", str(self.outside)], "outside the selected repository roots"),
            (["grep", "-rn", "secret", str(self.outside / "secret.txt")], "outside"),
        ]
        for argv, needle in denied:
            with self.subTest(argv=argv):
                result = self.tools.execute("run_command", {"argv": argv})
                self.assertIn(needle, result, argv)
                self.assertNotIn("exit code:", result, f"nothing ran: {argv}")
        if self.link is not None:
            # a symlink inside the repo that points outside is contained too
            result = self.tools.execute("run_command", {"argv": ["cat", "link.py"]})
            self.assertIn("outside the selected repository roots", result)
        # paths inside the repo pass containment and the command really runs
        result = self.tools.execute(
            "run_command", {"argv": ["git", "status", "--", "src"]}
        )
        self.assertNotIn("denied", result)
        self.assertIn("exit code:", result)


class MetricsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def record(self, invocation, cost, tokens=None):
        return {
            "schema": "role-metrics/1",
            "role": "planner",
            "invocation_id": invocation,
            "cost_usd": cost,
            "tokens": tokens,
        }

    def test_write_load_and_usd_idempotent(self):
        role_runtime.write_role_metrics(self.dir, self.record("a", 1.5))
        role_runtime.write_role_metrics(self.dir, self.record("b", 2.25))
        role_runtime.write_role_metrics(self.dir, self.record("c", None))
        records = role_runtime.load_role_metrics(self.dir)
        self.assertEqual([r["invocation_id"] for r in records], ["a", "b", "c"])
        total, unknown = role_runtime.role_usd(self.dir)
        self.assertAlmostEqual(total, 3.75)
        self.assertTrue(unknown, "a null cost stays unknown, never zero")
        self.assertEqual(
            role_runtime.role_usd(self.dir), (total, unknown), "idempotent"
        )

    def test_malformed_and_foreign_files_skipped(self):
        (self.dir / "role-planner-bad.json").write_text("{ not json", encoding="utf-8")
        (self.dir / "role-planner-other.json").write_text(
            json.dumps({"schema": "other/1"}), encoding="utf-8"
        )
        role_runtime.write_role_metrics(self.dir, self.record("ok", 1.0))
        self.assertEqual(len(role_runtime.load_role_metrics(self.dir)), 1)
        self.assertEqual(role_runtime.role_usd(self.dir), (1.0, False))

    def test_symlink_metrics_refused(self):
        target = self.dir / "target.json"
        target.write_text("{}", encoding="utf-8")
        link = self.dir / role_runtime.metrics_name("planner", "x")
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"cannot create symlinks: {exc}")
        with self.assertRaises(role_runtime.Refusal):
            role_runtime.write_role_metrics(self.dir, self.record("x", 1.0))

    def test_redaction(self):
        record = {"note": "key sk-fake-123456 here", "nested": {"k": "sk-fake-123456"}}
        redacted = role_runtime.redact_record(record, ["sk-fake-123456"])
        self.assertNotIn("sk-fake-123456", json.dumps(redacted))
        self.assertIn("***", json.dumps(redacted))


class PublishTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.work = self.tmp / ".work"
        self.task = self.work / "t"
        self.task.mkdir(parents=True)

    def ctx(self):
        ctx = role_run.Context()
        ctx.task_dir = self.task
        ctx.artifact_name = "plan.md"
        ctx.role = "planner"
        return ctx

    def test_publish_atomic_and_symlink_refused(self):
        ctx = self.ctx()
        path = role_run.publish(ctx, VALID_PLAN)
        self.assertEqual(path, self.task / "plan.md")
        self.assertEqual(
            (self.task / "plan.md").read_text(encoding="utf-8"), VALID_PLAN
        )
        (self.task / "plan.md").unlink()
        try:
            (self.task / "plan.md").symlink_to(self.tmp / "elsewhere.md")
        except OSError as exc:
            self.skipTest(f"cannot create symlinks: {exc}")
        with self.assertRaises(role_run.Refusal):
            role_run.publish(ctx, VALID_PLAN)
        self.assertFalse(
            (self.tmp / "elsewhere.md").exists(), "never write through a symlink"
        )


class UnicodePathTest(Base):
    """A repository path with spaces and non-ASCII characters works unchanged."""

    def test_spaced_unicode_repo(self):
        base = self.tmp / "moje repo" / "äß"
        repo = Repo(base, API_TOML)
        calls = []
        urlopen = make_urlopen([api_payload(VALID_PLAN)], calls)
        with (
            mock.patch.dict(os.environ, {"FAKE_API_KEY": "sk-fake-123456"}),
            patch_post(urlopen),
        ):
            code = role_run.main(repo.argv())
        self.assertEqual(code, 0)
        self.assertTrue((repo.task / "plan.md").is_file())


class ReadRolesTest(Base):
    def setUp(self) -> None:
        super().setUp()
        from test_role_contracts import (
            REVIEW_PLAN,
            VALID_BACKLOG,
            VALID_REVIEW,
            checks_fixture,
        )

        self.backlog = VALID_BACKLOG
        self.review = VALID_REVIEW
        self.plan = REVIEW_PLAN
        self.checks = checks_fixture
        patcher = mock.patch.dict(
            os.environ, {"FAKE_API_KEY": "fake-key", "FALLBACK_API_KEY": "backup-key"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def backlog_repo(self, from_repo: bool = False, toml: str | None = None) -> tuple:
        repo = self.repo(
            toml or API_TOML.replace('planner = "fake"', 'backlog = "fake"')
        )
        run = repo.root / ".work" / "_autopilot" / "run-x"
        run.mkdir(parents=True)
        (run / "requirements.md").write_text(
            "Original requirement.\n", encoding="utf-8"
        )
        args = [
            "--role",
            "backlog",
            "--run-dir",
            str(run),
            "--project",
            str(repo.root),
            "--worktree",
            "none",
            "--heartbeat",
            "0",
        ]
        if from_repo:
            args.append("--from-repo")
        return repo, run, args

    def reviewer_repo(self, toml: str | None = None) -> tuple:
        repo = self.repo(
            toml or API_TOML.replace('planner = "fake"', 'reviewer = "fake"')
        )
        (repo.task / "status.md").write_text(
            "- round: 1\n- worktree: none\n- base: main\n## Log\nEXCLUDED_NARRATIVE\n",
            encoding="utf-8",
        )
        (repo.task / "plan.md").write_text(self.plan, encoding="utf-8")
        (repo.task / "checks-1.md").write_text(self.checks(), encoding="utf-8")
        (repo.task / "impl-1.json").write_text(
            json.dumps(
                {
                    "model": "implementer-model",
                    "profile": "alias",
                    "status": "ok",
                    "settings": {},
                    "fallback_chain": [{"profile": "alias", "outcome": "ok"}],
                    "narrative": "EXCLUDED_NARRATIVE",
                }
            ),
            encoding="utf-8",
        )
        (repo.task / "review-0.md").write_text("EXCLUDED_NARRATIVE", encoding="utf-8")
        (repo.root / "src").mkdir()
        (repo.root / "src" / "x.py").write_text(
            "def feature(value: int) -> int:\n    return value + 1\n", encoding="utf-8"
        )
        args = repo.argv("--round", "1")
        args[1] = "reviewer"
        return repo, args

    @staticmethod
    def fake_git(worktree: Path, argv: list[str]) -> str:
        if argv[0] == "rev-parse":
            return "abc123\n"
        if argv[0] == "diff" and "--name-only" in argv:
            return "src/x.py\0"
        if argv[0] == "diff":
            return "diff --git a/src/x.py b/src/x.py\n+return value + 1\n"
        if argv[0] == "ls-files":
            return ""
        raise AssertionError(argv)

    def execute(self, args: list[str], outputs: list[str]) -> tuple[int, list]:
        calls = []
        with (
            mock.patch.object(role_run, "git_input", side_effect=self.fake_git),
            mock.patch("autopilot.inspect", return_value="thin: no\n"),
            patch_post(make_urlopen([api_payload(text) for text in outputs], calls)),
        ):
            code = role_run.main(args)
        return code, calls

    def test_api_backlog_and_envelope(self) -> None:
        _repo, run, args = self.backlog_repo()
        code, _ = self.execute(args, [self.backlog])
        self.assertEqual(code, 0)
        self.assertEqual((run / "backlog.md").read_text(encoding="utf-8"), self.backlog)
        self.assertEqual(role_runtime.load_role_metrics(run)[0]["role"], "backlog")
        _repo, run, args = self.backlog_repo(True)
        envelope = json.dumps(
            {
                "requirements.md": "Original requirement.\n- Addition (README.md:1).\n",
                "backlog.md": self.backlog,
            }
        )
        code, _ = self.execute(args, [envelope])
        self.assertEqual(code, 0)
        before = {
            name: (run / name).read_bytes()
            for name in ("backlog.md", "requirements.md")
        }
        code, calls = self.execute(args, ['{"backlog.md":"bad"}'] * 2)
        self.assertEqual((code, len(calls)), (1, 2))
        self.assertEqual(before, {name: (run / name).read_bytes() for name in before})

    def test_pair_rollback_preserves_bytes_and_absence(self) -> None:
        _repo, run, _ = self.backlog_repo(True)
        original_replace = role_run.os.replace
        for existing in (True, False):
            for name in ("requirements.md", "backlog.md"):
                (run / name).unlink(missing_ok=True)
                if existing:
                    (run / name).write_bytes(b"original\r\nbytes\x00")
            calls = []

            def replace(source: Path, target: Path, calls: list = calls) -> None:
                calls.append(target)
                if len(calls) == 2:
                    raise OSError("injected second replacement failure")
                original_replace(source, target)

            with (
                mock.patch.object(role_run.os, "replace", side_effect=replace),
                self.assertRaises(role_runtime.Refusal),
            ):
                role_run.publish_outputs(
                    run, {"requirements.md": "new", "backlog.md": "new"}, "backlog.md"
                )
            for name in ("requirements.md", "backlog.md"):
                self.assertEqual((run / name).exists(), existing)
                if existing:
                    self.assertEqual(
                        (run / name).read_bytes(), b"original\r\nbytes\x00"
                    )

    def test_destinations_refused_before_contact(self) -> None:
        repo, run, args = self.backlog_repo(True)
        outside = repo.root / "outside.md"
        outside.write_text("original", encoding="utf-8")
        try:
            (run / "backlog.md").symlink_to(outside)
        except OSError as exc:
            self.skipTest(str(exc))
        code, calls = self.execute(args, [self.backlog])
        self.assertEqual((code, len(calls)), (2, 0))
        self.assertEqual(outside.read_text(encoding="utf-8"), "original")

    def test_reviewer_prompt_publication_and_invalid_output(self) -> None:
        repo, args = self.reviewer_repo()
        before = (repo.task / "status.md").read_bytes()
        code, calls = self.execute(args, ["invalid", self.review])
        self.assertEqual((code, len(calls)), (0, 2))
        payload = json.loads(calls[0].data)
        prompt = json.dumps(payload)
        self.assertIn("base diff", prompt)
        self.assertIn("changed files", prompt)
        self.assertIn("def feature", prompt)
        self.assertIn("Review checklist", prompt)
        self.assertNotIn("EXCLUDED_NARRATIVE", prompt)
        self.assertEqual((repo.task / "status.md").read_bytes(), before)
        self.assertEqual(
            (repo.task / "review-1.md").read_text(encoding="utf-8"), self.review
        )
        code, calls = self.execute(args, ["bad", "bad"])
        self.assertEqual((code, len(calls)), (1, 2))
        self.assertEqual(
            (repo.task / "review-1.md").read_text(encoding="utf-8"), self.review
        )

    def test_review_transport_failure_preserves_artifact_and_usage_uncertainty(
        self,
    ) -> None:
        repo, args = self.reviewer_repo()
        review_path = repo.task / "review-1.md"
        review_path.write_bytes(b"PREVIOUS REVIEW\r\n")
        status = (repo.task / "status.md").read_bytes()
        calls = []
        with (
            mock.patch.object(role_run, "git_input", side_effect=self.fake_git),
            patch_post(make_urlopen([urllib.error.URLError("unavailable")] * 4, calls)),
        ):
            self.assertEqual(role_run.main(args), 1)
        self.assertEqual(review_path.read_bytes(), b"PREVIOUS REVIEW\r\n")
        self.assertEqual((repo.task / "status.md").read_bytes(), status)
        record = role_runtime.load_role_metrics(repo.task)[0]
        self.assertIsNone(record["tokens"])
        self.assertIsNone(record["cost_usd"])

    def test_strict_provider_and_warn_matching_identity(self) -> None:
        config = API_TOML.replace('planner = "fake"', 'reviewer = "fake"')
        repo, args = self.reviewer_repo(
            config + '\n[gates]\nreviewer_independence = "different-provider"\n'
        )
        code, calls = self.execute(args, [self.review])
        self.assertEqual((code, len(calls)), (2, 0))
        repo, args = self.reviewer_repo(config)
        (repo.task / "impl-1.json").write_text(
            json.dumps(
                {
                    "model": "fake-model",
                    "profile": "alias",
                    "status": "ok",
                    "settings": {},
                    "fallback_chain": [{"profile": "alias", "outcome": "ok"}],
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(role_run, "log") as log:
            self.assertEqual(self.execute(args, [self.review])[0], 0)
        self.assertTrue(any("matches" in str(call) for call in log.call_args_list))

    def test_review_preflight_requires_complete_inputs(self) -> None:
        for mutation in (
            "round-zero",
            "round-mismatch",
            "missing-checks",
            "bad-base",
            "large-file",
            "missing-file",
            "deleted-binary",
            "bad-checks",
        ):
            with self.subTest(mutation=mutation):
                repo, args = self.reviewer_repo()
                if mutation == "round-zero":
                    args[-1] = "0"
                elif mutation == "round-mismatch":
                    args[-1] = "2"
                elif mutation == "missing-checks":
                    (repo.task / "checks-1.md").unlink()
                elif mutation == "large-file":
                    (repo.root / "src/x.py").write_text(
                        "x" * (role_run.MAX_REQUIRED_INPUT + 1), encoding="utf-8"
                    )
                elif mutation in ("missing-file", "deleted-binary"):
                    (repo.root / "src/x.py").unlink()
                elif mutation == "bad-checks":
                    (repo.task / "checks-1.md").write_text("missing", encoding="utf-8")
                calls = []

                def git(
                    worktree: Path, argv: list[str], mutation: str = mutation
                ) -> str:
                    if mutation == "bad-base" or (
                        mutation == "missing-file" and argv[0] == "show"
                    ):
                        raise role_runtime.Refusal("unresolved git input")
                    if mutation == "deleted-binary" and argv[0] == "show":
                        return "binary\0content"
                    return self.fake_git(worktree, argv)

                with (
                    mock.patch.object(role_run, "git_input", side_effect=git),
                    patch_post(make_urlopen([], calls)),
                ):
                    self.assertEqual(role_run.main(args), 2)
                self.assertEqual(calls, [])
                self.assertFalse((repo.task / "review-1.md").exists())

    def test_api_exclusions_across_tools_and_symlinks(self) -> None:
        repo, _args = self.reviewer_repo()
        tools = role_runtime.ProducerTools(
            repo.root, [repo.root, repo.task], "external-reviewer", 1
        )
        for path in (
            repo.task / "status.md",
            repo.task / "impl-1.json",
            repo.task / "review-0.md",
        ):
            self.assertIn("denied", tools.execute("read_file", {"path": str(path)}))
        self.assertNotIn(
            "EXCLUDED_NARRATIVE",
            tools.execute("grep", {"path": ".", "pattern": "EXCLUDED_NARRATIVE"}),
        )

        self.assertIn(
            "denied",
            tools.execute(
                "run_command", {"argv": ["git", "show", "main:.work/task-x/status.md"]}
            ),
        )
        try:
            (repo.root / "alias.md").symlink_to(repo.task / "status.md")
        except OSError:
            return  # direct and recursive exclusions remain exercised on restricted Windows
        self.assertIn("denied", tools.execute("read_file", {"path": "alias.md"}))
        self.assertNotIn(
            "EXCLUDED_NARRATIVE",
            tools.execute("grep", {"path": ".", "pattern": "EXCLUDED_NARRATIVE"}),
        )

    def test_touched_alias_to_excluded_input_refused_before_contact(self) -> None:
        repo, args = self.reviewer_repo()
        touched = repo.root / "src/x.py"
        original_resolve = Path.resolve

        def resolve(path: Path, *args, **kwargs) -> Path:
            # Exercise canonical-path policy even on Windows without symlink privileges.
            if original_resolve(path) == original_resolve(touched):
                return repo.task / "status.md"
            return original_resolve(path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", resolve):
            code, calls = self.execute(args, [self.review])
        self.assertEqual((code, len(calls)), (2, 0))
        self.assertFalse((repo.task / "review-1.md").exists())

    def test_independence_and_fallback_candidate(self) -> None:
        config = (
            API_TOML.replace('planner = "fake"', 'reviewer = "fake"')
            + '\n[gates]\nreviewer_independence = "different-model"\n'
        )
        repo, args = self.reviewer_repo(config)
        record = repo.task / "impl-1.json"
        identity = json.loads(record.read_text(encoding="utf-8"))
        record.write_text(
            json.dumps(dict(identity, model="fake-model")),
            encoding="utf-8",
        )
        code, calls = self.execute(args, [self.review])
        self.assertEqual((code, len(calls)), (2, 0))
        record.write_text(
            json.dumps(dict(identity, model="other-model")),
            encoding="utf-8",
        )
        self.assertEqual(self.execute(args, [self.review])[0], 0)
        record.write_text(
            json.dumps({"model": "other-model", "cli": {"driver": "cli"}}),
            encoding="utf-8",
        )
        self.assertEqual(self.execute(args, [self.review])[0], 2)
        record.unlink()
        self.assertEqual(self.execute(args, [self.review])[0], 2)
        fallback_config = config.replace(
            "max_turns = 10", 'max_turns = 10\nfallback = ["fallback"]'
        )
        repo, args = self.reviewer_repo(fallback_config)
        (repo.task / "impl-1.json").write_text(
            json.dumps(dict(identity, model="fallback-model")), encoding="utf-8"
        )
        calls = []
        unavailable = urllib.error.URLError("primary unavailable")
        with (
            mock.patch.object(role_run, "git_input", side_effect=self.fake_git),
            patch_post(make_urlopen([unavailable] * 4, calls)),
        ):
            self.assertEqual(role_run.main(args), 1)
        self.assertTrue(calls)
        self.assertTrue(
            all("fallback.example" not in request.full_url for request in calls)
        )
        self.assertFalse((repo.task / "review-1.md").exists())

    def test_incomplete_or_conflicting_implementer_identity_is_unknown(self) -> None:
        config = (
            API_TOML.replace('planner = "fake"', 'reviewer = "fake"')
            + '\n[gates]\nreviewer_independence = "different-model"\n'
        )
        for mutation in ("missing-chain", "conflicting-profile", "conflicting-status"):
            with self.subTest(mutation=mutation):
                repo, args = self.reviewer_repo(config)
                path = repo.task / "impl-1.json"
                record = json.loads(path.read_text(encoding="utf-8"))
                if mutation == "missing-chain":
                    record.pop("fallback_chain")
                elif mutation == "conflicting-profile":
                    record["fallback_chain"][-1]["profile"] = "other"
                else:
                    record["status"] = "failed"
                path.write_text(json.dumps(record), encoding="utf-8")
                code, calls = self.execute(args, [self.review])
                self.assertEqual((code, len(calls)), (2, 0))

    def test_review_in_a_nested_task_worktree(self) -> None:
        repo, args = self.reviewer_repo()
        worktree = repo.task / "worktree"
        (worktree / "src").mkdir(parents=True)
        (worktree / "src/x.py").write_text(
            "def feature(value: int) -> int:\n    return value + 1\n", encoding="utf-8"
        )
        (repo.task / "status.md").write_text(
            "- round: 1\n- worktree: .work/task-x/worktree\n- base: main\n## Log\nEXCLUDED_NARRATIVE\n",
            encoding="utf-8",
        )
        args[args.index("--worktree") + 1] = str(worktree)
        code, calls = self.execute(args, [self.review])
        self.assertEqual((code, len(calls)), (0, 1))
        tools = role_runtime.ProducerTools(
            worktree, [worktree, repo.root], "external-reviewer", 1
        )
        self.assertIn("def feature", tools.execute("read_file", {"path": "src/x.py"}))
        self.assertIn("denied", tools.execute("read_file", {"path": "../status.md"}))

    def test_cli_backlog_and_review(self) -> None:
        runner = self.tmp / "read-role-cli.py"
        runner.write_text(FAKE_CLI_RUNNER, encoding="utf-8")
        with mock.patch.object(models, "VERIFIED_READ_ONLY_PROGRAMS", {"python"}):
            for role, output in (("backlog", self.backlog), ("reviewer", self.review)):
                spec = self.tmp / f"{role}-spec.json"
                spec.write_text(json.dumps(["invalid", output]), encoding="utf-8")
                config = (
                    CLI_TOML.replace('"{python}"', json.dumps(sys.executable))
                    .replace('"{runner}"', json.dumps(str(runner)))
                    .replace('"{spec}"', json.dumps(str(spec)))
                    .replace('planner = "fakecli"', f'{role} = "fakecli"')
                )
                if role == "backlog":
                    repo, directory, args = self.backlog_repo(toml=config)
                else:
                    repo, args = self.reviewer_repo(config)
                    directory = repo.task
                with (
                    mock.patch.object(role_run, "git_input", side_effect=self.fake_git),
                    mock.patch("autopilot.inspect", return_value="thin: no\n"),
                ):
                    self.assertEqual(role_run.main(args), 0)
                self.assertEqual(
                    role_runtime.load_role_metrics(directory)[0]["repair_attempts"], 1
                )
                name = "backlog.md" if role == "backlog" else "review-1.md"
                self.assertEqual((directory / name).read_text(encoding="utf-8"), output)


class PlantedReviewFixturesTest(unittest.TestCase):
    """Semantic oracles and contract acceptance are deliberately different measures."""

    def test_frozen_defects_and_clean_control(self) -> None:
        import role_contracts
        from test_role_contracts import REVIEW_PLAN, VALID_REVIEW, checks_fixture

        variants = {
            "implementation-defect": (
                "def feature(value: int) -> int:\n    return value - 1\n",
                False,
            ),
            "missing-acceptance": (
                "def feature(value: int) -> int:\n    return value + 1 if value >= 0 else value\n",
                False,
            ),
            "out-of-scope": (
                "def feature(value: int) -> int:\n    return value + 1\n",
                True,
            ),
            "clean-control": (
                "def feature(value: int) -> int:\n    return value + 1\n",
                False,
            ),
        }
        checks = role_contracts.parse_checks(
            checks_fixture(), round_number=1, commands=["python test_feature.py"]
        )
        with tempfile.TemporaryDirectory() as temporary:
            for name, (source, extra) in variants.items():
                directory = Path(temporary) / name
                directory.mkdir()
                (directory / "feature.py").write_text(source, encoding="utf-8")
                if extra:
                    (directory / "unrelated.py").write_text(
                        "UNREQUESTED = True\n", encoding="utf-8"
                    )
                namespace = runpy.run_path(str(directory / "feature.py"))
                semantic_pass = (
                    namespace["feature"](0) == 1
                    and namespace["feature"](-2) == -1
                    and not (directory / "unrelated.py").exists()
                )
                self.assertEqual(semantic_pass, name == "clean-control")
                # A fake model saying PASS has valid syntax even for planted defects.
                # This is never scored as the reviewer detecting the semantic defect.
                self.assertTrue(
                    role_contracts.validate_review(
                        VALID_REVIEW, round_number=1, plan=REVIEW_PLAN, current=checks
                    )
                )

    def test_known_usage_subtotals_and_duplicate_invocations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            record = {
                "schema": "role-metrics/1",
                "role": "reviewer",
                "invocation_id": "same",
                "cost_usd": None,
                "cost_known_usd": 1.25,
                "tokens": None,
                "tokens_known": {"input": 100, "output": 20},
            }
            role_runtime.write_role_metrics(directory, record)
            (directory / "role-reviewer-copy.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
            self.assertEqual(
                role_runtime.invocation_usd(directory)["total"], (1.25, True)
            )
            self.assertEqual(role_runtime.invocation_tokens(directory), (120, True))
            repo = Repo(directory / "r", API_TOML)
            with mock.patch.object(
                models, "user_file", return_value=directory / "absent"
            ):
                profiles, _ = models.load(repo.root)
            ledger = role_runtime.UsageLedger(profiles)
            ledger.add_api("fake", api_payload("answer"))
            ledger.add_api("fake", {"choices": []})
            ledger.add_api("fake", api_payload("answer"))
            self.assertIsNone(ledger.tokens())
            self.assertIsNone(ledger.cost())
            self.assertEqual(ledger.tokens_known()["input"], 200)
            self.assertGreater(ledger.cost_known(), 0)

    def test_unreadable_role_usage_cannot_establish_a_known_token_total(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            role_runtime.write_role_metrics(
                directory,
                {
                    "schema": "role-metrics/1",
                    "role": "reviewer",
                    "invocation_id": "known",
                    "tokens": {"input": 100, "output": 20},
                },
            )
            (directory / "role-backlog-broken.json").write_text(
                "{incomplete", encoding="utf-8"
            )
            self.assertEqual(role_runtime.invocation_tokens(directory), (120, True))


if __name__ == "__main__":
    unittest.main()
