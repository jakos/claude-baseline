# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/models.py: loads, merges and validates temp models.toml files.

Run: uv run --script plugins/task-flow/tests/test_models.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tomllib

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "models.py"
TEMPLATE = SCRIPTS.parent / "templates" / "models.toml"
AGENTS = SCRIPTS.parent / "agents"
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

    def test_post_edit_and_prompt_extras_load_and_show(self):
        self.project(
            BASE
            + 'post_edit = ["uvx ruff format {path}"]\nprompt_extras = ["AGENTS.md"]\n'
        )
        profiles, _roles = models.load(self.repo)
        profile = profiles["mistral"]
        self.assertEqual(profile["post_edit"], ["uvx ruff format {path}"])
        self.assertEqual(profile["prompt_extras"], ["AGENTS.md"])
        text = models.show_text(profile)
        self.assertIn('post_edit = ["uvx ruff format {path}"]', text)
        self.assertIn('prompt_extras = ["AGENTS.md"]', text)

    def test_post_edit_and_prompt_extras_default_to_empty_lists(self):
        self.project(BASE)
        profile = models.load(self.repo)[0]["mistral"]
        self.assertEqual(profile["post_edit"], [])
        self.assertEqual(profile["prompt_extras"], [])

    def test_post_edit_and_prompt_extras_invalid_values_rejected(self):
        cases = {
            'post_edit = "x"': "profiles.mistral.post_edit",
            'post_edit = [""]': "profiles.mistral.post_edit",
            "post_edit = [1]": "profiles.mistral.post_edit",
            'prompt_extras = "a.md"': "profiles.mistral.prompt_extras",
            'prompt_extras = [""]': "profiles.mistral.prompt_extras",
        }
        for line, needle in cases.items():
            with self.subTest(line=line):
                self.assertRejected(
                    BASE + line + "\n", needle, "must be a list of non-empty strings"
                )

    def test_project_list_replaces_user_list(self):
        self.user(BASE + 'post_edit = ["a {path}"]\nprompt_extras = ["a.md"]\n')
        self.project(
            '[profiles.mistral]\npost_edit = ["b {path}"]\nprompt_extras = ["b.md"]\n'
        )
        profile = models.load(self.repo)[0]["mistral"]
        self.assertEqual(profile["post_edit"], ["b {path}"])
        self.assertEqual(profile["prompt_extras"], ["b.md"])

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

    def run_roles(self, *args):
        return self.run_cli("roles", *args)

    def roles_json(self, *args):
        done = self.run_roles(*args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_roles_cli_no_config_all_claude_null_model(self):
        done = self.run_roles()
        self.assertEqual(done.returncode, 0, done.stderr)
        mapping = json.loads(done.stdout)
        self.assertEqual(len(mapping), 5)
        self.assertEqual(set(mapping), set(models.ROLES))
        for entry in mapping.values():
            self.assertEqual(
                entry, {"kind": "claude", "model": None, "value": "claude"}
            )

    def test_roles_cli_claude_variants_give_model(self):
        self.project(
            '[roles]\nplanner = "claude:haiku"\n'
            'reviewer = "claude:sonnet"\nchecker = "claude"\n'
        )
        mapping = self.roles_json()
        self.assertEqual(
            mapping["planner"],
            {"kind": "claude", "model": "haiku", "value": "claude:haiku"},
        )
        self.assertEqual(
            mapping["reviewer"],
            {"kind": "claude", "model": "sonnet", "value": "claude:sonnet"},
        )
        self.assertEqual(
            mapping["checker"], {"kind": "claude", "model": None, "value": "claude"}
        )
        for role in ("implementer", "backlog"):
            self.assertEqual(
                mapping[role], {"kind": "claude", "model": None, "value": "claude"}
            )

    def test_roles_cli_profile_and_script_kinds(self):
        self.project(BASE + '[roles]\nimplementer = "mistral"\nchecker = "script"\n')
        mapping = self.roles_json()
        self.assertEqual(
            mapping["implementer"],
            {"kind": "profile", "model": None, "value": "mistral"},
        )
        self.assertEqual(
            mapping["checker"], {"kind": "script", "model": None, "value": "script"}
        )

    def test_roles_cli_project_overrides_user(self):
        self.user('[roles]\nreviewer = "claude:haiku"\n')
        self.project('[roles]\nreviewer = "claude:opus"\n')
        mapping = self.roles_json()
        self.assertEqual(mapping["reviewer"]["model"], "opus")
        self.assertEqual(mapping["reviewer"]["value"], "claude:opus")

    def test_roles_cli_invalid_value_exits_2(self):
        self.project('[roles]\nreviewer = "claude:gpt"\n')
        done = self.run_roles()
        self.assertEqual(done.returncode, 2)
        self.assertIn("roles.reviewer", done.stderr)
        self.assertEqual(done.stdout, "")

    def test_roles_cli_misspelled_role_exits_2(self):
        self.project('[roles]\nreveiwer = "claude:opus"\n')
        done = self.run_roles()
        self.assertEqual(done.returncode, 2)
        self.assertIn("unknown role", done.stderr)
        self.assertEqual(done.stdout, "")

    def test_template_loads_and_covers_every_key(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.project(text)
        models.load(self.repo)  # must not raise
        raw = tomllib.loads(text)
        self.assertEqual(len(raw["profiles"]), 1)
        profile = next(iter(raw["profiles"].values()))
        self.assertEqual(set(profile), set(models.PROFILE_KEYS))
        self.assertEqual(set(raw["roles"]), set(models.ROLES))
        self.assertEqual(set(raw["gates"]), set(models.GATES_KEYS))
        self.assertEqual(models.gates(self.repo), {"command_timeout_seconds": 600})

    def test_gates_default_timeout(self):
        self.assertEqual(models.gates(self.repo), {"command_timeout_seconds": 600})
        self.assertEqual(
            models.gates(None), {"command_timeout_seconds": 600}
        )  # user file only, absent
        self.user("[gates]\ncommand_timeout_seconds = 300\n")
        self.assertEqual(models.gates(self.repo), {"command_timeout_seconds": 300})
        self.assertEqual(models.gates(None), {"command_timeout_seconds": 300})

    def test_gates_project_overrides_user(self):
        self.user("[gates]\ncommand_timeout_seconds = 300\n")
        self.project("[gates]\ncommand_timeout_seconds = 120\n")
        self.assertEqual(models.gates(self.repo)["command_timeout_seconds"], 120)
        # a project file without [gates] keeps the user value
        self.project('[roles]\nchecker = "script"\n')
        self.assertEqual(models.gates(self.repo)["command_timeout_seconds"], 300)
        self.assertEqual(models.load(self.repo)[1], {"checker": "script"})

    def test_gates_invalid_values_rejected(self):
        cases = [
            ("[gates]\ncommand_timeout_seconds = 0\n", "gates.command_timeout_seconds"),
            (
                "[gates]\ncommand_timeout_seconds = -5\n",
                "gates.command_timeout_seconds",
            ),
            (
                '[gates]\ncommand_timeout_seconds = "x"\n',
                "gates.command_timeout_seconds",
            ),
            (
                "[gates]\ncommand_timeout_seconds = true\n",
                "gates.command_timeout_seconds",
            ),
            ("[gates]\ncommand_timeout_seconds = 1.5\nok = 1\n", "gates.ok"),
            ("[gates]\nbogus = 1\n", "gates.bogus"),
            ("gates = 5\n", "gates: must be a table"),
        ]
        for text, needle in cases:
            with self.subTest(text=text):
                path = self.project(text)
                for call in (
                    lambda: models.gates(self.repo),
                    lambda: models.load(self.repo),
                ):
                    with self.assertRaises(models.ConfigError) as ctx:
                        call()
                    message = str(ctx.exception)
                    self.assertIn(str(path), message)
                    self.assertIn(needle, message)

    def test_template_roles_match_agent_frontmatter(self):
        raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
        for role, value in raw["roles"].items():
            agent = (AGENTS / f"{role}.md").read_text(encoding="utf-8")
            frontmatter = agent.split("---")[1]
            model_line = next(
                ln for ln in frontmatter.splitlines() if ln.startswith("model:")
            )
            frontmatter_model = model_line.split(":", 1)[1].strip()
            self.assertEqual(value, f"claude:{frontmatter_model}", role)

    # ------------------------------------------------------------- anthropic-loop

    ANTHROPIC = """
[profiles.claude]
driver = "anthropic-loop"
base_url = "https://api.anthropic.com/v1"
model = "claude-haiku-5-5"
api_key_env = "TASK_FLOW_ANTHROPIC_KEY"
billing = "api"
"""

    def test_anthropic_driver_accepted(self):
        self.project(self.ANTHROPIC)
        profiles, _roles = models.load(self.repo)
        profile = profiles["claude"]
        self.assertEqual(profile["driver"], "anthropic-loop")
        self.assertEqual(profile["max_tokens_param"], "max_tokens")
        self.assertIsNone(profile["price_cache_write_per_mtok"])
        self.assertIsNone(profile["temperature"])

    def test_anthropic_rejects_temperature(self):
        self.assertRejected(
            self.ANTHROPIC + "temperature = 0.7\n", "profiles.claude.temperature"
        )

    def test_extra_body_reserved_keys_per_driver(self):
        for key in ("system", "max_tokens", "stream", "temperature", "top_p", "top_k"):
            with self.subTest(key=key):
                self.assertRejected(
                    self.ANTHROPIC + f"extra_body = {{ {key} = 1 }}\n",
                    "profiles.claude.extra_body",
                    "reserved",
                )
        # system and max_tokens are still accepted on openai-loop
        self.project(BASE + 'extra_body = { system = "x", max_tokens = 5 }\n')
        profiles, _roles = models.load(self.repo)
        self.assertEqual(
            profiles["mistral"]["extra_body"], {"system": "x", "max_tokens": 5}
        )
        # a project-file extra_body is checked against a driver set in the user file
        self.user(
            BASE.replace('driver = "openai-loop"', 'driver = "anthropic-loop"').replace(
                "api.mistral.ai", "api.anthropic.com"
            )
        )
        self.project('[profiles.mistral]\nextra_body = { system = "x" }\n')
        with self.assertRaises(models.ConfigError) as ctx:
            models.load(self.repo)
        message = str(ctx.exception)
        self.assertIn("profiles.mistral.extra_body", message)
        self.assertIn("reserved", message)

    def test_max_tokens_param_validated(self):
        self.assertRejected(BASE + 'max_tokens_param = "tokens"\n', "max_tokens_param")
        # max_completion_tokens is valid on openai-loop ...
        self.project(BASE + 'max_tokens_param = "max_completion_tokens"\n')
        profiles, _roles = models.load(self.repo)
        self.assertEqual(
            profiles["mistral"]["max_tokens_param"], "max_completion_tokens"
        )
        # ... but rejected on anthropic-loop, whose default is max_tokens
        self.assertRejected(
            self.ANTHROPIC + 'max_tokens_param = "max_completion_tokens"\n',
            "profiles.claude.max_tokens_param",
        )
        self.project(self.ANTHROPIC)
        self.assertEqual(
            models.load(self.repo)[0]["claude"]["max_tokens_param"], "max_tokens"
        )

    def test_price_cache_write_key(self):
        self.assertRejected(
            BASE + "price_cache_write_per_mtok = -1\n", "price_cache_write_per_mtok"
        )
        self.project(BASE + "price_cache_write_per_mtok = 0.125\n")
        profiles, _roles = models.load(self.repo)
        self.assertEqual(profiles["mistral"]["price_cache_write_per_mtok"], 0.125)
        self.project(BASE)
        self.assertIsNone(
            models.load(self.repo)[0]["mistral"]["price_cache_write_per_mtok"]
        )

    def test_warnings_for_anthropic_api_key_env(self):
        self.project(self.ANTHROPIC + BASE)
        profiles, _roles = models.load(self.repo)
        self.assertEqual(models.warnings(profiles), [])
        self.project(
            self.ANTHROPIC.replace(
                'api_key_env = "TASK_FLOW_ANTHROPIC_KEY"',
                'api_key_env = "ANTHROPIC_API_KEY"',
            )
            + BASE
        )
        profiles, _roles = models.load(self.repo)
        lines = models.warnings(profiles)
        self.assertEqual(len(lines), 1)
        self.assertIn("profiles.claude", lines[0])
        self.assertIn("ANTHROPIC_API_KEY", lines[0])
        self.assertIn("subscription", lines[0])

    def test_show_warns_on_anthropic_api_key_env(self):
        self.project(
            self.ANTHROPIC.replace(
                'api_key_env = "TASK_FLOW_ANTHROPIC_KEY"',
                'api_key_env = "ANTHROPIC_API_KEY"',
            )
        )
        env = {k: v for k, v in os.environ.items() if k != "MISTRAL_API_KEY"}
        env["HOME"] = env["USERPROFILE"] = str(self.home)
        env["ANTHROPIC_API_KEY"] = "sk-ant-very-secret"
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "show", "claude"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=self.repo,
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ANTHROPIC_API_KEY", done.stderr)
        self.assertIn("subscription", done.stderr)
        self.assertIn("api_key: set", done.stdout)
        self.assertNotIn("sk-ant-very-secret", done.stdout + done.stderr)
        # a profile with another variable name gives no warning
        self.project(self.ANTHROPIC)
        quiet = self.run_show("claude")
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(quiet.stderr, "")


if __name__ == "__main__":
    unittest.main()
