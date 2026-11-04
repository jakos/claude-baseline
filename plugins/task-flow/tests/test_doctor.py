# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/doctor.py: temp dirs, temp git repos, one local HTTP server.

Run: uv run --script plugins/task-flow/tests/test_doctor.py
"""

import ast
import contextlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "doctor.py"
SETUP_PAGE = SCRIPTS.parents[2] / "docs" / "task-flow" / "setup.md"
sys.path.insert(0, str(SCRIPTS))
import doctor
import models

HAS_GIT = shutil.which("git") is not None
SECRET = "sk-SECRET-123"

MISTRAL_TOML = """
[profiles.mistral]
driver = "openai-loop"
base_url = "https://api.mistral.ai/v1"
model = "mistral-large"
api_key_env = "MISTRAL_API_KEY"
billing = "api"
max_cost_usd = 1.5
price_input_per_mtok = 0.5
fallback = ["backup"]

[profiles.backup]
driver = "openai-loop"
base_url = "https://api.mistral.ai/v1"
model = "mistral-small"
api_key_env = "BACKUP_API_KEY"
billing = "api"

[roles]
implementer = "mistral"
"""

LOCAL_TOML = """
[profiles.local]
driver = "openai-loop"
base_url = "http://localhost:8080/v1"
model = "local-model"
billing = "local"

