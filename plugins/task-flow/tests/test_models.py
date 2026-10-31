# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/models.py: loads, merges and validates temp models.toml files.

Run: uv run --script plugins/task-flow/tests/test_models.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "models.py"
sys.path.insert(0, str(SCRIPTS))
import models

BASE = """
[profiles.mistral]
driver = "openai-loop"
base_url = "https://api.mistral.ai/v1"
model = "placeholder-model"
api_key_env = "MISTRAL_API_KEY"
billing = "api"
"""


class ModelsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "home"
        self.repo = Path(self._tmp.name) / "repo"
        self.home.mkdir()
        self.repo.mkdir()
        patcher = mock.patch.object(
            models, "user_file", lambda: self.home / ".claude/task-flow/models.toml"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def user(self, text):
        path = self.home / ".claude" / "task-flow" / "models.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def project(self, text):
        path = self.repo / ".claude" / "task-flow.models.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def assertRejected(self, text, *needles):
        path = self.project(text)
        with self.assertRaises(models.ConfigError) as ctx:
            models.load(self.repo)
        message = str(ctx.exception)
        self.assertIn(str(path), message)
        for needle in needles:
            self.assertIn(needle, message)

    def test_project_overrides_user_per_key(self):
        self.user(
            BASE
            + 'max_turns = 10\nretries = 1\n[roles]\nplanner = "claude:opus"\nreviewer = "claude"\n'
        )
        self.project(
            '[profiles.mistral]\nmodel = "other"\nmax_turns = 20\n[roles]\nplanner = "claude:haiku"\n'
        )
        profiles, roles = models.load(self.repo)
        profile = profiles["mistral"]
        self.assertEqual(profile["model"], "other")
        self.assertEqual(profile["max_turns"], 20)
        self.assertEqual(profile["retries"], 1)  # kept from the user file
        self.assertEqual(profile["base_url"], "https://api.mistral.ai/v1")
        self.assertEqual(profile["request_timeout_seconds"], 300)  # default filled
        self.assertEqual(roles, {"planner": "claude:haiku", "reviewer": "claude"})

    def test_missing_files_mean_no_profiles(self):
        self.assertEqual(models.load(self.repo), ({}, {}))

    def test_unknown_key_rejected(self):
        self.assertRejected(
            BASE + "max_turn = 3\n", "profiles.mistral.max_turn", "unknown"
        )
        self.assertRejected("[extra]\na = 1\n", "unknown top-level")

    def test_invalid_values_rejected(self):
        cases = {
            'driver = "cli"': "driver",
            'billing = "free"': "billing",
            'base_url = "ftp://x"': "base_url",
            "price_input_per_mtok = -1": "price_input_per_mtok",
            'max_turns = "ten"': "max_turns",
            "max_turns = 1.5": "max_turns",
            "max_turns = true": "max_turns",
            'api_key_env = "not a name"': "api_key_env",
            "temperature = 2.5": "temperature",
            'extra_body = "x"': "extra_body",
            'extra_body = { model = "x" }': "reserved",
            "extra_body = { tools = [] }": "reserved",
            "request_timeout_seconds = 0": "request_timeout_seconds",
            'enabled = "yes"': "enabled",
        }
        for line, needle in cases.items():
            with self.subTest(line=line):
                # a later duplicate key would be invalid TOML, so replace the base line
                text = BASE
                key = line.split("=")[0].strip()
                text = "\n".join(
                    ln for ln in text.splitlines() if not ln.startswith(f"{key} =")
                )
                self.assertRejected(text + "\n" + line + "\n", needle)

    def test_required_keys(self):
        self.assertRejected(
            '[profiles.x]\ndriver = "openai-loop"\n', "profiles.x.base_url", "required"
        )
        self.assertRejected(
            BASE.replace('api_key_env = "MISTRAL_API_KEY"\n', ""), "api_key_env"
        )
        # local profiles need no key
        self.project(
            BASE.replace('api_key_env = "MISTRAL_API_KEY"\n', "").replace(
                "api", "local"
            )
        )
        self.assertIn("mistral", models.load(self.repo)[0])

    def test_fallback_must_name_existing_profiles(self):
        self.assertRejected(BASE + 'fallback = ["nope"]\n', "fallback", "nope")
        self.assertRejected(BASE + 'fallback = ["mistral"]\n', "itself")
        self.project(
            BASE
            + 'fallback = ["b"]\n[profiles.b]\n'
            + BASE.split("\n", 2)[2].replace("api.mistral.ai", "b.example")
        )
        profiles, _ = models.load(self.repo)
        self.assertEqual(profiles["mistral"]["fallback"], ["b"])

    def test_roles_values_validated(self):
        self.assertRejected(BASE + '[roles]\nplanner = "mistral"\n', "roles.planner")
        self.assertRejected(
            BASE + '[roles]\nimplementer = "missing"\n', "unknown profile"
        )
        self.assertRejected(BASE + '[roles]\nimplementer = "script"\n', "checker")
        self.assertRejected(
            BASE + '[roles]\nboss = "claude"\n', "roles.boss", "unknown role"
        )
        self.assertRejected(BASE + '[roles]\nchecker = "claude:gpt"\n', "roles.checker")
        self.project(
            BASE
            + '[roles]\nimplementer = "mistral"\nchecker = "script"\nbacklog = "claude:opus"\n'
        )
        _, roles = models.load(self.repo)
        self.assertEqual(roles["implementer"], "mistral")

    def test_invalid_toml_reports_file(self):
        path = self.user("[profiles\nbroken")
        with self.assertRaises(models.ConfigError) as ctx:
            models.load(self.repo)
        self.assertIn(str(path), str(ctx.exception))
        self.assertIn("invalid TOML", str(ctx.exception))

    def run_show(self, *args, key=None):
        return self.run_cli("show", *args, key=key)

    def run_cli(self, *args, key=None):
        env = {k: v for k, v in os.environ.items() if k != "MISTRAL_API_KEY"}
        env["HOME"] = env["USERPROFILE"] = str(self.home)
        if key:
            env["MISTRAL_API_KEY"] = key
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=self.repo,
            timeout=60,
            check=False,
        )

    def test_show_prints_no_secret(self):
        self.project(BASE)
        done = self.run_show("mistral", key="sk-very-secret")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('model = "placeholder-model"', done.stdout)
        self.assertIn("max_turns = 60", done.stdout)
        self.assertIn("api_key: set", done.stdout)
        self.assertNotIn("sk-very-secret", done.stdout + done.stderr)
        done = self.run_show("mistral", "--project", str(self.repo))
        self.assertIn("api_key: missing", done.stdout)

    def test_show_unknown_profile_refused(self):
        done = self.run_show("mistral")  # no config at all
        self.assertEqual(done.returncode, 2)
        self.assertIn("unknown profile", done.stderr)
        self.project(BASE)
        done = self.run_show("other")
        self.assertEqual(done.returncode, 2)
        done = self.run_show("mistral", "--project", str(self.repo / "nope"))
        self.assertEqual(done.returncode, 2)
        self.project("[profiles.mistral]\nbogus = 1\n")
        done = self.run_show("mistral")
        self.assertEqual(done.returncode, 2)
        self.assertIn("bogus", done.stderr)

    def test_role_cli_no_config_prints_claude(self):
        done = self.run_cli("role", "implementer")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "claude")

    def test_role_cli_unset_prints_claude(self):
        self.project(BASE)
        done = self.run_cli("role", "implementer")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "claude")

    def test_role_cli_prints_profile_name(self):
        self.project(BASE + '[roles]\nimplementer = "mistral"\n')
        done = self.run_cli("role", "implementer", "--project", str(self.repo))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "mistral")

    def test_role_cli_prints_claude_variant(self):
        self.project('[roles]\nimplementer = "claude:sonnet"\n')
        done = self.run_cli("role", "implementer")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "claude:sonnet")

    def test_role_cli_invalid_config_exits_2(self):
        self.project('[roles]\nimplementer = "nosuch"\n')
        done = self.run_cli("role", "implementer")
        self.assertEqual(done.returncode, 2)
        self.assertIn("nosuch", done.stderr)

    def test_role_cli_unknown_role_exits_2(self):
        done = self.run_cli("role", "wizard")
        self.assertEqual(done.returncode, 2)
        self.assertIn("unknown role", done.stderr)


if __name__ == "__main__":
    unittest.main()
