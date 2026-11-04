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
import tomllib
import unittest
from pathlib import Path
from unittest import mock

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
            # "cli" is a valid driver now, but this profile has no command and
            # sets API-only keys, so it is rejected for the missing command
            'driver = "cli"': "command",
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
            '[profiles.x]\ndriver = "openai-loop"\nmodel = "m"\nbilling = "api"\n',
            "profiles.x.base_url",
            "required",
        )
        self.assertRejected(
            '[profiles.x]\ndriver = "openai-loop"\nmodel = "m"\nbase_url = "https://x/v1"\n'
            'billing = "api"\n',
            "profiles.x.api_key_env",
            "required",
        )
        # a cli profile needs no base_url, only its command
        self.assertRejected(
            '[profiles.x]\ndriver = "cli"\nmodel = "m"\nbilling = "subscription"\n',
            "profiles.x.command",
            "required",
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
        # planner and implementer may name a profile; backlog and reviewer may not
        self.assertRejected(BASE + '[roles]\nbacklog = "mistral"\n', "roles.backlog")
        self.assertRejected(BASE + '[roles]\nreviewer = "mistral"\n', "roles.reviewer")
        self.assertRejected(
            BASE + '[roles]\nimplementer = "missing"\n', "unknown profile"
        )
        self.assertRejected(BASE + '[roles]\nplanner = "missing"\n', "unknown profile")
        self.assertRejected(BASE + '[roles]\nimplementer = "script"\n', "checker")
        self.assertRejected(
            BASE + '[roles]\nboss = "claude"\n', "roles.boss", "unknown role"
        )
        self.assertRejected(BASE + '[roles]\nchecker = "claude:gpt"\n', "roles.checker")
        self.project(
            BASE + '[roles]\nplanner = "mistral"\nimplementer = "mistral"\n'
            'checker = "script"\nbacklog = "claude:opus"\n'
        )
        _, roles = models.load(self.repo)
        self.assertEqual(roles["planner"], "mistral")
        self.assertEqual(roles["implementer"], "mistral")
        resolved = models.resolve_roles(self.repo)
        self.assertEqual(resolved["planner"]["kind"], "profile")
        self.assertEqual(resolved["backlog"]["kind"], "claude")
        self.assertEqual(resolved["checker"]["kind"], "script")

    def test_implementer_table_form_normalizes_to_scalar(self):
        self.project(BASE + '[roles.implementer]\ndefault = "mistral"\n')
        _profiles, roles = models.load(self.repo)
        self.assertEqual(roles, {"implementer": "mistral"})
        self.assertEqual(
            models.resolve_roles(self.repo)["implementer"],
            {"value": "mistral", "kind": "profile", "model": None},
        )
        # the table and the scalar resolve identically
        scalar = BASE + '[roles]\nimplementer = "mistral"\n'
        self.project(scalar)
        self.assertEqual(models.load(self.repo)[1], {"implementer": "mistral"})

    def test_implementer_table_rejects_unknown_and_missing_keys(self):
        self.assertRejected(
            BASE + '[roles.implementer]\nrouting = "x"\ndefault = "mistral"\n',
            "roles.implementer.routing",
            "unknown key",
        )
        self.assertRejected(
            BASE + '[roles.implementer]\nrouting = "x"\n',
            "roles.implementer.routing",
        )
        self.assertRejected(
            BASE + "[roles.implementer]\n", "roles.implementer", "default"
        )
        self.assertRejected(
            BASE + "[roles.implementer]\ndefault = 5\n", "roles.implementer.default"
        )
        self.assertRejected(
            BASE + '[roles.implementer]\ndefault = "missing"\n', "unknown profile"
        )
        # a table is only valid for implementer
        self.assertRejected(
            BASE + '[roles.planner]\ndefault = "mistral"\n',
            "roles.planner",
            "table",
        )

    def test_scalar_table_replacement_across_user_and_project(self):
        # user scalar + project table -> the project table wins, no mixing
        self.user(BASE + '[roles]\nimplementer = "mistral"\n')
        self.project(
            BASE.replace("mistral", "other").replace("api.mistral.ai", "b.example")
            + '[roles.implementer]\ndefault = "other"\n'
        )
        _p, roles = models.load(self.repo)
        self.assertEqual(roles, {"implementer": "other"})
        # user table + project scalar -> the project scalar wins
        self.user(
            BASE.replace("mistral", "other").replace("api.mistral.ai", "b.example")
            + '[roles.implementer]\ndefault = "other"\n'
        )
        self.project(BASE + '[roles]\nimplementer = "mistral"\n')
        _p, roles = models.load(self.repo)
        self.assertEqual(roles, {"implementer": "mistral"})
        # both tables: the project default wins wholesale
        self.user(
            BASE.replace("mistral", "other").replace("api.mistral.ai", "b.example")
            + '[roles.implementer]\ndefault = "other"\n'
        )
        self.project(BASE + '[roles.implementer]\ndefault = "mistral"\n')
        _p, roles = models.load(self.repo)
        self.assertEqual(roles, {"implementer": "mistral"})

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
        # the example profile is an API one, so it carries every non-CLI key; the
        # CLI-only keys live in the commented cli example below it and are checked
        # as text, because setting them on an API profile is an error
        self.assertEqual(set(profile), set(models.PROFILE_KEYS) - models.CLI_ONLY_KEYS)
        for key in sorted(models.CLI_ONLY_KEYS):
            self.assertIn(key, text, f"template: the cli example must name {key}")
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

    # ------------------------------------------------------------- cli driver

    CLI_SUBSCRIPTION = """
[profiles.codex]
driver = "cli"
model = "gpt-5.2-codex"
billing = "subscription"
command = ["codex", "exec", "--sandbox", "workspace-write", "--json", "-"]
"""

    CLI_API = """
[profiles.codex-api]
driver = "cli"
model = "gpt-5.2-codex"
billing = "api"
api_key_env = "CODEX_API_KEY"
pass_env = ["CODEX_API_KEY"]
command = ["codex", "exec", "--json", "-o", "{last_message_file}", "-"]
prompt_via = "stdin"
usage_format = "codex-jsonl"
timeout_seconds = 900
limit_patterns = ["rate limit reached"]
"""

    def test_cli_subscription_profile_loads_with_defaults(self):
        self.project(self.CLI_SUBSCRIPTION)
        profile = models.load(self.repo)[0]["codex"]
        self.assertEqual(profile["driver"], "cli")
        self.assertIsNone(profile["base_url"])
        self.assertIsNone(profile["api_key_env"])
        self.assertEqual(profile["timeout_seconds"], 600)
        self.assertEqual(profile["prompt_via"], "stdin")
        self.assertEqual(profile["usage_format"], "none")
        self.assertEqual(profile["pass_env"], [])
        self.assertEqual(profile["limit_patterns"], [])
        self.assertEqual(profile["command"][-1], "-")
        self.assertIn("api_key: none needed", models.show_text(profile))

    def test_cli_api_profile_loads_and_shows_key_state(self):
        self.project(self.CLI_API)
        profile = models.load(self.repo)[0]["codex-api"]
        self.assertEqual(profile["pass_env"], ["CODEX_API_KEY"])
        self.assertEqual(profile["usage_format"], "codex-jsonl")
        self.assertEqual(profile["timeout_seconds"], 900)
        self.assertEqual(profile["limit_patterns"], ["rate limit reached"])
        self.assertIn("api_key: missing", models.show_text(profile))

    def test_cli_placeholders_worktree_prompt_file_last_message(self):
        self.project(
            self.CLI_SUBSCRIPTION.replace(
                'command = ["codex", "exec", "--sandbox", "workspace-write", "--json", "-"]',
                'command = ["codex", "exec", "--json", "-o", "{last_message_file}", '
                '"--cd", "{worktree}", "{prompt_file}"]\nprompt_via = "file"',
            )
        )
        profile = models.load(self.repo)[0]["codex"]
        self.assertEqual(profile["prompt_via"], "file")
        self.assertIn("{worktree}", profile["command"][-2])

    def test_cli_rejects_bad_keys_and_values(self):
        no_command = "\n".join(
            ln
            for ln in self.CLI_SUBSCRIPTION.splitlines()
            if not ln.startswith("command =")
        )
        cases = {
            "command = []": "command",
            'command = "codex"': "command",
            'command = [""]': "command",
            "timeout_seconds = 0": "timeout_seconds",
            'prompt_via = "env"': "prompt_via",
            'usage_format = "claude-json"': "usage_format",
            'pass_env = ["not a name"]': "pass_env",
            'pass_env = "CODEX_API_KEY"': "pass_env",
            'limit_patterns = ["("]': "limit_patterns",
            'limit_patterns = "x"': "limit_patterns",
        }
        for line, needle in cases.items():
            with self.subTest(line=line):
                base = (
                    no_command if line.startswith("command") else self.CLI_SUBSCRIPTION
                )
                self.assertRejected(base + "\n" + line + "\n", needle)

    def test_cli_rejects_unknown_placeholders(self):
        self.assertRejected(
            self.CLI_SUBSCRIPTION.replace('"-"]', '"{prompt}"]'),
            "command",
            "unknown placeholder",
            "{prompt}",
        )
        self.assertRejected(
            self.CLI_SUBSCRIPTION.replace('"-"]', '"{env.HOME}"]'),
            "placeholder",
            "{env.HOME}",
        )

    def test_cli_prompt_via_file_requires_prompt_file_placeholder(self):
        self.assertRejected(
            self.CLI_SUBSCRIPTION + 'prompt_via = "file"\n',
            "prompt_via",
            "{prompt_file}",
        )
        # stdin and arg transport must not name {prompt_file}
        self.assertRejected(
            self.CLI_SUBSCRIPTION.replace('"-"]', '"{prompt_file}"]'),
            "command",
            "prompt_via",
        )
        self.assertRejected(
            self.CLI_SUBSCRIPTION.replace('"-"]', '"{prompt_file}"]')
            + 'prompt_via = "arg"\n',
            "prompt_via",
        )

    def test_cli_rejects_api_loop_keys_set_explicitly(self):
        for key, value in {
            "base_url": '"https://api.openai.com/v1"',
            "max_turns": "5",
            "max_cost_usd": "1.0",
            "max_context": "1000",
            "retries": "1",
            "retry_base_seconds": "0",
            "request_timeout_seconds": "5",
            "command_timeout_seconds": "5",
            "temperature": "0.5",
            "max_output_tokens": "100",
            "max_tokens_param": '"max_completion_tokens"',
            "extra_body": "{}",
            "post_edit": '["true"]',
        }.items():
            with self.subTest(key=key):
                self.assertRejected(
                    self.CLI_SUBSCRIPTION + f"{key} = {value}\n", key, '"cli"'
                )

    def test_cli_api_billing_requires_api_key_env(self):
        self.assertRejected(
            self.CLI_API.replace('api_key_env = "CODEX_API_KEY"\n', "").replace(
                'pass_env = ["CODEX_API_KEY"]\n', ""
            ),
            "api_key_env",
            "api",
        )
        # a subscription or local cli profile must not name a key variable
        self.assertRejected(
            self.CLI_SUBSCRIPTION + 'api_key_env = "CODEX_API_KEY"\n',
            "api_key_env",
            "subscription",
        )
        self.project(self.CLI_SUBSCRIPTION.replace('"subscription"', '"local"'))
        self.assertRejected(
            self.CLI_SUBSCRIPTION.replace('"subscription"', '"local"')
            + 'api_key_env = "CODEX_API_KEY"\n',
            "api_key_env",
        )

    def test_api_profile_rejects_cli_only_keys(self):
        for key, value in {
            "command": '["codex"]',
            "timeout_seconds": "60",
            "prompt_via": '"file"',
            "usage_format": '"codex-jsonl"',
            "pass_env": '["CODEX_API_KEY"]',
            "limit_patterns": '["x"]',
        }.items():
            with self.subTest(key=key):
                self.assertRejected(BASE + f"{key} = {value}\n", key, '"cli"')

    def test_cli_project_overrides_user_per_key(self):
        self.user(
            self.CLI_SUBSCRIPTION.replace(
                '"--json", "-"]', '"--json", "-o", "{last_message_file}", "-"]'
            )
            + 'timeout_seconds = 111\nusage_format = "codex-jsonl"\n'
        )
        self.project('[profiles.codex]\ntimeout_seconds = 222\nprompt_via = "stdin"\n')
        profile = models.load(self.repo)[0]["codex"]
        self.assertEqual(profile["timeout_seconds"], 222)  # project wins
        self.assertEqual(profile["usage_format"], "codex-jsonl")  # kept from user
        self.assertEqual(profile["prompt_via"], "stdin")  # project wins
        self.assertIn("{last_message_file}", profile["command"][-2])  # kept from user

    # ------------------------------------------------------------- read_only_command

    READ_ONLY_LINE = (
        'read_only_command = ["codex", "exec", "--sandbox", "read-only", "--json", '
        '"-o", "{last_message_file}", "-"]\n'
    )
    READ_ONLY_CODEX = (
        'command = ["codex", "exec", "--sandbox", "workspace-write", "--json", "-"]\n'
        + READ_ONLY_LINE
    )

    def cli_profile(self, extra=""):
        return (
            "[profiles.codex]\n"
            'driver = "cli"\n'
            'model = "gpt-5.2-codex"\n'
            'billing = "subscription"\n'
            'command = ["codex", "exec", "--json", "-"]\n' + extra
        )

    def test_read_only_command_verified_codex_loads(self):
        self.project(self.cli_profile(self.READ_ONLY_LINE))
        profile = models.load(self.repo)[0]["codex"]
        self.assertEqual(
            profile["read_only_command"],
            [
                "codex",
                "exec",
                "--sandbox",
                "read-only",
                "--json",
                "-o",
                "{last_message_file}",
                "-",
            ],
        )
        self.assertIsNone(models.verify_read_only_command(profile["read_only_command"]))
        # the long flag forms work too
        self.assertIsNone(
            models.verify_read_only_command(
                [
                    "codex",
                    "exec",
                    "--sandbox=read-only",
                    "--output-last-message",
                    "{last_message_file}",
                    "-",
                ]
            )
        )

    def test_read_only_command_rejects_unsafe_or_unverified_forms(self):
        base = self.cli_profile()
        cases = {
            # no explicit read-only sandbox
            'read_only_command = ["codex", "exec", "--json", "-o", "{last_message_file}", "-"]': "sandbox",
            # a workspace-write sandbox
            'read_only_command = ["codex", "exec", "--sandbox", "workspace-write", '
            '"-o", "{last_message_file}", "-"]': "not read-only",
            # full-auto / approval bypass / config override
            'read_only_command = ["codex", "exec", "--sandbox", "read-only", "--full-auto", '
            '"-o", "{last_message_file}", "-"]': "conflicts",
            'read_only_command = ["codex", "exec", "--sandbox", "read-only", '
            '"--dangerously-bypass-approvals-and-sandbox", "-o", "{last_message_file}", "-"]': "conflicts",
            'read_only_command = ["codex", "exec", "--sandbox", "read-only", "-c", '
            '"sandbox = \\"workspace-write\\"", "-o", "{last_message_file}", "-"]': "conflicts",
            # no host-captured final message
            'read_only_command = ["codex", "exec", "--sandbox", "read-only", "-"]': "last_message_file",
            # unverified CLI adapter
            'read_only_command = ["aider", "--sandbox", "read-only", "-o", '
            '"{last_message_file}", "-"]': "unverified CLI adapter",
            # not exec
            'read_only_command = ["codex", "chat", "--sandbox", "read-only", "-o", '
            '"{last_message_file}", "-"]': "exec",
            # unknown placeholder and shell metacharacter
            'read_only_command = ["codex", "exec", "--sandbox", "read-only", "-o", '
            '"{last_message_file}", "{prompt}"]': "unknown placeholder",
            'read_only_command = ["codex", "exec", "--sandbox", "read-only", "-o", '
            '"{last_message_file}", "a>b"]': "metacharacter",
        }
        for line, needle in cases.items():
            with self.subTest(line=line):
                self.assertRejected(base + line + "\n", needle)

    def test_read_only_command_is_cli_only(self):
        self.assertRejected(
            BASE + 'read_only_command = ["codex", "exec", "--sandbox", "read-only", '
            '"-o", "{last_message_file}", "-"]\n',
            "read_only_command",
            '"cli"',
        )

    # ------------------------------------------------------------- resolve_role_profile

    def test_resolve_role_profile_claude_and_script(self):
        self.project(BASE + '[roles]\nchecker = "script"\n')
        claude = models.resolve_role_profile(self.repo, "planner")
        self.assertEqual(claude["kind"], "claude")
        self.assertEqual(claude["method"], "claude")
        self.assertEqual(claude["billing"], "subscription (Claude Code)")
        script = models.resolve_role_profile(self.repo, "checker")
        self.assertEqual(script["kind"], "script")
        self.assertEqual(script["method"], "script")
        with self.assertRaises(models.ConfigError):
            models.resolve_role_profile(self.repo, "nope")

    def test_resolve_role_profile_override_rules(self):
        self.project(BASE)
        # an override must name an enabled configured profile
        picked = models.resolve_role_profile(self.repo, "planner", override="mistral")
        self.assertEqual(picked["kind"], "profile")
        self.assertEqual(picked["profile"], "mistral")
        self.assertEqual(picked["method"], "api")
        self.assertEqual(picked["billing"], "api")
        with self.assertRaises(models.ConfigError) as ctx:
            models.resolve_role_profile(self.repo, "planner", override="nope")
        self.assertIn("unknown profile", str(ctx.exception))
        with self.assertRaises(models.ConfigError) as ctx:
            models.resolve_role_profile(self.repo, "planner", override="claude:opus")
        self.assertIn("not a profile name", str(ctx.exception))
        with self.assertRaises(models.ConfigError) as ctx:
            models.resolve_role_profile(self.repo, "planner", override="script")
        self.assertIn("not a profile name", str(ctx.exception))
        # a free-form model or command is never accepted as an override
        with self.assertRaises(models.ConfigError):
            models.resolve_role_profile(self.repo, "planner", override="gpt-9")
        # reviewer may not name a profile, configured or overridden
        with self.assertRaises(models.ConfigError) as ctx:
            models.resolve_role_profile(self.repo, "reviewer", override="mistral")
        self.assertIn("may name a profile", str(ctx.exception))
        # a disabled profile is refused
        self.project(BASE + "enabled = false\n")
        with self.assertRaises(models.ConfigError) as ctx:
            models.resolve_role_profile(self.repo, "planner", override="mistral")
        self.assertIn("disabled", str(ctx.exception))

    def test_resolve_role_profile_producer_needs_read_only_command(self):
        cli = self.cli_profile()
        codex2 = (
            "[profiles.codex2]\n"
            'driver = "cli"\nmodel = "m"\nbilling = "subscription"\n'
            'command = ["codex", "exec", "-"]\n'
        )
        self.project(cli + '[roles]\nplanner = "codex"\n')
        with self.assertRaises(models.ConfigError) as ctx:
            models.resolve_role_profile(self.repo, "planner")
        self.assertIn("read_only_command", str(ctx.exception))
        # a fallback in the chain needs one too
        self.project(
            cli
            + self.READ_ONLY_LINE
            + 'fallback = ["codex2"]\n'
            + codex2
            + '[roles]\nplanner = "codex"\n'
        )
        with self.assertRaises(models.ConfigError) as ctx:
            models.resolve_role_profile(self.repo, "planner")
        self.assertIn("codex2", str(ctx.exception))
        self.assertIn("read_only_command", str(ctx.exception))
        # with both set, the cli producer role resolves
        self.project(
            cli
            + self.READ_ONLY_LINE
            + 'fallback = ["codex2"]\n'
            + codex2
            + self.READ_ONLY_LINE
            + '[roles]\nplanner = "codex"\n'
        )
        picked = models.resolve_role_profile(self.repo, "planner")
        self.assertEqual(picked["method"], "cli")
        self.assertEqual(picked["fallback"], ["codex2"])
        # the implementer may use the same profile without read_only_command
        self.project(cli + '[roles]\nimplementer = "codex"\n')
        picked = models.resolve_role_profile(self.repo, "implementer")
        self.assertEqual(picked["kind"], "profile")
        self.assertEqual(picked["method"], "cli")

    def test_select_cli_subcommand(self):
        self.project(BASE + '[roles]\nplanner = "mistral"\n')
        done = self.run_cli("select", "planner", "--project", str(self.repo))
        self.assertEqual(done.returncode, 0, done.stderr)
        payload = json.loads(done.stdout)
        self.assertEqual(payload["kind"], "profile")
        self.assertEqual(payload["profile"], "mistral")
        done = self.run_cli(
            "select", "planner", "--override", "nope", "--project", str(self.repo)
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn("unknown profile", done.stderr)

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