[roles]
implementer = "local"
"""

ENABLED_TOML = MISTRAL_TOML.replace(
    'billing = "api"\nmax_cost_usd',
    'billing = "api"\nenabled = false\nmax_cost_usd',
).replace(
    'api_key_env = "BACKUP_API_KEY"\nbilling = "api"',
    'api_key_env = "BACKUP_API_KEY"\nbilling = "api"\nenabled = false',
)


def make_handler(hits, status=200, ids=(), echo_auth_in_body=False):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "x_api_key": self.headers.get("x-api-key"),
                    "anthropic_version": self.headers.get("anthropic-version"),
                }
            )
            if status == 200:
                body = json.dumps({"data": [{"id": i} for i in ids]}).encode("utf-8")
            elif echo_auth_in_body:
                body = json.dumps(
                    {"error": "bad key " + str(self.headers.get("Authorization"))}
                ).encode("utf-8")
            else:
                body = b'{"error": "nope"}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


class FakeProvider:
    """A ThreadingHTTPServer on 127.0.0.1:0 in a thread; records every hit."""

    def __init__(self, handler):
        self.hits: list[dict] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "home"
        self.repo = Path(self._tmp.name) / "repo"
        self.home.mkdir()
        self.repo.mkdir()
        for patcher in (
            mock.patch.object(Path, "home", lambda: self.home),
            mock.patch.object(
                models, "user_file", lambda: self.home / ".claude/task-flow/models.toml"
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        env_patcher = mock.patch.dict(os.environ)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop("ANTHROPIC_API_KEY", None)

    # --- helpers -------------------------------------------------------------------------

    def git(self, *args):
        subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True
        )

    def init_repo(self):
        self.git("init", "-q")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "commit.gpgsign", "false")

    def commit(self, *names):
        if names:
            self.git("add", *names)
        self.git("commit", "-q", "--allow-empty", "-m", "c")

    def write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def project_models(self, text):
        return self.write(".claude/task-flow.models.toml", text)

    def load_profiles(self, text):
        self.project_models(text)
        return models.load(self.repo)

    def write_full_settings(self):
        rules = {
            "permissions": {
                "allow": list(doctor.REQUIRED_ALLOW),
                "ask": ["Bash(git push:*)"],
            }
        }
        self.write(".claude/settings.json", json.dumps(rules))

    def online_toml(self, url, model="mistral-large", driver="openai-loop"):
        return (
            f'[profiles.mistral]\ndriver = "{driver}"\nbase_url = "{url}"\n'
            f'model = "{model}"\napi_key_env = "MISTRAL_API_KEY"\nbilling = "api"\n'
            f"max_cost_usd = 1.5\nprice_input_per_mtok = 0.5\n\n"
            f'[roles]\nimplementer = "mistral"\n'
        )

    def run_doctor(self, argv=()):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = doctor.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def row(self, out, name):
        for line in out.splitlines():
            if line.startswith("| ") and f"| {name}" in line:
                return line
        self.fail(f"no row {name!r} in:\n{out}")

    def row_number(self, out, name):
        return self.row(out, name).split("|")[1].strip()


# --- AC1: header and imports --------------------------------------------------------------


class ScriptTest(DoctorTest):
    def test_script_header_and_stdlib_only(self):
        text = SCRIPT.read_text(encoding="utf-8")
        block = text.split("# /// script", 1)[1].split("# ///", 1)[0]
        self.assertIn('# requires-python = ">=3.14"', block)
        self.assertIn("# dependencies = []", block)
        imported = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        self.assertIn("models", imported)
        for module in imported:
            self.assertTrue(
                module in sys.stdlib_module_names or module == "models",
                f"non-stdlib import: {module}",
            )

    def test_usage_error_exits_2(self):
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            doctor.main(["--bogus"])
        self.assertEqual(ctx.exception.code, 2)


# --- AC2, AC3: the run itself ----------------------------------------------------------------


class RunTest(DoctorTest):
    def test_all_pass_exit_zero(self):
        self.init_repo()
        self.write_full_settings()
        self.commit(".claude/settings.json")
        code, out, err = self.run_doctor(["--project", str(self.repo)])
        self.assertEqual(code, 0, out + err)
        self.assertIn("# task-flow doctor", out)
        self.assertIn("| # | Check | Result | Detail |", out)
        self.assertIn("## Fixes\nnone", out)
        self.assertRegex(out, r"## Summary\n\d+ PASS, 0 WARN, 0 FAIL")

    def test_exit_codes_and_fix_lines(self):
        # not a repository -> a FAIL row, exit 1, numbered fix lines for FAIL and WARN
        code, out, _ = self.run_doctor(["--project", str(self.repo)])
        self.assertEqual(code, 1)
        self.assertNotIn("## Fixes\nnone", out)
        fail_number = self.row_number(out, "repository")
        self.assertIn(f"{fail_number}. repository: fix: git init", out)
        warn_number = self.row_number(out, "permission rules")
        self.assertIn(f"{warn_number}. permission rules: fix:", out)
        # WARN alone keeps the exit code at 0
        self.init_repo()
        self.commit()
        code, out, _ = self.run_doctor(["--project", str(self.repo)])
        self.assertEqual(code, 0)
        self.assertIn("## Fixes\n", out)
        self.assertIn("0 FAIL", out)
        self.assertIn(
            f"{self.row_number(out, 'permission rules')}. permission rules:", out
        )


# --- AC4: uv and Python -----------------------------------------------------------------------


class ToolTest(DoctorTest):
    def test_uv_missing_fails(self):
        rows = doctor.check_uv(lambda name: None, doctor.make_run())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].result, "FAIL")
        fixes = " ".join(rows[0].fixes)
        self.assertIn("astral.sh/uv/install.ps1", fixes)
        self.assertIn("astral.sh/uv/install.sh", fixes)
        self.assertIn("PowerShell", fixes)
        self.assertIn("POSIX", fixes)
        self.assertIn("restart Claude Code", fixes)

    def test_python_requirement_parsed_from_headers(self):
        specs = [spec for spec, _source in doctor.script_requirements(SCRIPTS)]
        self.assertIn(">=3.14", specs)
        self.assertGreaterEqual(len(specs), 2)
        self.assertEqual(
            doctor.check_python(SCRIPTS, sys.version_info)[0].result, "PASS"
        )
        self.assertEqual(doctor.check_python(SCRIPTS, (3, 13, 0))[0].result, "FAIL")
        # not hard-coded: a scripts dir with a higher requirement fails on this interpreter
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp)
            (scripts / "x.py").write_text(
                '# /// script\n# requires-python = ">=99.0"\n# dependencies = []\n# ///\n',
                encoding="utf-8",
            )
            self.assertEqual(
                doctor.check_python(scripts, sys.version_info)[0].result, "FAIL"
            )


# --- AC5: the repository ------------------------------------------------------------------------


@unittest.skipUnless(HAS_GIT, "git is not on PATH")
class RepositoryTest(DoctorTest):
    def test_not_a_repo(self):
        code, out, _ = self.run_doctor(["--project", str(self.repo)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "repository"))
        self.assertIn("git init", out)
        # the commit and working-tree rows are skipped
        self.assertNotIn("| commit |", out)
        self.assertNotIn("| working tree |", out)

    def test_no_commit_fails(self):
        self.init_repo()
        code, out, _ = self.run_doctor(["--project", str(self.repo)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "commit"))
        self.assertIn('git commit --allow-empty -m "chore: initial commit"', out)

    def test_dirty_tree_warns_work_ignored(self):
        self.init_repo()
        self.commit()
        self.write("dirty.txt", "x\n")
        code, out, _ = self.run_doctor(["--project", str(self.repo)])
        self.assertEqual(code, 0)
        row = self.row(out, "working tree")
        self.assertIn("WARN", row)
        self.assertIn("1 uncommitted change", row)
        # a change only under .work/ is ignored
        self.commit("dirty.txt")
        self.write(".work/task-x/status.md", "x\n")
        code, out, _ = self.run_doctor(["--project", str(self.repo)])
        self.assertEqual(code, 0)
        self.assertIn("PASS", self.row(out, "working tree"))


# --- AC6: plugin source ----------------------------------------------------------------------------


class PluginSourceTest(DoctorTest):
    def test_plugin_source_working_tree_vs_installed(self):
        self.write(
            "plugins/task-flow/.claude-plugin/plugin.json",
            json.dumps({"name": "task-flow", "version": "9.9.9"}),
        )
        tracked = ["plugins/task-flow/.claude-plugin/plugin.json"]
        installed = Path(self._tmp.name) / "installed"
        (installed / ".claude-plugin").mkdir(parents=True)
        (installed / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "task-flow", "version": "0.11.0"}), encoding="utf-8"
        )
        row = doctor.check_plugin_source(self.repo, installed, tracked)
        self.assertEqual(row.result, "WARN")
        self.assertIn("installed task-flow", row.detail)
        fixes = " ".join(row.fixes)
        self.assertIn("$env:CLAUDE_CODE_PLUGIN_DIRS", fixes)
        self.assertIn(";", fixes)  # PowerShell separator
        self.assertIn("export CLAUDE_CODE_PLUGIN_DIRS", fixes)
        self.assertIn(":", fixes)  # POSIX separator
        self.assertIn("restart Claude Code", fixes)
        # the same directory -> PASS
        row = doctor.check_plugin_source(
            self.repo, self.repo / "plugins" / "task-flow", tracked
        )
        self.assertEqual(row.result, "PASS")
        self.assertIn("9.9.9", row.detail)


# --- AC7, AC8: settings and permission rules ---------------------------------------------------------


class SettingsTest(DoctorTest):
    def test_settings_invalid_json_fails(self):
        self.write(".claude/settings.json", "{ not json")
        rows = doctor.check_settings(doctor.settings_paths(self.repo, self.home))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].result, "FAIL")
        code, out, _ = self.run_doctor(["--project", str(self.repo)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "settings"))

    def test_permission_rules_warn(self):
        paths = doctor.settings_paths(self.repo, self.home)
        row = doctor.check_permissions(paths)
        self.assertEqual(row.result, "WARN")
        for rule in doctor.REQUIRED_ALLOW:
            self.assertIn(rule, row.detail)
        self.assertIn("permissions.allow", row.fixes[0])
        self.assertIn(json.dumps(doctor.REQUIRED_ALLOW), row.fixes[0])
        # a subset present -> only the rest are missing
        self.write(
            ".claude/settings.json",
            json.dumps(
                {"permissions": {"allow": ["Bash(uv run:*)", "Bash(git status:*)"]}}
            ),
        )
        row = doctor.check_permissions(paths)
        self.assertEqual(row.result, "WARN")
        self.assertIn("Bash(git diff:*)", row.detail)
        self.assertNotIn("Bash(uv run:*)", row.detail)
        # a bare Bash allow rule satisfies all of them
        self.write(
            ".claude/settings.json", json.dumps({"permissions": {"allow": ["Bash"]}})
        )
        row = doctor.check_permissions(paths)
        self.assertEqual(row.result, "PASS")
        self.assertEqual(row.fixes, [])

    def test_required_allow_matches_readme(self):
        text = SETUP_PAGE.read_text(encoding="utf-8")
        section = text.split("## Permissions", 1)[1]
        block = re.search(r"```json\n(.*?)```", section, re.DOTALL).group(1)
        allow = json.loads(block)["permissions"]["allow"]
        self.assertEqual(doctor.REQUIRED_ALLOW, allow)


# --- AC9: ANTHROPIC_API_KEY ----------------------------------------------------------------------


class AnthropicKeyTest(DoctorTest):
    def test_anthropic_api_key_warns(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-abcdef"
        row = doctor.check_anthropic_key(dict(os.environ))
        self.assertEqual(row.result, "WARN")
        self.assertIn("token", row.detail)
        self.assertIn(f"{len('sk-test-abcdef')} chars", row.detail)
        self.assertNotIn("sk-test-abcdef", row.detail)
        fixes = " ".join(row.fixes)
        self.assertIn("Remove-Item Env:ANTHROPIC_API_KEY", fixes)
        self.assertIn("unset ANTHROPIC_API_KEY", fixes)
        self.assertEqual(doctor.check_anthropic_key({}).result, "PASS")


# --- AC10: models.toml ----------------------------------------------------------------------------


class ModelsTest(DoctorTest):
    def test_models_invalid_fails(self):
        path = self.project_models(
            '[profiles.mistral]\ndriver = "openai-loop"\n'
            'base_url = "https://api.mistral.ai/v1"\nmodel = "m"\nbilling = "api"\n'
            "max_turn = 3\n"
        )
        check, profiles, roles = doctor.check_models(self.repo)
        self.assertEqual(check.result, "FAIL")
        self.assertIn(str(path), check.detail)
        self.assertIn("profiles.mistral.max_turn", check.detail)
        self.assertIsNone(profiles)
        self.assertIsNone(roles)
        # [roles] implementer naming a missing profile
        self.project_models('[roles]\nimplementer = "nope"\n')
        check, _profiles, _roles = doctor.check_models(self.repo)
        self.assertEqual(check.result, "FAIL")
        self.assertIn("roles.implementer", check.detail)
        self.assertIn("unknown profile", check.detail)
        # main: exit 1 and no roles / implementer rows
        code, out, _ = self.run_doctor(["--project", str(self.repo)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "models.toml"))
        self.assertNotIn("| roles |", out)

    def test_models_absent_passes(self):
        check, profiles, roles = doctor.check_models(self.repo)
        self.assertEqual(check.result, "PASS")
        self.assertIn("none: every role is Claude", check.detail)
        self.assertEqual((profiles, roles), ({}, {}))


# --- AC11: profile key and enabled checks -----------------------------------------------------------


class ProfileTest(DoctorTest):
    def rows_for(self, profiles, roles, env, lookup=lambda name: None):
        rows, targets = doctor.check_implementer_profiles(profiles, roles, env, lookup)
        return {row.name: row for row in rows}, targets

    def test_profile_key_checks(self):
        profiles, roles = self.load_profiles(MISTRAL_TOML)
        rows, targets = self.rows_for(profiles, roles, {})
        primary = rows["profile mistral (primary): key"]
        fallback = rows["profile backup (fallback): key"]
        self.assertEqual(primary.result, "FAIL")
        self.assertIn("MISTRAL_API_KEY", primary.detail)
        fixes = " ".join(primary.fixes)
        self.assertIn("PowerShell", fixes)
        self.assertIn("POSIX", fixes)
        self.assertEqual(fallback.result, "WARN")
        # a set key shows the length only, and both profiles become online targets
        env = {"MISTRAL_API_KEY": "sk-test-abcdef", "BACKUP_API_KEY": "sk-other-999"}
        rows, targets = self.rows_for(profiles, roles, env)
        primary = rows["profile mistral (primary): key"]
        self.assertEqual(primary.result, "PASS")
        self.assertIn(f"{len('sk-test-abcdef')} chars", primary.detail)
        self.assertNotIn("sk-test-abcdef", primary.detail)
        self.assertEqual(len(targets), 2)
        # billing = "local" without a key -> PASS "no key needed"
        profiles, roles = self.load_profiles(LOCAL_TOML)
        rows, targets = self.rows_for(profiles, roles, {})
        key_row = rows["profile local (primary): key"]
        self.assertEqual(key_row.result, "PASS")
        self.assertIn("no key needed", key_row.detail)
        self.assertEqual(len(targets), 1)

    def test_profile_enabled_checks(self):
        profiles, roles = self.load_profiles(ENABLED_TOML)
        rows, _targets = self.rows_for(profiles, roles, {})
        self.assertEqual(rows["profile mistral (primary): enabled"].result, "FAIL")
        self.assertEqual(rows["profile backup (fallback): enabled"].result, "WARN")

    def test_key_set_after_start_hint(self):
        profiles, roles = self.load_profiles(MISTRAL_TOML)
        lookup = lambda name: "x" if name == "MISTRAL_API_KEY" else None
        rows, _targets = self.rows_for(profiles, roles, {}, lookup)
        row = rows["profile mistral (primary): key"]
        self.assertEqual(row.result, "FAIL")
        self.assertIn("not in this session", row.detail)
        self.assertIn("restart Claude Code", " ".join(row.fixes))


# --- AC12: base_url and limit rules ------------------------------------------------------------------


class UrlAndLimitsTest(DoctorTest):
    def test_base_url_rules(self):
        profiles, roles = self.load_profiles(MISTRAL_TOML)
        profile = profiles["mistral"]
        env = {"MISTRAL_API_KEY": "k", "BACKUP_API_KEY": "k"}

        def row():
            rows, _ = doctor.check_implementer_profiles(
                profiles, roles, env, lambda name: None
            )
            return next(
                r for r in rows if r.name == "profile mistral (primary): base_url"
            )

        profile["base_url"] = "ftp://example.com/v1"
        self.assertEqual(row().result, "FAIL")
        profile["base_url"] = "http://"
        self.assertEqual(row().result, "FAIL")
        profile["base_url"] = "https://api.mistral.ai/v1"
        self.assertEqual(row().result, "PASS")
        profile["base_url"] = "https://api.mistral.ai/v1/chat/completions"
        self.assertEqual(row().result, "WARN")
        profile["base_url"] = "http://example.com/v1"
        self.assertEqual(row().result, "WARN")
        self.assertIn("unencrypted", row().detail)
        profile["base_url"] = "http://localhost:8080/v1"
        self.assertEqual(row().result, "PASS")
        profile["base_url"] = "https://api.mistral.ai/v1?x=1"
        self.assertEqual(row().result, "WARN")
        profile["driver"] = "anthropic-loop"
        profile["base_url"] = "https://api.anthropic.com/v1/messages"
        self.assertEqual(row().result, "WARN")

    def test_limit_rules(self):
        profiles, roles = self.load_profiles(MISTRAL_TOML)
        profile = profiles["mistral"]
        env = {"MISTRAL_API_KEY": "k", "BACKUP_API_KEY": "k"}

        def row():
            rows, _ = doctor.check_implementer_profiles(
                profiles, roles, env, lambda name: None
            )
            return next(
                r for r in rows if r.name == "profile mistral (primary): limits"
            )

        self.assertEqual(row().result, "PASS")
        profile["max_turns"] = 201
        self.assertEqual(row().result, "WARN")
        self.assertIn("max_turns = 201", row().detail)
        profile["max_turns"] = 60
        profile["max_cost_usd"] = None
        self.assertEqual(row().result, "WARN")
        self.assertIn("max_cost_usd", row().detail)
        profile["max_cost_usd"] = 1.5
        profile["price_input_per_mtok"] = 0
        profile["price_cached_input_per_mtok"] = 0
        profile["price_output_per_mtok"] = 0
        result = row()
        self.assertEqual(result.result, "WARN")
        self.assertIn("price", result.detail)
        # billing = "local": no cost-cap warnings
        profiles, roles = self.load_profiles(LOCAL_TOML)
        rows, _ = doctor.check_implementer_profiles(
            profiles, roles, {}, lambda name: None
        )
        self.assertEqual(
            next(r for r in rows if r.name == "profile local (primary): limits").result,
            "PASS",
        )


# --- AC13: DRIVER_CHECKS ---------------------------------------------------------------------------


class DriverTest(DoctorTest):
    def test_every_driver_has_checks(self):
        for driver in models.DRIVERS:
            self.assertIn(driver, doctor.DRIVER_CHECKS)


CLI_TOML = """
[profiles.codex]
driver = "cli"
model = "gpt-5.2-codex"
billing = "subscription"
command = ["codex", "exec", "-"]
prompt_via = "stdin"
usage_format = "codex-jsonl"
fallback = ["codex-api"]

