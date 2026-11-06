# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/cli_driver.py with Python fake CLIs in temp directories.

Run: uv run --script plugins/task-flow/tests/test_cli_driver.py

Every fake CLI is a small Python program written into a temporary directory and run
with sys.executable; no real agent CLI, credential, login or repository state is
involved. The Windows shim path is exercised through build_popen's `windows` override,
so the test runs unchanged on Linux, macOS and Windows.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import cli_driver

PROMPT = "# Round context\nPlan: do the thing.\nConventions: be nice.\n"


def fake_cli(tmp: Path, name: str, body: str) -> Path:
    """Write a fake CLI program and return its path."""
    path = Path(tmp) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def run(
    fake: Path,
    worktree: Path,
    *,
    prompt_via="stdin",
    usage_format="none",
    timeout=60,
    env=None,
    prompt=PROMPT,
    extra_args=(),
    on_event=None,
    secrets=(),
):
    return cli_driver.run_cli(
        command=[sys.executable, str(fake), *extra_args],
        prompt=prompt,
        prompt_via=prompt_via,
        usage_format=usage_format,
        timeout_seconds=timeout,
        worktree=worktree,
        env=env if env is not None else dict(os.environ),
        on_event=on_event,
        secrets=list(secrets),
    )


class CliDriverTest(unittest.TestCase):
    def test_benchmark_environment_is_opt_in_and_per_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            env = {
                "PATH": os.environ.get("PATH", ""),
                "UNRELATED_SECRET": "private",
                "APPROVED_TOKEN": "approved",
                "HOME": "personal",
                "CODEX_HOME": "personal-config",
            }
            self.assertIs(cli_driver.benchmark_environment(env), env)
            for tool, variable in cli_driver.BENCH_TOOL_ENV.items():
                policy = {
                    "tool": tool,
                    "root": str(root / tool),
                    "pass_env": ["APPROVED_TOKEN"],
                }
                selected = cli_driver.benchmark_environment(
                    {**env, cli_driver.BENCH_ENV: json.dumps(policy)}
                )
                self.assertNotIn("UNRELATED_SECRET", selected)
                self.assertNotIn(cli_driver.BENCH_ENV, selected)
                self.assertEqual(selected["APPROVED_TOKEN"], "approved")
                self.assertEqual(selected[variable], str(root / tool / "tool"))
                self.assertTrue(Path(selected["HOME"]).is_dir())
                self.assertNotEqual(selected["HOME"], env["HOME"])

    def test_fake_cli_receives_only_approved_benchmark_environment(self) -> None:
        fake = fake_cli(
            self.tmp,
            "environment.py",
            "import os, json\nprint(json.dumps(dict(os.environ)))\n",
        )
        state = self.tmp / "isolated"
        env = {
            **os.environ,
            "UNRELATED_SECRET": "private",
            "APPROVED_TOKEN": "approved",
            cli_driver.BENCH_ENV: json.dumps(
                {"tool": "codex", "root": str(state), "pass_env": ["APPROVED_TOKEN"]}
            ),
        }
        result = run(fake, self.worktree, env=env)
        self.assertEqual(result.exit_code, 0, result.stderr)
        selected = json.loads(result.stdout)
        self.assertNotIn("UNRELATED_SECRET", selected)
        self.assertEqual(selected["CODEX_HOME"], str(state / "tool"))
        self.assertEqual(selected["APPROVED_TOKEN"], "approved")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        # a worktree with spaces and Unicode in its path
        self.worktree = self.tmp / "wörk tree" / "ünicode wörk"
        self.worktree.mkdir(parents=True)

    # ------------------------------------------------------------- transports

    def test_stdin_transport_sends_prompt_and_runs_in_worktree(self):
        fake = fake_cli(
            self.tmp,
            "stdin_cli.py",
            "import sys, os, pathlib\n"
            "data = sys.stdin.read()\n"
            "pathlib.Path('made by the cli.txt').write_text('x', encoding='utf-8')\n"
            "print('cwd_ok=' + str(os.getcwd() == sys.argv[1]))\n"
            "print('prompt=' + data)\n",
        )
        result = run(
            fake, self.worktree, prompt_via="stdin", extra_args=[str(self.worktree)]
        )
        self.assertFalse(result.timed_out)
        self.assertEqual(result.exit_code, 0, result.stderr)
        # the child ran with the spaced/Unicode worktree as its cwd and could write there
        self.assertIn("cwd_ok=True", result.stdout)
        self.assertTrue((self.worktree / "made by the cli.txt").is_file())
        self.assertIn("prompt=" + PROMPT, result.stdout)
        self.assertIsNone(result.usage)  # usage_format none keeps usage unknown
        self.assertEqual(result.final_message, result.stdout.strip())

    def test_file_transport_uses_temp_prompt_file_outside_worktree(self):
        recorded = self.tmp / "recorded.json"
        fake = fake_cli(
            self.tmp,
            "file_cli.py",
            "import sys, json, pathlib\n"
            "path = pathlib.Path(sys.argv[1])\n"
            f"recorded = pathlib.Path({str(recorded)!r})\n"
            "recorded.write_text(json.dumps({'path': str(path), 'text': path.read_text(encoding='utf-8'), 'inside': str(path).startswith(sys.argv[2])}), encoding='utf-8')\n"
            "print('ok')\n",
        )
        result = cli_driver.run_cli(
            command=[sys.executable, str(fake), "{prompt_file}", "{worktree}"],
            prompt=PROMPT,
            prompt_via="file",
            usage_format="none",
            timeout_seconds=60,
            worktree=self.worktree,
            env=dict(os.environ),
        )
        self.assertEqual(result.exit_code, 0, result.stderr)
        data = json.loads(recorded.read_text(encoding="utf-8"))
        self.assertEqual(data["text"], PROMPT)
        self.assertFalse(
            data["inside"], "the prompt file must live outside the worktree"
        )
        self.assertFalse(
            Path(data["path"]).exists(), "scratch files are removed after the run"
        )

    def test_arg_transport_appends_prompt_as_one_final_argument(self):
        fake = fake_cli(
            self.tmp,
            "arg_cli.py",
            "import sys\n"
            "print('argc=' + str(len(sys.argv)))\n"
            "print('last=' + sys.argv[-1])\n"
            "print('second=' + sys.argv[1])\n",
        )
        result = run(
            fake, self.worktree, prompt_via="arg", extra_args=["--flag", "a b"]
        )
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn("argc=4", result.stdout)
        self.assertIn("second=--flag", result.stdout)
        self.assertIn("last=" + PROMPT, result.stdout)
        # the prompt is one argument, never split
        self.assertNotIn("prompt=# Round", result.stdout)

    def test_native_argv_is_not_shell_interpreted(self):
        fake = fake_cli(
            self.tmp,
            "argv_cli.py",
            "import sys\nfor i, a in enumerate(sys.argv[1:]):\n    print(f'arg{i}={a}')\n",
        )
        weird = "a b&c|d;e`f$g"
        result = run(fake, self.worktree, extra_args=[weird, "two words"])
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn(f"arg0={weird}", result.stdout)
        self.assertIn("arg1=two words", result.stdout)

    def test_missing_program_is_refused(self):
        with self.assertRaises(cli_driver.CliRefused) as ctx:
            cli_driver.run_cli(
                command=["no-such-cli-xyz", "--flag"],
                prompt=PROMPT,
                prompt_via="stdin",
                usage_format="none",
                timeout_seconds=10,
                worktree=self.worktree,
                env=dict(os.environ),
            )
        self.assertIn("program not found", str(ctx.exception))

    # ------------------------------------------------------------- placeholders

    def test_expand_arguments_only_allows_valid_placeholders(self):
        values = {
            "worktree": "/wt",
            "prompt_file": "/tmp/p",
            "last_message_file": "/tmp/m",
        }
        self.assertEqual(
            cli_driver.expand_arguments(
                ["--cd", "{worktree}", "{prompt_file}"], values
            ),
            ["--cd", "/wt", "/tmp/p"],
        )
        with self.assertRaises(cli_driver.CliRefused) as ctx:
            cli_driver.expand_arguments(["{prompt}"], values)
        self.assertIn("{prompt}", str(ctx.exception))
        with self.assertRaises(cli_driver.CliRefused):
            cli_driver.expand_arguments(["{env.HOME}"], values)

    def test_build_popen_windows_shim_and_metachar_refusal(self):
        shim = self.tmp / "tool dir" / "my tool.cmd"
        shim.parent.mkdir(parents=True)
        shim.write_text("@echo off\r\n", encoding="utf-8", newline="")
        env = {"PATH": str(self.tmp), "SYSTEMROOT": r"C:\Win"}
        line, is_shim = cli_driver.build_popen(
            [str(shim), "arg with spaces", "--flag=x"],
            "stdin",
            "prompt",
            env,
            self.worktree,
            windows=True,
        )
        self.assertTrue(is_shim)
        self.assertEqual(
            line,
            f'"C:\\Win\\System32\\cmd.exe" /d /s /c ""{shim}" "arg with spaces" --flag=x"',
        )
        # metacharacters are refused, not escaped
        with self.assertRaises(cli_driver.CliRefused) as ctx:
            cli_driver.build_popen(
                [str(shim), "a&b"], "stdin", "prompt", env, self.worktree, windows=True
            )
        self.assertIn("&", str(ctx.exception))
        # prompt_via "arg" would put the prompt on the shim command line: refused
        with self.assertRaises(cli_driver.CliRefused) as ctx:
            cli_driver.build_popen(
                [str(shim), "x"],
                "arg",
                "prompt with & metachar",
                env,
                self.worktree,
                windows=True,
            )
        self.assertIn('prompt_via = "arg"', str(ctx.exception))
        # a native program gets a plain argv list, prompt appended for "arg"
        exe = self.tmp / "tool.exe"
        exe.write_bytes(b"")
        argv, is_shim = cli_driver.build_popen(
            [str(exe), "x"], "arg", "the prompt", env, self.worktree, windows=True
        )
        self.assertFalse(is_shim)
        self.assertEqual(argv, [str(exe), "x", "the prompt"])
        argv, is_shim = cli_driver.build_popen(
            [str(exe)], "stdin", "the prompt", env, self.worktree, windows=False
        )
        self.assertEqual(argv, [str(exe)])

    def test_find_program_resolves_relative_to_run_dir_and_path(self):
        tool = self.tmp / "tools" / "mytool.cmd"
        tool.parent.mkdir()
        tool.write_text("@echo off\r\n", encoding="utf-8", newline="")
        tool.chmod(0o755)  # POSIX `which` only finds executable files
        env = {"PATH": str(self.tmp / "tools")}
        self.assertEqual(
            cli_driver.find_program("mytool.cmd", self.worktree, env), str(tool)
        )
        self.assertEqual(
            cli_driver.find_program(str(tool), self.worktree, env), str(tool)
        )
        self.assertIsNone(cli_driver.find_program("missing-xyz", self.worktree, env))
        self.assertEqual(
            cli_driver.find_program(sys.executable, self.worktree, env), sys.executable
        )

    # ------------------------------------------------------------- jsonl usage

    def jsonl_cli(self, events, exit_code=0, extra_stdout=""):
        body = (
            "import sys, json\n"
            f"events = {json.dumps(events)}\n"
            "for e in events:\n    print(json.dumps(e))\n"
            f"print({extra_stdout!r})\n"
            f"sys.exit({exit_code})\n"
        )
        return fake_cli(self.tmp, "jsonl_cli.py", body)

    def test_codex_jsonl_usage_summed_cached_and_reasoning_separate(self):
        events = [
            {"type": "thread.started", "id": "t"},  # unknown event: tolerated
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 400,
                    "output_tokens": 50,
                    "reasoning_output_tokens": 20,
                },
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 500,
                    "cached_input_tokens": 100,
                    "output_tokens": 10,
                },
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "all done"},
            },
        ]
        fake = self.jsonl_cli(events)
        seen = []
        result = run(
            fake, self.worktree, usage_format="codex-jsonl", on_event=seen.append
        )
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(
            result.usage,
            {"input": 1500, "cached": 500, "reasoning": 20, "output": 60},
        )
        self.assertFalse(result.usage_malformed)
        self.assertFalse(result.failure_event)
        self.assertEqual(result.final_message, "all done")
        self.assertGreaterEqual(len(seen), 4)
        self.assertEqual(seen[1]["event"], "turn.completed")
        self.assertEqual(
            seen[1]["usage"],
            {
                "input_tokens": 1000,
                "cached_input_tokens": 400,
                "output_tokens": 50,
                "reasoning_output_tokens": 20,
            },
        )

    def test_codex_jsonl_final_message_from_last_message_file(self):
        recorded = self.tmp / "last.txt"
        fake = fake_cli(
            self.tmp,
            "lm_cli.py",
            "import sys, json, pathlib\n"
            "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 3, 'output_tokens': 1}}))\n"
            "pathlib.Path(sys.argv[1]).write_text('BLOCKED: the plan is wrong', encoding='utf-8')\n",
        )
        result = cli_driver.run_cli(
            command=[sys.executable, str(fake), "{last_message_file}"],
            prompt=PROMPT,
            prompt_via="stdin",
            usage_format="codex-jsonl",
            timeout_seconds=60,
            worktree=self.worktree,
            env=dict(os.environ),
        )
        self.assertEqual(
            result.usage, {"input": 3, "cached": 0, "reasoning": 0, "output": 1}
        )
        self.assertEqual(result.final_message, "BLOCKED: the plan is wrong")
        self.assertFalse(recorded.exists())

    def test_malformed_recognized_usage_is_unknown_not_zero(self):
        fake = self.jsonl_cli(
            [{"type": "turn.completed", "usage": {"input_tokens": "many"}}]
        )
        result = run(fake, self.worktree, usage_format="codex-jsonl")
        self.assertIsNone(result.usage)
        self.assertTrue(result.usage_malformed)

    def test_failure_event_cannot_become_success_on_exit_zero(self):
        fake = self.jsonl_cli(
            [
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
                {"type": "turn.failed", "error": {"message": "boom"}},
            ],
            exit_code=0,
        )
        result = run(fake, self.worktree, usage_format="codex-jsonl")
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.failure_event)

    def test_plain_output_usage_stays_null(self):
        fake = fake_cli(
            self.tmp, "plain.py", "print('working...')\nprint('done here')\n"
        )
        result = run(fake, self.worktree, usage_format="none")
        self.assertIsNone(result.usage)
        self.assertEqual(result.final_message, "working...\ndone here")

    # ------------------------------------------------------------- deadline, pipes, cleanup

    def test_timeout_kills_process_and_its_child(self):
        marker = self.tmp / "child-alive.txt"
        child_code = (
            f"import time; time.sleep(30); open({str(marker)!r}, 'w').write('alive')"
        )
        fake = fake_cli(
            self.tmp,
            "sleep_cli.py",
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {json.dumps(child_code)}])\n"
            "print('parent sleeping', flush=True)\n"
            "time.sleep(30)\n",
        )
        started = time.monotonic()
        result = run(fake, self.worktree, timeout=2)
        elapsed = time.monotonic() - started
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.exit_code)
        self.assertLess(elapsed, 15, "the deadline plus bounded cleanup")
        time.sleep(0.2)
        self.assertFalse(marker.exists(), "the child must be killed with the tree")

    def test_large_concurrent_output_cannot_deadlock(self):
        fake = fake_cli(
            self.tmp,
            "flood_cli.py",
            "import sys\n"
            "for i in range(20000):\n"
            "    sys.stdout.write('out line %d padding padding padding\\n' % i)\n"
            "    sys.stderr.write('err line %d padding padding padding\\n' % i)\n"
            "sys.stdout.flush(); sys.stderr.flush()\n",
        )
        result = run(fake, self.worktree, timeout=60)
        self.assertEqual(result.exit_code, 0, result.stderr[:400])
        self.assertFalse(result.timed_out)
        self.assertLessEqual(len(result.stdout), cli_driver.OUTPUT_CAP + 200)
        self.assertLessEqual(len(result.stderr), cli_driver.OUTPUT_CAP + 200)
        self.assertIn("truncated", result.stdout)

    def test_scratch_files_removed_on_every_exit(self):
        before = set(Path(tempfile.gettempdir()).glob("task-flow-cli-*"))
        fake = fake_cli(self.tmp, "ok.py", "print('fine')\n")
        run(fake, self.worktree)
        fake2 = fake_cli(self.tmp, "boom.py", "import sys; sys.exit(7)\n")
        result = run(fake2, self.worktree)
        self.assertEqual(result.exit_code, 7)
        after = set(Path(tempfile.gettempdir()).glob("task-flow-cli-*"))
        self.assertEqual(after - before, set())

    # ------------------------------------------------------------- redaction

    def test_secret_values_and_redaction(self):
        env = {
            "PATH": "/usr/bin",
            "CODEX_API_KEY": "sk-super-secret-value-1",
            "MY_TOKEN": "tok-abcdef-1234",
            "PLAIN": "not-secret",
        }
        secrets = cli_driver.secret_values(env, ["CODEX_API_KEY"])
        self.assertIn("sk-super-secret-value-1", secrets)
        self.assertIn("tok-abcdef-1234", secrets)
        self.assertNotIn("not-secret", secrets)
        fake = fake_cli(
            self.tmp,
            "echo_secret.py",
            "import os\nprint('key=' + os.environ.get('CODEX_API_KEY', ''))\n",
        )
        result = run(fake, self.worktree, env=env, secrets=secrets)
        self.assertNotIn("sk-super-secret-value-1", result.stdout)
        self.assertIn("key=***", result.stdout)
        redacted = cli_driver.redact("value sk-super-secret-value-1 here", secrets)
        self.assertEqual(redacted, "value *** here")
        # short values are not replaced (they would mangle ordinary text)
        self.assertEqual(cli_driver.redact("ab", ["ab"]), "ab")

    def test_jsonl_event_summaries_are_redacted(self):
        # an agent_message that quotes the API key must never reach on_event
        # records or CliRun.events unredacted
        secret = "sk-codex-secret-123456"
        events = [
            {
                "type": "item.started",
                "item": {
                    "type": "agent_message",
                    "text": f"using key {secret} now",
                },
            },
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 5, "output_tokens": 1},
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": f"used key {secret} ok"},
            },
        ]
        fake = self.jsonl_cli(events)
        seen = []
        result = run(
            fake,
            self.worktree,
            usage_format="codex-jsonl",
            on_event=seen.append,
            secrets=[secret],
        )
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.final_message, "used key *** ok")
        self.assertGreaterEqual(len(seen), 3)
        messages = [record["message"] for record in seen if "message" in record]
        self.assertEqual(len(messages), 2)
        for message in messages:
            self.assertNotIn(secret, message)
            self.assertIn("***", message)
        for record in seen:
            self.assertNotIn(secret, json.dumps(record))
        for record in result.events:
            self.assertNotIn(secret, json.dumps(record))
        # without secrets configured the driver cannot know the value to redact
        result_plain = run(fake, self.worktree, usage_format="codex-jsonl")
        self.assertIn(secret, result_plain.final_message or "")


if __name__ == "__main__":
    unittest.main()
