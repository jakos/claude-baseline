# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/implement.py against a fake OpenAI-compatible server.

Each test builds a temp git repo with a worktree and `.work/t1/` fixtures, points a project
models.toml at a scripted server on 127.0.0.1 and runs the script by subprocess.
No real keys, no network beyond 127.0.0.1, no change to the real ~/.claude.

Run: uv run --script plugins/task-flow/tests/test_implement.py
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPT = PLUGIN / "scripts" / "implement.py"

PLAN = """# Plan: demo
## Goal
Do it.
## Profiles
python: src/app.py
docs: README.md
## Steps
1. Change things.
"""
STATUS = """# Status: t1
- phase: implementing
- round: 1
- base: main
- plan-approved: yes (user, 2026-10-31)
## History
- 2026-10-31T00:00:00Z new
## Log
"""
CHECKS0 = "# Checks: round 0\n| 1 | `x` | PASS | 0 | 1s |\n"
REVIEW1 = "# Review: round 1\n## Fix list\n1. Rename the helper in src/app.py.\n"
USAGE = {"prompt_tokens": 10, "completion_tokens": 5}


def tool_call(name, args, call_id="c1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def reply(*calls, content=None, usage=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = list(calls)
    return {
        "choices": [{"message": message}],
        "usage": usage if usage is not None else USAGE,
    }


def toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{k} = {toml_value(v)}" for k, v in value.items()) + " }"
        )
    return repr(value)


def command(*argv):
    if os.name == "nt":
        return subprocess.list2cmdline(list(argv))
    return shlex.join(argv)


def python_command(code):
    return command(sys.executable, "-c", code)