[profiles.codex-api]
driver = "cli"
model = "gpt-5.2-codex"
billing = "api"
api_key_env = "CODEX_API_KEY"
pass_env = ["CODEX_API_KEY"]
command = ["codex", "exec", "-"]

[roles]
implementer = "codex"
"""


class CliProfileTest(DoctorTest):
    """The driver "cli" rows of check 14: executable and key, never an online probe."""

    def rows_for(self, profiles, roles, env, lookup=lambda name: None, which=None):
        kwargs = {} if which is None else {"which": which}
        rows, targets = doctor.check_implementer_profiles(
            profiles, roles, env, lookup, **kwargs
        )
        return {row.name: row for row in rows}, targets

    def fake_which(self, name):
        return f"/fake/bin/{name}" if name == "codex" else None

    def test_cli_rows_replace_base_url_and_limits(self):
        profiles, roles = self.load_profiles(CLI_TOML)
        rows, targets = self.rows_for(profiles, roles, {}, which=self.fake_which)
        self.assertNotIn("profile codex (primary): base_url", rows)
        self.assertNotIn("profile codex (primary): limits", rows)
        driver = rows["profile codex (primary): driver"]
        self.assertEqual(driver.result, "PASS")
        self.assertIn("no /models request", driver.detail)
        self.assertIn("timeout_seconds", driver.detail)
        self.assertIn("saved login", driver.detail)
        command = rows["profile codex (primary): command"]
        self.assertEqual(command.result, "PASS")
        self.assertIn("/fake/bin/codex", command.detail)
        # subscription: no key needed, and never an online target
        key = rows["profile codex (primary): key"]
        self.assertEqual(key.result, "PASS")
        self.assertIn("no api_key_env", key.detail)
        self.assertEqual(targets, [])
        # the api fallback: its key is required, and it is no online target either
        api_key = rows["profile codex-api (fallback): key"]
        self.assertEqual(api_key.result, "WARN")
        self.assertIn("CODEX_API_KEY", api_key.detail)
        self.assertEqual(targets, [])

    def test_cli_missing_executable_fails_primary_warns_fallback(self):
        profiles, roles = self.load_profiles(CLI_TOML)
        rows, _ = self.rows_for(profiles, roles, {}, which=lambda name: None)
        primary = rows["profile codex (primary): command"]
        self.assertEqual(primary.result, "FAIL")
        self.assertIn("not found on PATH", primary.detail)
        self.assertIn("install codex", " ".join(primary.fixes))
        self.assertEqual(rows["profile codex-api (fallback): command"].result, "WARN")

    def test_cli_api_key_set_passes_without_revealing_and_stays_offline(self):
        profiles, roles = self.load_profiles(CLI_TOML)
        env = {"CODEX_API_KEY": "sk-codex-123456"}
        rows, targets = self.rows_for(profiles, roles, env, which=self.fake_which)
        key = rows["profile codex-api (fallback): key"]
        self.assertEqual(key.result, "PASS")
        self.assertIn("chars", key.detail)
        self.assertNotIn("sk-codex-123456", key.detail)
        self.assertEqual(targets, [])

    def test_cli_profile_is_never_probed_online(self):
        provider = FakeProvider(make_handler([], 200, ["gpt-5.2-codex"]))
        self.addCleanup(provider.stop)
        self.project_models(CLI_TOML)
        os.environ["CODEX_API_KEY"] = "sk-live-key-1"
        _code, out, _ = self.run_doctor(["--project", str(self.repo), "--online"])
        self.assertNotIn("| online", out)
        self.assertEqual(provider.hits, [])


# --- AC14: the online check ------------------------------------------------------------------------


class OnlineTest(DoctorTest):
    def provider(self, status=200, ids=(), echo_auth_in_body=False):
        hits: list[dict] = []
        provider = FakeProvider(make_handler(hits, status, ids, echo_auth_in_body))
        provider.hits = hits
        self.addCleanup(provider.stop)
        return provider

    def test_online_model_listed(self):
        provider = self.provider(ids=["mistral-large", "mistral-small"])
        self.project_models(self.online_toml(provider.url))
        os.environ["MISTRAL_API_KEY"] = "sk-live-key-1"
        _code, out, _ = self.run_doctor(["--project", str(self.repo), "--online"])
        row = self.row(out, "online mistral")
        self.assertIn("PASS", row)
        self.assertIn("HTTP 200", row)
        self.assertRegex(row, r"\d+ ms")
        self.assertIn("`mistral-large` listed", row)
        self.assertEqual(provider.hits[0]["path"], "/v1/models")
        self.assertEqual(provider.hits[0]["authorization"], "Bearer sk-live-key-1")
        self.assertNotIn("sk-live-key-1", out)

    def test_online_model_missing(self):
        provider = self.provider(
            ids=["mistral-small", "mistral-medium", "mistral-large-latest"]
        )
        self.project_models(self.online_toml(provider.url, model="mistral-large"))
        os.environ["MISTRAL_API_KEY"] = "sk-live-key-1"
        _code, out, _ = self.run_doctor(["--project", str(self.repo), "--online"])
        row = self.row(out, "online mistral")
        self.assertIn("WARN", row)
        self.assertIn("not among the 3 listed ids", row)
        self.assertIn("mistral-large-latest", row)
        self.assertIn(f"GET {provider.url}/models", out)

    def test_online_auth_rejected(self):
        provider = self.provider(status=401)
        self.project_models(self.online_toml(provider.url))
        os.environ["MISTRAL_API_KEY"] = "sk-live-key-1"
        code, out, _ = self.run_doctor(["--project", str(self.repo), "--online"])
        self.assertEqual(code, 1)
        self.assertIn("key rejected", self.row(out, "online mistral"))

    def test_online_no_model_list(self):
        provider = self.provider(status=404)
        self.project_models(self.online_toml(provider.url))
        os.environ["MISTRAL_API_KEY"] = "sk-live-key-1"
        _code, out, _ = self.run_doctor(["--project", str(self.repo), "--online"])
        row = self.row(out, "online mistral")
        self.assertIn("WARN", row)
        self.assertIn("no model list", row)

    def test_online_unreachable(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.project_models(self.online_toml(f"http://127.0.0.1:{port}/v1"))
        os.environ["MISTRAL_API_KEY"] = "sk-live-key-1"
        code, out, _ = self.run_doctor(["--project", str(self.repo), "--online"])
        self.assertEqual(code, 1)
        row = self.row(out, "online mistral")
        self.assertIn("FAIL", row)
        self.assertIn("URLError", row)
        self.assertRegex(row, r"\d+ ms")

    def test_offline_sends_nothing(self):
        provider = self.provider(ids=["mistral-large"])
        self.project_models(self.online_toml(provider.url))
        os.environ["MISTRAL_API_KEY"] = "sk-live-key-1"
        _code, out, _ = self.run_doctor(["--project", str(self.repo)])
        self.assertEqual(provider.hits, [])
        self.assertNotIn("| online", out)


# --- AC15: no secret is printed ----------------------------------------------------------------------


class SecretTest(DoctorTest):
    def test_never_prints_secret(self):
        # the server echoes the Authorization header in an id and in an error body
        provider = FakeProvider(
            make_handler(
                [], 200, [SECRET + "-echo", "other-model"], echo_auth_in_body=True
            )
        )
        self.addCleanup(provider.stop)
        self.project_models(self.online_toml(provider.url, model="missing-model"))
        os.environ["MISTRAL_API_KEY"] = SECRET
        os.environ["ANTHROPIC_API_KEY"] = SECRET
        code, out, err = self.run_doctor(["--project", str(self.repo), "--online"])
        self.assertNotIn(SECRET, out)
        self.assertNotIn(SECRET, err)
        self.assertIn("***", out)  # the echoed id was redacted
        # a 401 whose body echoes the header: the body is never printed
        provider2 = FakeProvider(make_handler([], 401, echo_auth_in_body=True))
        self.addCleanup(provider2.stop)
        self.project_models(self.online_toml(provider2.url))
        code, out, err = self.run_doctor(["--project", str(self.repo), "--online"])
        self.assertEqual(code, 1)
        self.assertNotIn(SECRET, out)
        self.assertNotIn(SECRET, err)
        # offline too
        code, out, err = self.run_doctor(["--project", str(self.repo)])
        self.assertNotIn(SECRET, out)
        self.assertNotIn(SECRET, err)


# --- AC16: the doctor changes nothing ------------------------------------------------------------------


@unittest.skipUnless(HAS_GIT, "git is not on PATH")
class ChangesNothingTest(DoctorTest):
    def test_changes_nothing(self):
        provider = FakeProvider(make_handler([], 200, ["mistral-large"]))
        self.addCleanup(provider.stop)
        self.init_repo()
        self.write_full_settings()
        self.project_models(self.online_toml(provider.url))
        self.commit(".claude/settings.json", ".claude/task-flow.models.toml")
        os.environ["MISTRAL_API_KEY"] = "sk-live-key-1"
        before_env = dict(os.environ)

        def snapshot(root):
            snap = {}
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    stat = path.stat()
                    snap[str(path)] = (path.read_bytes(), stat.st_mtime_ns)
            return snap

        def status():
            return subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "--no-optional-locks",
                    "status",
                    "--porcelain",
                ],
                capture_output=True,
                check=True,
            ).stdout

        repo_before = snapshot(self.repo)
        home_before = snapshot(self.home)
        status_before = status()
        code, out, _ = self.run_doctor(["--project", str(self.repo), "--online"])
        self.assertEqual(code, 0, out)
        self.assertEqual(snapshot(self.repo), repo_before)
        self.assertEqual(snapshot(self.home), home_before)
        self.assertEqual(status(), status_before)
        self.assertEqual(dict(os.environ), before_env)


if __name__ == "__main__":
    unittest.main()
