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
import time
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


def anthropic_text(text):
    return {"type": "text", "text": text}


def anthropic_tool_use(name, args, use_id="t1"):
    return {"type": "tool_use", "id": use_id, "name": name, "input": args}


def anthropic_reply(*blocks, stop_reason=None, usage=None):
    """A Messages-API-shaped response: content blocks plus stop_reason and usage."""
    content = list(blocks) if blocks else [anthropic_text("done")]
    if stop_reason is None:
        stop_reason = (
            "tool_use"
            if any(block.get("type") == "tool_use" for block in content)
            else "end_turn"
        )
    return {
        "content": content,
        "stop_reason": stop_reason,
        "usage": usage
        if usage is not None
        else {"input_tokens": 10, "output_tokens": 5},
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
    """Serves a scripted queue: dict -> 200 JSON, int -> that status, (int, headers),
    ("delay", <seconds>, <item>) -> sleep, then serve <item> as today."""

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
                        "api_key": self.headers.get("x-api-key"),
                        "anthropic_version": self.headers.get("anthropic-version"),
                        "body": body,
                    }
                )
                item = outer.responses.pop(0) if outer.responses else 500
                if isinstance(item, tuple) and len(item) == 3 and item[0] == "delay":
                    time.sleep(item[1])
                    item = item[2]
                headers = {}
                if isinstance(item, dict):
                    status, payload = 200, item
                elif isinstance(item, tuple):
                    (status, headers), payload = item, {"error": "scripted"}
                else:
                    status, payload = item, {"error": "scripted"}
                data = json.dumps(payload).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass  # the client may have timed out and closed the socket

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
class RoundFixture(unittest.TestCase):
    """Temp git repo with a worktree and .work/t1 fixtures, a scripted fake server,
    and the helpers to configure a profile and run one round by subprocess."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()  # expand Windows 8.3 short names
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

    def round_cmd(self, round_no=1, profile="fake", worktree=None, cwd=None, extra=()):
        cmd = [sys.executable, str(SCRIPT)]
        if cwd is None:
            cmd += ["--task-dir", str(self.task)]
        else:
            cmd += ["--task-dir", ".work/t1"]
        cmd += ["--worktree", worktree or str(self.worktree), "--round", str(round_no)]
        if profile:
            cmd += ["--profile", profile]
        cmd += list(extra)
        return cmd

    def round_env(self, env=None, drop=()):
        environment = {k: v for k, v in os.environ.items() if k not in drop}
        environment.update({"HOME": str(self.home), "USERPROFILE": str(self.home)})
        environment.update({"FAKE_KEY": "fake-key", "NO_PROXY": "127.0.0.1"})
        environment.update(env or {})
        for name in drop:
            environment.pop(name, None)
        return environment

    def run_round(
        self,
        round_no=1,
        profile="fake",
        worktree=None,
        env=None,
        drop=(),
        cwd=None,
        extra=(),
    ):
        cmd = self.round_cmd(round_no, profile, worktree, cwd, extra)
        done = subprocess.run(
            cmd,
            cwd=cwd or self.repo,
            capture_output=True,
            encoding="utf-8",
            env=self.round_env(env, drop),
            timeout=180,
            check=False,
        )
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(git(self.worktree, "rev-parse", "HEAD").strip(), self.head)
        return done

    def metrics(self, round_no=1):
        text = (self.task / f"impl-{round_no}.json").read_text(encoding="utf-8")
        return json.loads(text)

    def progress(self, round_no=1):
        path = self.task / f"impl-{round_no}.progress.jsonl"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        return [json.loads(line) for line in text.splitlines() if line.strip()]

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

    def one_round_of_calls(self, *calls):
        server = self.server([reply(*calls), reply(content="done")])
        self.configure(self.profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return server.tool_results(1)

    # ------------------------------------------------------------------ staging

    def staged(self):
        return git(self.worktree, "diff", "--cached", "--name-status").splitlines()

    # ------------------------------------------------------- worktree and refusals

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

    # ------------------------------------------------------- observability

    TURN_LINE = re.compile(
        r"^\[implement\] turn \d+ \(\d+\.\d s, [\d.]+k? in / [\d.]+k? out, "
        r"\$\d+\.\d{4} total\): "
    )


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class ImplementTest(RoundFixture):
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

    @unittest.skipUnless(os.name == "nt", "cmd.exe exists only on Windows")
    def test_cmd_script_runs_through_cmd_exe(self):
        tools_dir = self.worktree / "tools"
        tools_dir.mkdir()
        (tools_dir / "hi.cmd").write_text(
            "@echo off\r\necho hello %~1\r\nexit /b 0\r\n",
            encoding="utf-8",
            newline="",
        )
        results = self.one_round_of_calls(
            tool_call("run_command", {"command": r"tools\hi.cmd world"}, "a"),
        )
        self.assertIn("exit code: 0", results[0])
        self.assertIn("hello world", results[0])

    @unittest.skipUnless(os.name == "nt", "cmd.exe exists only on Windows")
    def test_cmd_script_found_via_pathext(self):
        tools_dir = self.worktree / "tools"
        tools_dir.mkdir()
        (tools_dir / "hi.cmd").write_text(
            "@echo off\r\necho via pathext\r\nexit /b 0\r\n",
            encoding="utf-8",
            newline="",
        )
        results = self.one_round_of_calls(
            tool_call("run_command", {"command": r"tools\hi"}, "a"),
        )
        self.assertIn("exit code: 0", results[0])
        self.assertIn("via pathext", results[0])

    @unittest.skipUnless(os.name == "nt", "cmd.exe exists only on Windows")
    def test_cmd_script_exit_code_propagates(self):
        tools_dir = self.worktree / "tools"
        tools_dir.mkdir()
        (tools_dir / "hi.cmd").write_text(
            "@echo off\r\nexit /b 3\r\n", encoding="utf-8", newline=""
        )
        results = self.one_round_of_calls(
            tool_call("run_command", {"command": r"tools\hi.cmd"}, "a"),
        )
        self.assertIn("exit code: 3", results[0])

    @unittest.skipUnless(os.name == "nt", "cmd.exe exists only on Windows")
    def test_cmd_script_metachar_rejected(self):
        tools_dir = self.worktree / "tools"
        tools_dir.mkdir()
        (tools_dir / "mark.cmd").write_text(
            "@echo off\r\necho x > marker.txt\r\n", encoding="utf-8", newline=""
        )
        results = self.one_round_of_calls(
            tool_call("run_command", {"command": r"tools\mark.cmd a & echo b"}, "a"),
            tool_call("run_command", {"command": r"tools\mark.cmd %PATH%"}, "b"),
            tool_call("run_command", {"command": r"tools\mark.cmd a > out.txt"}, "c"),
            tool_call("run_command", {"command": r"tools\mark.cmd a^b"}, "d"),
            tool_call("run_command", {"command": r"tools\mark.cmd !x!"}, "e"),
        )
        for result, char in zip(results, ("&", "%", ">", "^", "!")):
            self.assertTrue(result.startswith("error:"), result)
            self.assertIn(char, result)
        self.assertFalse((self.worktree / "marker.txt").exists())
        self.assertFalse((self.worktree / "out.txt").exists())

    @unittest.skipUnless(os.name == "nt", "cmd.exe exists only on Windows")
    def test_cmd_script_denial_rules_first(self):
        tools_dir = self.worktree / "tools"
        tools_dir.mkdir()
        (tools_dir / "git.cmd").write_text(
            "@echo off\r\nexit /b 0\r\n", encoding="utf-8", newline=""
        )
        results = self.one_round_of_calls(
            tool_call(
                "run_command", {"command": r"tools\git.cmd push origin main"}, "a"
            ),
        )
        self.assertTrue(results[0].startswith("denied:"), results[0])

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
        # values use "!" and "-", which tempfile names never contain, so they cannot
        # collide with the printed worktree path
        secrets = {
            "MISTRAL_API_KEY": "!secret-mistral-api-key!",
            "GH_TOKEN": "!secret-gh-token!",
            "AWS_SECRET_ACCESS_KEY": "!secret-aws-secret-access-key!",
            "MY_PASSWORD": "!secret-my-password!",
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

    # ------------------------------------------------------------------ post_edit

    def test_post_edit_feedback_returned_to_model(self):
        checker = python_command(
            "import sys; print('checked', sys.argv[1]); sys.exit(1)"
        )
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
        self.configure(self.profile("fake", server, post_edit=[checker + " {path}"]))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        result = server.tool_results(1)[0]
        self.assertIn("wrote 6 characters to src/new.py", result)
        self.assertIn("post_edit:", result)
        self.assertIn("(exit code 1)", result)
        self.assertIn("checked src/new.py", result)
        self.assertIn(
            "src/new.py", git(self.worktree, "diff", "--cached", "--name-only")
        )

    def test_post_edit_runs_after_edit_file(self):
        checker = python_command("import sys; print('edited-check', sys.argv[1])")
        server = self.server(
            [
                reply(
                    tool_call(
                        "edit_file",
                        {"path": "src/app.py", "old": "1", "new": "2"},
                    )
                ),
                reply(content="done"),
            ]
        )
        self.configure(self.profile("fake", server, post_edit=[checker + " {path}"]))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        result = server.tool_results(1)[0]
        self.assertIn("edited src/app.py", result)
        self.assertIn("post_edit:", result)
        self.assertIn("(exit code 0)", result)
        self.assertIn("edited-check src/app.py", result)

    def test_post_edit_output_capped(self):
        printer = python_command("print('\\n'.join(f'line {i}' for i in range(100)))")
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
        self.configure(self.profile("fake", server, post_edit=[printer + " {path}"]))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        result = server.tool_results(1)[0]
        self.assertIn("post_edit:", result)
        output_lines = [ln for ln in result.splitlines() if ln.startswith("line ")]
        self.assertEqual(len(output_lines), 40, result)
        self.assertEqual(output_lines[0], "line 0")
        self.assertEqual(output_lines[-1], "line 39")
        self.assertIn("...[60 more lines]", result)

    def test_post_edit_clean_run_is_silent(self):
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
        self.configure(
            self.profile("fake", server, post_edit=[python_command("pass") + " {path}"])
        )
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(server.tool_results(1)[0], "wrote 6 characters to src/new.py")

    def test_post_edit_denied_and_missing_program_are_feedback(self):
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
        self.configure(
            self.profile(
                "fake",
                server,
                post_edit=[
                    "git commit -m x",
                    "habit-snooze --snooze x {path}",
                    "no-such-program-xyz {path}",
                ],
            )
        )
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        result = server.tool_results(1)[0]
        self.assertIn("post_edit:", result)
        self.assertEqual(result.count("denied:"), 2, result)
        self.assertEqual(result.count("error:"), 1, result)
        self.assertEqual(git(self.repo, "rev-list", "--all", "--count").strip(), "1")

    def test_post_edit_not_run_for_denied_write(self):
        server = self.server(
            [
                reply(tool_call("write_file", {"path": ".git/x", "content": "x"})),
                reply(content="done"),
            ]
        )
        self.configure(
            self.profile(
                "fake", server, post_edit=[python_command("print('must not run')")]
            )
        )
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        result = server.tool_results(1)[0]
        self.assertTrue(result.startswith("denied"), result)
        self.assertNotIn("post_edit:", result)
        self.assertNotIn("must not run", result)

    # ------------------------------------------------------------- prompt_extras

    def test_prompt_extras_appended_before_adapter(self):
        (self.repo / "extras").mkdir()
        (self.repo / "extras" / "one.md").write_text(
            "extra one text\n", encoding="utf-8", newline=""
        )
        absolute = self.home / "absolute-extra.md"
        absolute.write_text("absolute extra text\n", encoding="utf-8", newline="")
        server = self.simple(prompt_extras=["extras/one.md", str(absolute)])
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        content = server.requests[0]["body"]["messages"][0]["content"]
        first = content.index("# Extra instructions: extras/one.md")
        second = content.index(f"# Extra instructions: {absolute}")
        adapter = content.index("## Runner adapter")
        self.assertLess(first, content.index("extra one text"))
        self.assertLess(content.index("extra one text"), second)
        self.assertLess(second, content.index("absolute extra text"))
        self.assertLess(content.index("absolute extra text"), adapter)

    def test_prompt_extras_missing_file_refused(self):
        server = self.server([reply(content="done")])
        self.configure(self.profile("fake", server, prompt_extras=["no-such-extra.md"]))
        before = (self.task / "status.md").read_text(encoding="utf-8")
        done = self.run_round()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith("refused:"), done.stdout)
        self.assertIn("no-such-extra.md", done.stdout)
        self.assertEqual(len(server.requests), 0)
        self.assertFalse(list(self.task.glob("impl-*.json")))
        self.assertEqual((self.task / "status.md").read_text(encoding="utf-8"), before)

    def test_without_new_keys_results_unchanged(self):
        server = self.simple()
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(server.tool_results(1)[0], "wrote 6 characters to src/new.py")
        content = server.requests[0]["body"]["messages"][0]["content"]
        self.assertNotIn("# Extra instructions:", content)

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

    def test_openai_max_completion_tokens_param(self):
        server = self.server([reply(content="done")])
        self.configure(
            self.profile(
                "fake",
                server,
                max_tokens_param="max_completion_tokens",
                max_output_tokens=777,
            )
        )
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        body = server.requests[0]["body"]
        self.assertEqual(body["max_completion_tokens"], 777)
        self.assertNotIn("max_tokens", body)
        self.assertEqual(self.metrics()["settings"]["max_output_tokens"], 777)

    def test_openai_cache_tokens_in_metrics(self):
        server = self.server(
            [
                reply(
                    content="done",
                    usage={
                        "prompt_tokens": 100,
                        "completion_tokens": 5,
                        "prompt_tokens_details": {"cached_tokens": 40},
                    },
                )
            ]
        )
        self.configure(self.profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        metrics = self.metrics()
        self.assertEqual(metrics["tokens"], {"input": 100, "cached": 40, "output": 5})
        self.assertEqual(metrics["cache_tokens"], {"read": 40, "write": 0})
        self.assertEqual(
            set(metrics["settings"]), {"temperature", "max_output_tokens", "extra_body"}
        )

    # ------------------------------------------------------------------ staging

    def test_preexisting_untracked_files_not_staged(self):
        cache = self.worktree / "__pycache__"
        cache.mkdir()
        (cache / "x.pyc").write_bytes(b"pyc")
        (self.worktree / "notes.txt").write_text("n\n", encoding="utf-8", newline="")
        self.simple()  # the model writes src/new.py, then finishes
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.staged(), ["A\tsrc/new.py"])
        self.assertEqual(
            self.metrics()["files_changed"], [{"path": "src/new.py", "status": "A"}]
        )
        others = git(self.worktree, "ls-files", "--others").splitlines()
        self.assertEqual(sorted(others), ["__pycache__/x.pyc", "notes.txt"])

    def test_command_changes_staged_except_artifacts(self):
        code = (
            "from pathlib import Path;"
            "p = Path('README.md');"
            "p.write_text(p.read_text(encoding='utf-8') + 'more\\n', encoding='utf-8');"
            "Path('gen.py').write_text('g = 1\\n', encoding='utf-8');"
            "Path('__pycache__').mkdir(exist_ok=True);"
            "Path('__pycache__/y.pyc').write_bytes(b'y');"
            "Path('pkg/__pycache__').mkdir(parents=True, exist_ok=True);"
            "Path('pkg/__pycache__/z.cpython-314.pyc').write_bytes(b'z');"
            "Path('.pytest_cache').mkdir(exist_ok=True);"
            "Path('.pytest_cache/v').write_text('v', encoding='utf-8');"
            "Path('.DS_Store').write_bytes(b'ds')"
        )
        server = self.server(
            [
                reply(tool_call("run_command", {"command": python_command(code)})),
                reply(content="done"),
            ]
        )
        self.configure(self.profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(self.staged()), ["A\tgen.py", "M\tREADME.md"])
        files = self.metrics()["files_changed"]
        self.assertEqual(
            sorted(files, key=lambda f: f["path"]),
            [{"path": "README.md", "status": "M"}, {"path": "gen.py", "status": "A"}],
        )
        others = git(self.worktree, "ls-files", "--others").splitlines()
        for artifact in (
            "__pycache__/y.pyc",
            "pkg/__pycache__/z.cpython-314.pyc",
            ".pytest_cache/v",
            ".DS_Store",
        ):
            self.assertIn(artifact, others)

    def test_write_tool_paths_staged_artifacts_never(self):
        (self.worktree / "notes.txt").write_text("n\n", encoding="utf-8", newline="")
        server = self.server(
            [
                reply(
                    tool_call(
                        "write_file", {"path": "notes.txt", "content": "edited\n"}
                    ),
                    tool_call(
                        "write_file",
                        {"path": "build/__pycache__/w.pyc", "content": "w"},
                        "c2",
                    ),
                ),
                reply(content="done"),
            ]
        )
        self.configure(self.profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.staged(), ["A\tnotes.txt"])
        self.assertEqual(
            self.metrics()["files_changed"], [{"path": "notes.txt", "status": "A"}]
        )
        others = git(self.worktree, "ls-files", "--others").splitlines()
        self.assertIn("build/__pycache__/w.pyc", others)

    def test_preexisting_tracked_change_staged_but_not_reported(self):
        (self.worktree / "README.md").write_text(
            "changed before the round\n", encoding="utf-8", newline=""
        )
        self.simple()  # the model writes src/new.py, then finishes
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertRegex(
            done.stdout.splitlines()[0],
            r"^ok: 1 files changed \(fake/fake-model, 2 turns, \$[\d.]+\)$",
        )
        self.assertEqual(sorted(self.staged()), ["A\tsrc/new.py", "M\tREADME.md"])
        self.assertEqual(
            self.metrics()["files_changed"], [{"path": "src/new.py", "status": "A"}]
        )
        status = (self.task / "status.md").read_text(encoding="utf-8")
        self.assertTrue(status.endswith("round 1 implementer: src/new.py (A)\n"))

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
        self.refusal("negative heartbeat", extra=("--heartbeat", "-1"))

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

    # ------------------------------------------------------- observability

    def test_turn_line_shows_seconds_tokens_and_cost(self):
        server = self.server(
            [
                reply(
                    tool_call(
                        "write_file", {"path": "src/new.py", "content": "x = 1\n"}
                    ),
                    tool_call(
                        "run_command", {"command": python_command("print('hi')")}, "c2"
                    ),
                    usage={"prompt_tokens": 1200, "completion_tokens": 34},
                ),
                reply(
                    content="done", usage={"prompt_tokens": 10, "completion_tokens": 5}
                ),
            ]
        )
        self.configure(self.profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [
            line for line in done.stderr.splitlines() if self.TURN_LINE.match(line)
        ]
        self.assertEqual(len(lines), 3, done.stderr)
        self.assertIn("1.2k in", lines[0])
        self.assertIn("34 out", lines[0])
        self.assertIn("write_file -> ", lines[0])
        self.assertRegex(lines[1], r"run_command \[\d+\.\d s\] -> ")
        self.assertIn("10 in", lines[2])
        self.assertTrue(lines[2].endswith(": final message"), lines[2])

    def test_heartbeat_while_waiting(self):
        server = self.server(
            [
                (
                    "delay",
                    2.5,
                    reply(
                        tool_call(
                            "write_file", {"path": "src/new.py", "content": "x = 1\n"}
                        )
                    ),
                ),
                reply(content="done"),
            ]
        )
        self.configure(self.profile("fake", server))
        done = self.run_round(extra=("--heartbeat", "1"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stderr.splitlines()
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith("[implement] ") for line in lines), lines)
        heartbeat = re.compile(r"^\[implement\] waiting for fake \.\.\. \d+ s$")
        beats = [i for i, line in enumerate(lines) if heartbeat.match(line)]
        turns = [i for i, line in enumerate(lines) if self.TURN_LINE.match(line)]
        self.assertGreaterEqual(len(beats), 1)
        self.assertTrue(turns)
        self.assertLess(min(beats), min(turns))
        self.assertLess(max(beats), max(turns))

    def test_heartbeat_during_command(self):
        server = self.server(
            [
                reply(
                    tool_call(
                        "run_command",
                        {"command": python_command("import time; time.sleep(2.5)")},
                    )
                ),
                reply(content="done"),
            ]
        )
        self.configure(self.profile("fake", server))
        done = self.run_round(extra=("--heartbeat", "1"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        running = re.compile(r"^\[implement\] running .+ \.\.\. \d+ s$")
        self.assertTrue(
            any(running.match(line) for line in done.stderr.splitlines()),
            done.stderr,
        )

    def test_retry_429_recorded_in_chain(self):
        server = self.server([(429, {"Retry-After": "0"}), reply(content="done")])
        self.configure(self.profile("fake", server, retries=1))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        chain = self.metrics()["fallback_chain"]
        self.assertEqual(len(chain), 1)
        retries = chain[0]["retries"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["reason"], "HTTP 429")
        self.assertEqual(retries[0]["turn"], 1)
        self.assertEqual(self.progress()[0]["retries"], 1)

    def test_timeout_retry_recorded(self):
        server = self.server(
            [("delay", 3.0, reply(content="late")), reply(content="done")]
        )
        self.configure(
            self.profile(
                "fake",
                server,
                retries=1,
                request_timeout_seconds=1,
                retry_base_seconds=0,
            )
        )
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        metrics = self.metrics()
        retries = metrics["fallback_chain"][0]["retries"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["reason"], "timeout")
        self.assertEqual(metrics["slowest_request"]["outcome"], "timeout")
        self.assertGreaterEqual(metrics["slowest_request"]["seconds"], 1.0)

    def test_timing_metrics(self):
        command_text = python_command("import time; time.sleep(1)")
        server = self.server(
            [
                (
                    "delay",
                    1.0,
                    reply(tool_call("run_command", {"command": command_text})),
                ),
                reply(content="done"),
            ]
        )
        self.configure(self.profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        metrics = self.metrics()
        for key in (
            "profile",
            "model",
            "turns",
            "tokens",
            "cost_usd",
            "duration_seconds",
            "status",
            "reason",
            "fallback_chain",
            "settings",
            "files_changed",
            "model_seconds",
            "retry_wait_seconds",
            "command_seconds",
            "slowest_request",
            "slowest_command",
        ):
            self.assertIn(key, metrics)
        self.assertGreaterEqual(metrics["model_seconds"], 1.0)
        self.assertGreaterEqual(metrics["command_seconds"], 1.0)
        self.assertEqual(metrics["slowest_request"]["turn"], 1)
        self.assertEqual(metrics["slowest_request"]["profile"], "fake")
        self.assertEqual(metrics["slowest_request"]["outcome"], "ok")
        self.assertIn("time.sleep", metrics["slowest_command"]["command"])
        self.assertIs(metrics["slowest_command"]["timed_out"], False)
        self.assertEqual(metrics["fallback_chain"][0]["retries"], [])

    def test_progress_file_one_line_per_turn(self):
        read = tool_call("read_file", {"path": "README.md"})
        server = self.server([reply(read), reply(read), reply(content="done")])
        self.configure(self.profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        records = self.progress()
        self.assertEqual([r["turn"] for r in records], [1, 2, 3])
        self.assertEqual(self.metrics()["turns"], len(records))
        keys = {
            "turn",
            "profile",
            "tools",
            "request_seconds",
            "retries",
            "command_seconds",
            "tokens",
            "cost_usd",
            "elapsed_seconds",
        }
        for record in records:
            self.assertEqual(set(record), keys)
            self.assertEqual(set(record["tokens"]), {"input", "cached", "output"})
            self.assertEqual(record["profile"], "fake")
        self.assertEqual(records[0]["tools"], ["read_file"])
        self.assertEqual(records[2]["tools"], [])
        text = (self.task / "impl-1.progress.jsonl").read_text(encoding="utf-8")
        lines = text.splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertNotIn(", ", line)
            self.assertNotIn(": ", line)

    def test_progress_survives_failure(self):
        server = self.server(
            [
                reply(
                    tool_call("write_file", {"path": "a.txt", "content": "a\n"}, "a")
                ),
                reply(
                    tool_call("write_file", {"path": "b.txt", "content": "b\n"}, "b")
                ),
                400,
            ]
        )
        self.configure(self.profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith("failed: "))
        records = self.progress()
        self.assertEqual([r["turn"] for r in records], [1, 2])
        self.assertEqual(self.metrics()["turns"], 2)
        self.assertEqual(self.metrics()["status"], "failed")

    def test_progress_survives_kill(self):
        server = self.server(
            [
                reply(tool_call("read_file", {"path": "README.md"}, "a")),
                reply(tool_call("read_file", {"path": "src/app.py"}, "b")),
                ("delay", 30.0, reply(content="done")),
            ]
        )
        self.configure(self.profile("fake", server))
        proc = subprocess.Popen(
            self.round_cmd(extra=("--heartbeat", "5")),
            cwd=self.repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.round_env(),
        )
        path = self.task / "impl-1.progress.jsonl"
        deadline = time.time() + 30
        while time.time() < deadline:
            if (
                path.is_file()
                and len(path.read_text(encoding="utf-8").splitlines()) >= 2
            ):
                break
            time.sleep(0.1)
        proc.kill()
        proc.communicate(timeout=30)
        self.assertFalse((self.task / "impl-1.json").exists())
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(len(lines), 2)
        records = [json.loads(line) for line in lines]
        self.assertEqual([r["turn"] for r in records], [1, 2])

    def test_verbose_prints_model_text(self):
        server = self.server(
            [
                reply(
                    tool_call(
                        "write_file", {"path": "src/new.py", "content": "x = 1\n"}
                    ),
                    content="I will write src/new.py now.",
                ),
                reply(content="finished writing"),
            ]
        )
        self.configure(self.profile("fake", server))
        done = self.run_round(extra=("--verbose",))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("turn 1 text: I will write src/new.py now.", done.stderr)
        self.assertIn("turn 2 text: finished writing", done.stderr)
        quiet = self.server([reply(content="quiet finish")])
        self.configure(self.profile("fake", quiet))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("text:", done.stderr)

    def test_stdout_line_unchanged_with_new_options(self):
        self.simple()
        done = self.run_round(extra=("--verbose", "--heartbeat", "1"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(
            lines[0], r"^ok: 1 files changed \(fake/fake-model, 2 turns, \$[\d.]+\)$"
        )


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class AnthropicLoopTest(RoundFixture):
    """The anthropic-loop driver (native Messages API) end to end against the fake
    server: requests, cache-aware cost, fallback, refusal and blocked."""

    def anthropic_profile(self, name, server, **overrides):
        return self.profile(name, server, driver="anthropic-loop", **overrides)

    def test_anthropic_round_writes_stages_and_logs(self):
        server = self.server(
            [
                anthropic_reply(
                    anthropic_tool_use(
                        "write_file", {"path": "src/new.py", "content": "x = 1\n"}
                    )
                ),
                anthropic_reply(anthropic_text("done"), stop_reason="end_turn"),
            ]
        )
        self.configure(self.anthropic_profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual((self.worktree / "src/new.py").read_bytes(), b"x = 1\n")
        staged = git(self.worktree, "diff", "--cached", "--name-only").split()
        self.assertIn("src/new.py", staged)
        status = (self.task / "status.md").read_text(encoding="utf-8")
        self.assertTrue(status.endswith("round 1 implementer: src/new.py (A)\n"))
        # requests went to /v1/messages with the key in x-api-key, no Authorization
        self.assertEqual(server.requests[0]["path"], "/v1/messages")
        self.assertEqual(server.requests[0]["api_key"], "fake-key")
        self.assertEqual(server.requests[0]["anthropic_version"], "2026-10-21")
        self.assertIsNone(server.requests[0]["auth"])
        body = server.requests[0]["body"]
        self.assertEqual(body["tool_choice"], {"type": "auto"})
        self.assertEqual(body["max_tokens"], 16000)
        self.assertIn("cache_control", body["system"][0])
        self.assertNotIn("temperature", body)
        self.assertEqual(
            [tool["name"] for tool in body["tools"]],
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
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(body["messages"][0]["content"][0]["type"], "text")
        # turn 1's tool result went back as ONE user message of tool_result blocks,
        # whose last block carries the moving cache breakpoint in the sent body
        second = server.requests[1]["body"]["messages"]
        self.assertEqual(second[1]["role"], "assistant")
        self.assertEqual(second[1]["content"][0]["type"], "tool_use")
        self.assertEqual(len(second[2]["content"]), 1)
        tool_block = second[2]["content"][0]
        self.assertEqual(tool_block["type"], "tool_result")
        self.assertEqual(tool_block["tool_use_id"], "t1")
        self.assertEqual(tool_block["content"], "wrote 6 characters to src/new.py")
        self.assertIn("cache_control", tool_block)

    def test_anthropic_cost_with_cache_reads_and_writes(self):
        usage1 = {
            "input_tokens": 100,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 0,
            "output_tokens": 100,
        }
        usage2 = {
            "input_tokens": 50,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 1000,
            "output_tokens": 20,
        }
        server = self.server(
            [
                anthropic_reply(
                    anthropic_tool_use(
                        "write_file", {"path": "m.txt", "content": "m\n"}
                    ),
                    usage=usage1,
                ),
                anthropic_reply(
                    anthropic_text("done"), stop_reason="end_turn", usage=usage2
                ),
            ]
        )
        self.configure(
            self.anthropic_profile(
                "fake",
                server,
                price_input_per_mtok=2.0,
                price_cached_input_per_mtok=0.2,
                price_cache_write_per_mtok=2.5,
                price_output_per_mtok=10.0,
            )
        )
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("$0.0047", done.stdout)
        metrics = self.metrics()
        self.assertEqual(
            metrics["tokens"], {"input": 2350, "cached": 1000, "output": 120}
        )
        self.assertEqual(metrics["cache_tokens"], {"read": 1000, "write": 1200})
        self.assertAlmostEqual(metrics["cost_usd"], 0.0047, places=7)
        for record in self.progress():
            self.assertEqual(set(record["tokens"]), {"input", "cached", "output"})

    def test_cache_write_price_defaults_to_input_price(self):
        usage = {
            "input_tokens": 0,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 0,
            "output_tokens": 0,
        }
        server = self.server(
            [
                anthropic_reply(
                    anthropic_text("done"), stop_reason="end_turn", usage=usage
                )
            ]
        )
        # no price_cache_write_per_mtok: cache writes are priced at price_input_per_mtok
        self.configure(self.anthropic_profile("fake", server, price_input_per_mtok=3.0))
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("$0.0030", done.stdout)
        self.assertAlmostEqual(self.metrics()["cost_usd"], 0.003, places=9)
        self.assertEqual(self.metrics()["cache_tokens"], {"read": 0, "write": 1000})

    def test_fallback_openai_to_anthropic(self):
        down = self.server([503, 503])
        up = self.server(
            [
                anthropic_reply(
                    anthropic_tool_use(
                        "write_file", {"path": "src/new.py", "content": "x = 1\n"}
                    )
                ),
                anthropic_reply(anthropic_text("done"), stop_reason="end_turn"),
            ]
        )
        text = self.profile("fake", down, retries=1, fallback=["claude"])
        text += self.anthropic_profile("claude", up)
        self.configure(text)
        done = self.run_round()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(down.requests), 2)
        chain = [
            (entry["profile"], entry["outcome"])
            for entry in self.metrics()["fallback_chain"]
        ]
        self.assertEqual(chain, [("fake", "unavailable"), ("claude", "ok")])
        self.assertEqual(up.requests[0]["path"], "/v1/messages")
        self.assertEqual(up.requests[0]["api_key"], "fake-key")
        # the "previous attempt" note is in the first user message of the Messages call
        first_user = up.requests[0]["body"]["messages"][0]
        self.assertIn("previous attempt", first_user["content"][0]["text"])

    def test_anthropic_refusal_fails(self):
        server = self.server(
            [anthropic_reply(anthropic_text("I cannot help"), stop_reason="refusal")]
        )
        self.configure(self.anthropic_profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith("failed: "), done.stdout)
        self.assertIn("refused", done.stdout)
        self.assertEqual(self.metrics()["status"], "failed")
        self.assertEqual((self.task / "status.md").read_text(encoding="utf-8"), STATUS)

    def test_anthropic_blocked_exits_3(self):
        server = self.server(
            [
                anthropic_reply(
                    anthropic_tool_use("blocked", {"reason": "plan is wrong"})
                )
            ]
        )
        self.configure(self.anthropic_profile("fake", server))
        done = self.run_round()
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(), "blocked: plan is wrong")
        status = (self.task / "status.md").read_text(encoding="utf-8")
        self.assertEqual(status, STATUS + "BLOCKED: plan is wrong\n")
        self.assertEqual(self.metrics()["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