class FakeServer:
    """Serves a scripted queue: dict -> 200 JSON, int -> that status, (int, headers)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                outer.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "body": body,
                    }
                )
                item = outer.responses.pop(0) if outer.responses else 500
                headers = {}
                if isinstance(item, dict):
                    status, payload = 200, item
                elif isinstance(item, tuple):
                    (status, headers), payload = item, {"error": "scripted"}
                else:
                    status, payload = item, {"error": "scripted"}
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def tool_results(self, index):
        messages = self.requests[index]["body"]["messages"]
        return [m["content"] for m in messages if m["role"] == "tool"]


def git(cwd, *args):
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    return done.stdout


def tree_snapshot(path, skip=()):
    files = {}
    for item in sorted(Path(path).rglob("*")):
        rel = item.relative_to(path)
        if item.is_file() and not (set(rel.parts) & set(skip)):
            files[rel.as_posix()] = item.read_bytes()
    return files


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class ImplementTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "core.autocrlf", "false")
        (self.repo / "README.md").write_text("hello\n", encoding="utf-8", newline="")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "app.py").write_text(
            "def foo():\n    return 1\n", encoding="utf-8", newline=""
        )
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        self.task = self.repo / ".work" / "t1"
        self.task.mkdir(parents=True)
        self.write_task("plan.md", PLAN)
        self.write_task("status.md", STATUS)
        self.write_task("checks-0.md", CHECKS0)
        self.worktree = self.task / "worktree"
        git(self.repo, "worktree", "add", "-q", str(self.worktree), "-b", "task/t1")
        self.head = git(self.repo, "rev-parse", "HEAD").strip()
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.close()

    def server(self, responses):
        server = FakeServer(responses)
        self.servers.append(server)
        return server

    def write_task(self, name, text):
        (self.task / name).write_text(text, encoding="utf-8", newline="")

    def profile(self, name, server, **overrides):
        settings = {
            "driver": "openai-loop",
            "base_url": server.url,
            "model": f"{name}-model",
            "api_key_env": "FAKE_KEY",
            "billing": "api",
            "retries": 0,
            "retry_base_seconds": 0,
            "request_timeout_seconds": 30,
        }
        settings.update(overrides)
        lines = [f"[profiles.{name}]"]
        lines += [f"{k} = {toml_value(v)}" for k, v in settings.items()]
        return "\n".join(lines) + "\n"

    def configure(self, text):
        path = self.repo / ".claude" / "task-flow.models.toml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")

    def run_round(
        self, round_no=1, profile="fake", worktree=None, env=None, drop=(), cwd=None
    ):
        cmd = [sys.executable, str(SCRIPT)]
        if cwd is None:
            cmd += ["--task-dir", str(self.task)]
        else:
            cmd += ["--task-dir", ".work/t1"]
        cmd += ["--worktree", worktree or str(self.worktree), "--round", str(round_no)]
        if profile:
            cmd += ["--profile", profile]
        environment = {k: v for k, v in os.environ.items() if k not in drop}
        environment.update({"HOME": str(self.home), "USERPROFILE": str(self.home)})
        environment.update({"FAKE_KEY": "fake-key", "NO_PROXY": "127.0.0.1"})
        environment.update(env or {})
        for name in drop:
            environment.pop(name, None)
        done = subprocess.run(
            cmd,
            cwd=cwd or self.repo,
            capture_output=True,
            encoding="utf-8",
            env=environment,
            timeout=180,
            check=False,
        )
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(git(self.worktree, "rev-parse", "HEAD").strip(), self.head)
        return done

    def metrics(self, round_no=1):
        text = (self.task / f"impl-{round_no}.json").read_text(encoding="utf-8")
        return json.loads(text)

    def simple(self, **profile_overrides):
        """One fake profile and a server that makes one edit, then finishes."""
        server = self.server(
            [
                reply(
                    tool_call(
                        "write_file", {"path": "src/new.py", "content": "x = 1\n"}
                    )
                ),
                reply(content="done"),
            ]
        )
        self.configure(self.profile("fake", server, **profile_overrides))
        return server

    # ------------------------------------------------------------------ happy path

    def test_round1_writes_stages_and_logs(self):
        server = self.server(
            [
                reply(
                    tool_call(
                        "write_file", {"path": "src/new.py", "content": "x = 1\nl2\n"}
                    ),
                    tool_call(
                        "edit_file",
                        {"path": "README.md", "old": "hello", "new": "bye"},
                        "c2",
                    ),
                ),
                reply(content="done"),
            ]
        )
        self.configure(self.profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(
            lines[0], r"^ok: 2 files changed \(fake/fake-model, 2 turns, \$[\d.]+\)$"
        )
        self.assertEqual((self.worktree / "src/new.py").read_bytes(), b"x = 1\nl2\n")
        self.assertEqual(
            (self.worktree / "README.md").read_text(encoding="utf-8"), "bye\n"
        )
        staged = git(self.worktree, "diff", "--cached", "--name-only").split()
        self.assertIn("README.md", staged)
        self.assertIn("src/new.py", staged)
        status = (self.task / "status.md").read_text(encoding="utf-8")
        self.assertTrue(status.startswith(STATUS))
        self.assertEqual(
            status[len(STATUS) :],
            "round 1 implementer: README.md (M)\nround 1 implementer: src/new.py (A)\n",
        )
        self.assertEqual(server.requests[0]["path"], "/v1/chat/completions")
        self.assertEqual(server.requests[0]["auth"], "Bearer fake-key")
        self.assertNotIn("temperature", server.requests[0]["body"])

    def test_no_commit_is_made(self):
        # ok, blocked and failed rounds all leave history untouched (run_round also checks HEAD)
        self.simple()
        self.assertEqual(self.run_round().returncode, 0)
        server = self.server([reply(tool_call("blocked", {"reason": "wrong"}))])
        self.configure(self.profile("fake", server))
        self.assertEqual(self.run_round(2 - 1).returncode, 3)
        server = self.server([400])
        self.configure(self.profile("fake", server))
        self.assertEqual(self.run_round().returncode, 1)
        self.assertEqual(git(self.repo, "rev-list", "--all", "--count").strip(), "1")

    def test_system_prompt_is_implementer_body_and_conventions(self):
        server = self.simple()
        self.assertEqual(self.run_round().returncode, 0)
        system = server.requests[0]["body"]["messages"][0]
        self.assertEqual(system["role"], "system")
        content = system["content"]
        agent = (PLUGIN / "agents" / "implementer.md").read_text(encoding="utf-8")
        body = agent.split("\n---\n", 1)[1].strip()
        body = body.replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN.as_posix())
        self.assertIn(body, content)
        self.assertNotIn("model: sonnet", content)
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", content)
        for profile in ("python", "docs"):
            text = (PLUGIN / "skills" / f"profile-{profile}" / "SKILL.md").read_text(
                encoding="utf-8"
            )
            section = re.search(
                r"^## Conventions\s*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
            )
            first = next(ln for ln in section.group(1).splitlines() if ln.strip())
            self.assertIn(first, content)
        self.assertNotIn("profile-config", content)
        self.assertIn("blocked", content)

    def test_first_message_embeds_task_files(self):
        server = self.simple()
        self.assertEqual(self.run_round().returncode, 0)
        user = server.requests[0]["body"]["messages"][1]["content"]
        self.assertEqual(server.requests[0]["body"]["messages"][1]["role"], "user")
        for text in (PLAN, STATUS, CHECKS0):
            self.assertIn(text.strip(), user)
        self.assertIn("task-flow: the shared contract", user)
        self.assertIn(str(self.task / "plan.md"), user)
        self.assertIn(str(self.worktree), user)
        self.assertNotIn("Rename the helper", user)
        names = [t["function"]["name"] for t in server.requests[0]["body"]["tools"]]
        self.assertEqual(
            names,
            [
                "read_file",
                "write_file",
                "edit_file",
                "list_files",
                "grep",
                "run_command",
                "blocked",
            ],
        )

    def test_round2_embeds_review_fix_list(self):
        server = self.server([reply(content="nothing to do")])
        self.configure(self.profile("fake", server))
        self.write_task("review-1.md", REVIEW1)
        done = self.run_round(2)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        user = server.requests[0]["body"]["messages"][1]["content"]
        self.assertIn("Rename the helper in src/app.py.", user)
        self.assertIn("Round: 2", user)
        status = (self.task / "status.md").read_text(encoding="utf-8")
        self.assertTrue(status.endswith("round 2 implementer: no files changed\n"))
        self.assertTrue((self.task / "impl-2.json").is_file())

    # ------------------------------------------------------------------ file tools

    def one_round_of_calls(self, *calls):
        server = self.server([reply(*calls), reply(content="done")])
        self.configure(self.profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return server.tool_results(1)

    def test_path_outside_worktree_denied(self):
        (self.repo / "secret.txt").write_text("TOPSECRET", encoding="utf-8")
        results = self.one_round_of_calls(
            tool_call("read_file", {"path": "../../../secret.txt"}, "a"),
            tool_call("read_file", {"path": str(self.repo / "secret.txt")}, "b"),
            tool_call("write_file", {"path": "../escape.txt", "content": "x"}, "c"),
            tool_call("list_files", {"path": "../../.."}, "d"),
        )
        self.assertEqual(len(results), 4)
        for result in results:
            self.assertTrue(result.startswith("denied"), result)
            self.assertNotIn("TOPSECRET", result)
        self.assertFalse((self.task / "escape.txt").exists())

    def test_writes_into_git_and_work_denied(self):
        results = self.one_round_of_calls(
            tool_call("write_file", {"path": ".git", "content": "x"}, "a"),
            tool_call("write_file", {"path": "sub/.git/config", "content": "x"}, "b"),
            tool_call("write_file", {"path": "sub/.git./config", "content": "x"}, "e"),
            tool_call("write_file", {"path": ".work./x", "content": "x"}, "f"),
            tool_call("write_file", {"path": ".work/t1/plan.md", "content": "x"}, "c"),
            tool_call("edit_file", {"path": ".git", "old": "gitdir", "new": "x"}, "d"),
        )
        for result in results:
            self.assertTrue(result.startswith("denied"), result)
        self.assertFalse((self.worktree / "sub").exists())
        self.assertFalse((self.worktree / ".work").exists())
        self.assertIn("gitdir", (self.worktree / ".git").read_text(encoding="utf-8"))

    def test_edit_file_requires_unique_match(self):
        (self.worktree / "dup.txt").write_text(
            "a\na\nb\n", encoding="utf-8", newline=""
        )
        results = self.one_round_of_calls(
            tool_call("edit_file", {"path": "dup.txt", "old": "a", "new": "z"}, "a"),
            tool_call("edit_file", {"path": "dup.txt", "old": "nope", "new": "z"}, "b"),
            tool_call("edit_file", {"path": "dup.txt", "old": "b", "new": "c"}, "c"),
            tool_call(
                "edit_file", {"path": "missing.txt", "old": "b", "new": "c"}, "d"
            ),
        )
        self.assertIn("2 times", results[0])
        self.assertIn("0 times", results[1])
        self.assertTrue(results[2].startswith("edited"), results[2])
        self.assertTrue(results[3].startswith("error"), results[3])
        self.assertEqual((self.worktree / "dup.txt").read_bytes(), b"a\na\nc\n")

    def test_list_files_and_grep(self):
        results = self.one_round_of_calls(
            tool_call("list_files", {"pattern": "src/*.py"}, "a"),
            tool_call("list_files", {}, "b"),
            tool_call("grep", {"pattern": r"def \w+", "path": "src"}, "c"),
            tool_call("grep", {"pattern": "zzzz"}, "d"),
            tool_call("grep", {"pattern": "("}, "e"),
            tool_call(
                "read_file", {"path": "src/app.py", "offset": 2, "limit": 1}, "f"
            ),
        )
        self.assertEqual(results[0], "src/app.py")
        listed = results[1].splitlines()
        self.assertIn("README.md", listed)
        self.assertFalse([p for p in listed if p.startswith(".git/") or p == ".git"])
        self.assertEqual(results[2], "src/app.py:1:def foo():")
        self.assertEqual(results[3], "(no matches)")
        self.assertIn("bad regular expression", results[4])
        self.assertEqual(results[5], "    return 1\n")

    # ------------------------------------------------------------------ run_command

    def test_commit_and_push_commands_denied(self):
        results = self.one_round_of_calls(
            tool_call("run_command", {"command": "git commit -m x"}, "a"),
            tool_call("run_command", {"command": "git push origin main"}, "b"),
            tool_call("run_command", {"command": "gh pr create --fill"}, "c"),
            tool_call("run_command", {"command": "git status && git push"}, "d"),
        )
        for result in results:
            self.assertTrue(result.startswith("denied"), result)
        self.assertEqual(git(self.repo, "rev-list", "--all", "--count").strip(), "1")

    def test_relative_program_resolves_against_worktree(self):
        tools_dir = self.worktree / "tools"
        tools_dir.mkdir()
        (tools_dir / "hello.py").write_text("print('hi')\n", encoding="utf-8")
        if os.name == "nt":
            # a self-contained system tool, copied so only the worktree has it
            shutil.copy(shutil.which("hostname"), tools_dir / "host.exe")
            command = r"tools\host.exe"
        else:
            target = tools_dir / "py"
            target.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
            target.chmod(0o755)
            command = "tools/py tools/hello.py"
        results = self.one_round_of_calls(
            tool_call("run_command", {"command": command}, "a"),
        )
        self.assertIn("exit code: 0", results[0])

    def test_child_env_is_allowlisted(self):
        code = (
            "import os;"
            "print([k for k in ('MISTRAL_API_KEY','GH_TOKEN','AWS_SECRET_ACCESS_KEY',"
            "'MY_PASSWORD','FAKE_KEY') if k in os.environ]);"
            "print('PATH' in os.environ, os.getcwd())"
        )
        server = self.server(
            [
                reply(tool_call("run_command", {"command": python_command(code)})),
                reply(content="d"),
            ]
        )
        self.configure(self.profile("fake", server))
        secrets = {
            "MISTRAL_API_KEY": "s1",
            "GH_TOKEN": "s2",
            "AWS_SECRET_ACCESS_KEY": "s3",
            "MY_PASSWORD": "s4",
        }
        done = self.run_round(env=secrets)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        result = server.tool_results(1)[0]
        self.assertIn("exit code: 0", result)
        self.assertIn("[]", result)
        self.assertIn("True", result)
        self.assertIn(self.worktree.name, result)
        for secret in secrets.values():
            self.assertNotIn(secret, result)

    def test_command_timeout_and_output_truncated(self):
        sleeper = python_command("import time; time.sleep(60)")
        printer = python_command("print('x' * 50000)")
        server = self.server(
            [
                reply(
                    tool_call("run_command", {"command": sleeper}, "a"),
                    tool_call("run_command", {"command": printer}, "b"),
                ),
                reply(content="d"),
            ]
        )
        self.configure(self.profile("fake", server, command_timeout_seconds=4))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        slow, big = server.tool_results(1)
        self.assertIn("timed out", slow)
        self.assertIn("truncated", big)
        self.assertLess(len(big), 20500)
        self.assertIn("exit code: 0", big)

    # ------------------------------------------------------------------ driver

    def test_retry_on_429_then_success(self):
        server = self.server([(429, {"Retry-After": "0"}), 503, reply(content="done")])
        self.configure(self.profile("fake", server, retries=2))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(server.requests), 3)
        self.assertEqual(self.metrics()["turns"], 1)

    def test_unavailable_profile_falls_back_and_records_chain(self):
        down = self.server([503, 503])
        up = self.server([reply(content="done")])
        text = self.profile(
            "fake", down, retries=1, fallback=["off", "keyless", "backup"]
        )
        text += self.profile("off", down, enabled=False)
        text += self.profile("keyless", down, api_key_env="UNSET_KEY_VAR")
        text += self.profile("backup", up)
        self.configure(text)
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("backup/backup-model", done.stdout)
        self.assertEqual(len(down.requests), 2)
        self.assertIn(
            "previous attempt", up.requests[0]["body"]["messages"][1]["content"]
        )
        metrics = self.metrics()
        self.assertEqual(metrics["profile"], "backup")
        self.assertEqual(metrics["model"], "backup-model")
        chain = [(c["profile"], c["outcome"]) for c in metrics["fallback_chain"]]
        self.assertEqual(
            chain,
            [
                ("fake", "unavailable"),
                ("off", "skipped"),
                ("keyless", "skipped"),
                ("backup", "ok"),
            ],
        )

    def test_non_retryable_4xx_fails_without_fallback(self):
        bad = self.server([400])
        spare = self.server([reply(content="done")])
        text = self.profile("fake", bad, fallback=["backup"]) + self.profile(
            "backup", spare
        )
        self.configure(text)
        done = self.run_round()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith("failed: "))
        self.assertIn("HTTP 400", done.stdout)
        self.assertEqual(len(spare.requests), 0)
        self.assertEqual(self.metrics()["status"], "failed")
        self.assertEqual((self.task / "status.md").read_text(encoding="utf-8"), STATUS)

    def test_blocked_appends_blocked_and_exits_3(self):
        server = self.server([reply(tool_call("blocked", {"reason": "plan is wrong"}))])
        self.configure(self.profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(), "blocked: plan is wrong")
        status = (self.task / "status.md").read_text(encoding="utf-8")
        self.assertEqual(status, STATUS + "BLOCKED: plan is wrong\n")
        self.assertEqual(self.metrics()["status"], "blocked")

    def test_max_turns_exceeded_fails(self):
        read = tool_call("read_file", {"path": "README.md"})
        server = self.server([reply(read), reply(read), reply(read)])
        self.configure(self.profile("fake", server, max_turns=2))
        done = self.run_round()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("max_turns", done.stdout)
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(self.metrics()["status"], "failed")
        self.assertEqual(self.metrics()["turns"], 2)
        self.assertEqual((self.task / "status.md").read_text(encoding="utf-8"), STATUS)

    def test_max_cost_exceeded_fails(self):
        read = tool_call("read_file", {"path": "README.md"})
        usage = {"prompt_tokens": 3, "completion_tokens": 0}
        server = self.server([reply(read, usage=usage)] * 5)
        # 1 000 000 USD per Mtok = 1 USD per token, so each turn costs 3 USD
        self.configure(
            self.profile("fake", server, price_input_per_mtok=1000000, max_cost_usd=5)
        )
        done = self.run_round()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("max_cost_usd", done.stdout)
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(self.metrics()["status"], "failed")
        self.assertEqual((self.task / "status.md").read_text(encoding="utf-8"), STATUS)

    def test_max_cost_is_cumulative_across_fallback(self):
        read = tool_call("read_file", {"path": "README.md"})
        server = self.server(
            [
                reply(read, usage={"prompt_tokens": 4, "completion_tokens": 0}),
                503,
                reply(
                    content="done", usage={"prompt_tokens": 3, "completion_tokens": 0}
                ),
            ]
        )
        pricing = {"price_input_per_mtok": 1000000}
        text = self.profile(
            "fake", server, max_cost_usd=5, fallback=["backup"], **pricing
        )
        text += self.profile("backup", server, **pricing)
        self.configure(text)
        done = self.run_round()
        # 4 USD on fake + 3 USD on backup = 7 > 5, although each alone is under the cap
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("max_cost_usd", done.stdout)
        self.assertEqual(self.metrics()["profile"], "backup")
        self.assertEqual(self.metrics()["cost_usd"], 7.0)

    def test_metrics_json_tokens_and_cost(self):
        server = self.server(
            [
                reply(
                    tool_call("write_file", {"path": "m.txt", "content": "m\n"}),
                    usage={
                        "prompt_tokens": 1000,
                        "completion_tokens": 500,
                        "prompt_tokens_details": {"cached_tokens": 400},
                    },
                ),
                reply(
                    content="done",
                    usage={"prompt_tokens": 100, "completion_tokens": 50},
                ),
            ]
        )
        self.configure(
            self.profile(
                "fake",
                server,
                price_input_per_mtok=2.0,
                price_cached_input_per_mtok=0.5,
                price_output_per_mtok=6.0,
            )
        )
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("$0.0049", done.stdout)
        metrics = self.metrics()
        self.assertEqual(metrics["profile"], "fake")
        self.assertEqual(metrics["model"], "fake-model")
        self.assertEqual(metrics["turns"], 2)
        self.assertEqual(
            metrics["tokens"], {"input": 1100, "cached": 400, "output": 550}
        )
        self.assertAlmostEqual(metrics["cost_usd"], 0.0049, places=7)
        self.assertEqual(metrics["status"], "ok")
        self.assertIn("reason", metrics)
        self.assertGreaterEqual(metrics["duration_seconds"], 0)
        self.assertEqual(metrics["fallback_chain"][0]["profile"], "fake")
        self.assertEqual(metrics["files_changed"], [{"path": "m.txt", "status": "A"}])
        self.assertEqual(
            set(metrics["settings"]), {"temperature", "max_output_tokens", "extra_body"}
        )

    def test_request_includes_temperature_and_extra_body(self):
        server = self.server([reply(content="done")])
        self.configure(
            self.profile(
                "fake",
                server,
                temperature=0.2,
                max_output_tokens=777,
                extra_body={"reasoning_effort": "high"},
            )
        )
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        body = server.requests[0]["body"]
        self.assertEqual(body["temperature"], 0.2)
        self.assertEqual(body["max_tokens"], 777)
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["model"], "fake-model")
        self.assertEqual(
            self.metrics()["settings"],
            {
                "temperature": 0.2,
                "max_output_tokens": 777,
                "extra_body": {"reasoning_effort": "high"},
            },
        )

    # ------------------------------------------------------------------ worktree and refusals

    def test_worktree_none_uses_repo_root(self):
        server = self.server(
            [
                reply(
                    tool_call(
                        "write_file", {"path": "root.txt", "content": "r\n"}, "a"
                    ),
                    tool_call(
                        "write_file", {"path": ".work/t1/plan.md", "content": "x"}, "b"
                    ),
                ),
                reply(content="done"),
            ]
        )
        self.configure(self.profile("fake", server))
        done = self.run_round(worktree="none", cwd=self.repo)  # relative --task-dir
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual((self.repo / "root.txt").read_text(encoding="utf-8"), "r\n")
        self.assertTrue(server.tool_results(1)[1].startswith("denied"))
        self.assertIn("root.txt", git(self.repo, "diff", "--cached", "--name-only"))
        status = (self.task / "status.md").read_text(encoding="utf-8")
        self.assertTrue(status.endswith("round 1 implementer: root.txt (A)\n"))
        self.assertIn(
            f"worktree: {self.repo}".lower(),
            server.requests[0]["body"]["messages"][1]["content"].lower(),
        )

    def refusal(self, label, **kwargs):
        before_task = tree_snapshot(self.task, skip=("worktree",))
        before_wt = git(self.worktree, "status", "--porcelain")
        before_index = git(self.worktree, "diff", "--cached", "--name-only")
        done = self.run_round(**kwargs)
        self.assertEqual(done.returncode, 2, f"{label}: {done.stdout}{done.stderr}")
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, label)
        self.assertTrue(lines[0].startswith("refused: "), label)
        self.assertEqual(
            tree_snapshot(self.task, skip=("worktree",)), before_task, label
        )
        self.assertEqual(git(self.worktree, "status", "--porcelain"), before_wt, label)
        self.assertEqual(
            git(self.worktree, "diff", "--cached", "--name-only"), before_index, label
        )
        self.assertFalse(list(self.task.glob("impl-*.json")), label)

    def test_refusals_write_nothing(self):
        server = self.server([reply(content="done")] * 3)
        self.configure(
            self.profile("fake", server) + self.profile("off", server, enabled=False)
        )

        self.refusal("unknown profile", profile="nope")
        self.refusal("disabled profile", profile="off")
        self.refusal("unset key variable", drop=("FAKE_KEY",))
        self.refusal("no profile and no role", profile=None)
        self.refusal("round 0", round_no=0)
        self.refusal("round 2 without review-1.md", round_no=2)
        self.refusal("bad worktree", worktree=str(self.repo / "missing"))
        self.refusal("not a git work tree", worktree=str(self.home))

        self.write_task("status.md", STATUS.replace("yes (user, 2026-10-31)", "no"))
        self.refusal("unapproved plan")
        self.write_task("status.md", STATUS)

        (self.task / "checks-0.md").unlink()
        self.refusal("missing checks-0.md")
        self.write_task("checks-0.md", CHECKS0)

        (self.task / "plan.md").unlink()
        self.refusal("missing plan.md")
        self.write_task("plan.md", PLAN)

        self.configure("[profiles.fake]\nbogus = 1\n")
        self.refusal("invalid configuration")

        self.assertEqual(len(server.requests), 0)


if __name__ == "__main__":
    unittest.main()
