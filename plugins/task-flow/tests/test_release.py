# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/release.py: unit tests for the pure core, git-level tests.

Run: uv run --script plugins/task-flow/tests/test_release.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "release.py"
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "release.toml"
sys.path.insert(0, str(SCRIPTS))
import release


def toml_str(value: str) -> str:
    """A TOML basic string for generated config text (JSON escaping is valid TOML)."""
    return json.dumps(value)


class ReleaseCoreTest(unittest.TestCase):
    """Unit tests for the pure core; temp directories only, no git."""

    def commit(self, subject, body="", sha="aaaaaaa1111"):
        return release.parse_commit(sha, subject, body)

    def test_parse_version_rejects_non_semver(self):
        self.assertEqual(release.parse_version("1.2.3"), (1, 2, 3))
        self.assertEqual(release.parse_version("0.10.0"), (0, 10, 0))
        for bad in (
            "1.2",
            "1.2.3.4",
            "v1.2.3",
            "1.2.x",
            "1.0.0-rc.1",
            "",
            "1.2.3 ",
            "1..3",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                release.parse_version(bad)

    def test_bump_kinds(self):
        self.assertEqual(release.bump("1.2.3", "major"), "2.0.0")
        self.assertEqual(release.bump("1.2.3", "minor"), "1.3.0")
        self.assertEqual(release.bump("1.2.3", "patch"), "1.2.4")
        self.assertEqual(release.bump("0.10.0", "major"), "1.0.0")
        with self.assertRaises(ValueError):
            release.bump("1.2.3", "nope")

    def test_propose_explicit_version(self):
        self.assertEqual(
            release.propose_version("1.2.3", [], explicit="2.0.0"),
            ("2.0.0", "explicit"),
        )
        with self.assertRaises(ValueError):
            release.propose_version("1.2.3", [], explicit="2.0")
        with self.assertRaises(ValueError):
            release.propose_version("1.2.3", [], explicit="1.0.0-rc.1")

    def test_propose_flags(self):
        self.assertEqual(
            release.propose_version("1.2.3", [], kind="major"), ("2.0.0", "major")
        )
        self.assertEqual(
            release.propose_version("1.2.3", [], kind="minor"), ("1.3.0", "minor")
        )
        self.assertEqual(
            release.propose_version("1.2.3", [], kind="patch"), ("1.2.4", "patch")
        )

    def test_infer_feat_is_minor(self):
        commits = [self.commit("feat: add x"), self.commit("fix: y")]
        self.assertEqual(release.infer_bump("1.2.3", commits), "minor")
        self.assertEqual(release.propose_version("1.2.3", commits), ("1.3.0", "minor"))

    def test_infer_fix_is_patch(self):
        self.assertEqual(release.infer_bump("1.2.3", [self.commit("fix: y")]), "patch")

    def test_infer_other_types_patch(self):
        commits = [
            self.commit("docs: x"),
            self.commit("chore: y"),
            self.commit("plain subject"),
        ]
        self.assertEqual(release.infer_bump("1.2.3", commits), "patch")

    def test_infer_breaking_is_major(self):
        self.assertEqual(
            release.infer_bump("1.2.3", [self.commit("feat!: drop x")]), "major"
        )
        self.assertEqual(
            release.infer_bump("1.2.3", [self.commit("feat(api)!: x")]), "major"
        )

    def test_infer_breaking_body_is_major(self):
        commits = [self.commit("feat: x", "some body\n\nBREAKING CHANGE: drops y")]
        self.assertEqual(release.infer_bump("2.0.0", commits), "major")
        commits = [self.commit("refactor: x", "BREAKING-CHANGE: drops y")]
        self.assertEqual(release.infer_bump("2.0.0", commits), "major")

    def test_infer_breaking_below_1_is_minor(self):
        self.assertEqual(
            release.infer_bump("0.10.0", [self.commit("feat!: drop x")]), "minor"
        )
        commits = [self.commit("fix: x", "BREAKING CHANGE: y")]
        self.assertEqual(release.infer_bump("0.10.0", commits), "minor")

    def test_render_entry_groups_by_type(self):
        commits = [
            self.commit("feat: new thing", sha="aaaaaaa1111"),
            self.commit("fix: repair", sha="bbbbbbb2222"),
            self.commit("feat!: drop old", sha="ccccccc3333"),
            self.commit("docs: explain", sha="ddddddd4444"),
        ]
        entry = release.render_entry("1.3.0", "2026-11-01", commits)
        expected = (
            "## 1.3.0 - 2026-11-01\n"
            "\n"
            "### Breaking changes\n"
            "\n"
            "- feat!: drop old (ccccccc)\n"
            "\n"
            "### Features\n"
            "\n"
            "- feat: new thing (aaaaaaa)\n"
            "\n"
            "### Fixes\n"
            "\n"
            "- fix: repair (bbbbbbb)\n"
            "\n"
            "### Other\n"
            "\n"
            "- docs: explain (ddddddd)\n"
        )
        self.assertEqual(entry, expected)

    def test_prepend_creates_file_with_title(self):
        entry = "## 1.3.0 - 2026-11-01\n\n- x (aaaaaaa)\n"
        self.assertEqual(release.prepend_entry(None, entry), "# Changelog\n\n" + entry)

    def test_prepend_after_title(self):
        entry = "## 1.3.0 - 2026-11-01\n\n- x (aaaaaaa)\n"
        existing = "# Changelog\n\n## 1.0.0 - 2026-01-01\n\n- old (bbbbbbb)\n"
        self.assertEqual(
            release.prepend_entry(existing, entry),
            "# Changelog\n\n"
            + entry
            + "\n"
            + "## 1.0.0 - 2026-01-01\n\n- old (bbbbbbb)\n",
        )
        self.assertEqual(
            release.prepend_entry("# Changelog\n", entry), "# Changelog\n\n" + entry
        )
        self.assertEqual(
            release.prepend_entry("# Changelog", entry), "# Changelog\n\n" + entry
        )

    def test_prepend_without_title(self):
        entry = "## 1.3.0 - 2026-11-01\n"
        existing = "Some text\n"
        self.assertEqual(
            release.prepend_entry(existing, entry), entry + "\n" + existing
        )

    def test_json_selector_with_filter(self):
        data = {
            "plugins": [
                {"name": "a", "version": "0.1.0"},
                {"name": "b", "version": "0.2.0"},
            ]
        }
        text = json.dumps(data, indent=2) + "\n"
        vf = {
            "path": "marketplace.json",
            "format": "json",
            "selector": "plugins[name=b].version",
        }
        self.assertEqual(
            release.get_selector(json.loads(text), vf["selector"]), "0.2.0"
        )
        new = release.rewrite_version(text, vf, "0.2.0", "0.3.0")
        updated = json.loads(new)
        self.assertEqual(updated["plugins"][0]["version"], "0.1.0")
        self.assertEqual(updated["plugins"][1]["version"], "0.3.0")
        with self.assertRaises(ValueError):
            release.rewrite_version(text, vf, "9.9.9", "0.3.0")

    def test_json_package_json(self):
        text = '{\n  "name": "x",\n  "version": "1.2.3",\n  "private": true\n}\n'
        vf = {"path": "package.json", "format": "json", "selector": "version"}
        new = release.rewrite_version(text, vf, "1.2.3", "1.3.0")
        self.assertEqual(
            new, '{\n  "name": "x",\n  "version": "1.3.0",\n  "private": true\n}\n'
        )

    def test_json_crlf_keeps_crlf_everywhere(self):
        text = '{\r\n  "name": "x",\r\n  "version": "1.2.3"\r\n}\r\n'
        vf = {"path": "package.json", "format": "json", "selector": "version"}
        new = release.rewrite_version(text, vf, "1.2.3", "1.3.0")
        self.assertEqual(new, '{\r\n  "name": "x",\r\n  "version": "1.3.0"\r\n}\r\n')
        self.assertNotIn("\n", new.replace("\r\n", ""))

    def test_toml_table_key_replacement(self):
        text = (
            "[project]\n"
            'name = "x"\n'
            'version = "1.2.3"\n'
            "\n"
            "[tool.other]\n"
            'version = "9.9.9"\n'
        )
        vf = {"path": "pyproject.toml", "format": "toml", "selector": "project.version"}
        new = release.rewrite_version(text, vf, "1.2.3", "1.3.0")
        self.assertEqual(
            new,
            "[project]\n"
            'name = "x"\n'
            'version = "1.3.0"\n'
            "\n"
            "[tool.other]\n"
            'version = "9.9.9"\n',
        )

    def test_toml_replacement_must_match_once(self):
        vf = {"path": "pyproject.toml", "format": "toml", "selector": "project.version"}
        quoted_key = '[project]\n"version" = "1.2.3"\n'
        with self.assertRaises(ValueError) as caught:
            release.rewrite_version(quoted_key, vf, "1.2.3", "1.3.0")
        self.assertIn("toml-replace", str(caught.exception))
        missing_header = 'version = "1.2.3"\n'
        with self.assertRaises(ValueError):
            release.rewrite_version(missing_header, vf, "1.2.3", "1.3.0")

    def test_config_unknown_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "release.toml"
            path.write_text(
                "[targets.x]\n"
                'tag = "x-{version}"\n'
                'changelog = "C.md"\n'
                'scope = ["a"]\n'
                "bogus = 1\n"
                "[[targets.x.version_files]]\n"
                'path = "p.json"\n'
                'format = "json"\n'
                'selector = "version"\n',
                encoding="utf-8",
            )
            with self.assertRaises(release.ConfigError):
                release.load_config(Path(tmp), path)
            path.write_text(
                "[targets.x]\n"
                'tag = "x-{version}"\n'
                'changelog = "C.md"\n'
                'scope = ["a"]\n'
                "[[targets.x.version_files]]\n"
                'path = "p.json"\n'
                'format = "json"\n'
                'selector = "version"\n'
                "extra = true\n",
                encoding="utf-8",
            )
            with self.assertRaises(release.ConfigError):
                release.load_config(Path(tmp), path)

    def test_config_missing_file_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "targets", "--root", tmp],
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(b"release.py:", result.stderr)
            self.assertNotIn(b"Traceback", result.stderr)

    def test_targets_lists_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            (root / ".claude" / "task-flow.release.toml").write_text(
                "[targets.demo]\n"
                'tag = "demo-{version}"\n'
                'changelog = "CHANGELOG.md"\n'
                'scope = ["src"]\n'
                "[[targets.demo.version_files]]\n"
                'path = "plugin.json"\n'
                'format = "json"\n'
                'selector = "version"\n',
                encoding="utf-8",
            )
            (root / "plugin.json").write_text(
                '{\n  "name": "demo",\n  "version": "1.2.3"\n}\n', encoding="utf-8"
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "targets", "--root", str(root)],
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.decode("utf-8"), "demo 1.2.3 demo-{version}\n"
            )

    def test_template_config_is_valid(self):
        config = release.load_config(Path("."), TEMPLATE)
        self.assertIn("web", config["targets"])
        self.assertIn("app", config["targets"])
        web = config["targets"]["web"]
        self.assertEqual(web["branches"], ["main"])
        self.assertEqual(web["checks"], web["checks"])
        self.assertEqual(web["version_files"][0]["format"], "json")
        app = config["targets"]["app"]
        self.assertEqual(app["version_files"][0]["format"], "toml")


class ReleaseGitTest(unittest.TestCase):
    """Git-level tests: release.py run as a subprocess against a temp repository."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.env = os.environ.copy()
        self.env["GIT_CONFIG_GLOBAL"] = str(self.base / "git-global-config")
        (self.base / "git-global-config").write_bytes(b"")
        self.env["GIT_CONFIG_NOSYSTEM"] = "1"
        self.root = self.base / "repo"
        self.root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "tag.gpgsign", "false")
        (self.root / ".work").mkdir()
        (self.root / ".work" / ".gitignore").write_text("*\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
        (self.root / "plugin.json").write_text(
            '{\n  "name": "demo",\n  "version": "1.2.3"\n}\n', encoding="utf-8"
        )
        (self.root / "marketplace.json").write_text(
            json.dumps(
                {
                    "plugins": [
                        {"name": "demo", "version": "1.2.3"},
                        {"name": "other", "version": "0.1.0"},
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.root / "package.json").write_text(
            '{\n  "name": "pkg",\n  "version": "1.2.3"\n}\n', encoding="utf-8"
        )
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1.2.3"\n\n[tool.other]\nversion = "9.9.9"\n',
            encoding="utf-8",
        )
        (self.root / ".claude").mkdir()
        self.write_config()
        (self.root / "README.md").write_text("# demo\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-m", "chore: initial")
        self.initial = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.shas = {}
        for subject, filename in (
            ("feat: add login", "src/a.py"),
            ("fix: repair login", "src/b.py"),
            ("docs: update readme", "README.md"),
        ):
            (self.root / filename).write_text(f"# {subject}\n", encoding="utf-8")
            self.git("add", "-A")
            self.git("commit", "-m", subject)
            self.shas[subject] = self.git("rev-parse", "HEAD").stdout.decode().strip()

    def git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            check=True,
        )

    def run_release(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            check=False,
        )

    def check(self, *extra):
        result = self.run_release("check", "--target", "demo", *extra)
        return result, json.loads(result.stdout.decode("utf-8"))

    def config(self, checks=None, scope=("src",)):
        lines = [
            "[targets.demo]",
            'tag = "demo-{version}"',
            'changelog = "CHANGELOG.md"',
            "scope = [" + ", ".join(toml_str(s) for s in scope) + "]",
            "checks = ["
            + ", ".join(
                "[" + ", ".join(toml_str(a) for a in check) + "]"
                for check in (checks or [])
            )
            + "]",
            "[[targets.demo.version_files]]",
            'path = "plugin.json"',
            'format = "json"',
            'selector = "version"',
            "[[targets.demo.version_files]]",
            'path = "marketplace.json"',
            'format = "json"',
            'selector = "plugins[name=demo].version"',
            "[[targets.demo.version_files]]",
            'path = "pyproject.toml"',
            'format = "toml"',
            'selector = "project.version"',
        ]
        return "\n".join(lines) + "\n"

    def write_config(self, checks=None, scope=("src",)):
        (self.root / ".claude" / "task-flow.release.toml").write_text(
            self.config(checks=checks, scope=scope), encoding="utf-8"
        )

    def codes(self, report):
        return [failure["code"] for failure in report["failures"]]

    def test_check_ok_outputs_json(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "demo 1.2.3", self.initial)
        result, report = self.check("--date", "2026-11-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["target"], "demo")
        self.assertEqual(report["branch"], "main")
        self.assertEqual(report["current"], "1.2.3")
        self.assertEqual(report["version"], "1.3.0")
        self.assertEqual(report["bump"], "minor")
        self.assertEqual(report["base"], "demo-1.2.3")
        self.assertEqual(report["commits"], 2)
        self.assertEqual(report["tag"], "demo-1.3.0")
        self.assertEqual(
            report["files"],
            ["plugin.json", "marketplace.json", "pyproject.toml", "CHANGELOG.md"],
        )
        self.assertEqual(report["changelog"], "CHANGELOG.md")
        self.assertEqual(report["commit_message"], "chore(release): demo 1.3.0")
        self.assertIn("## 1.3.0 - 2026-11-01", report["entry"])
        self.assertIn("- feat: add login (", report["entry"])
        self.assertIn("- fix: repair login (", report["entry"])
        self.assertNotIn("docs: update readme", report["entry"])
        self.assertEqual(report["failures"], [])

    def test_check_uses_last_tag_as_base(self):
        self.git("tag", "-a", "demo-1.0.0", "-m", "x", self.initial)
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", self.initial)
        _result, report = self.check()
        self.assertEqual(report["base"], "demo-1.2.3")

    def test_check_since_overrides_base(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", self.initial)
        feat_sha = self.shas["feat: add login"]
        _result, report = self.check("--since", feat_sha)
        self.assertEqual(report["base"], feat_sha)
        self.assertEqual(report["commits"], 1)

    def test_refuses_dirty_tree(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", self.initial)
        with open(self.root / "src" / "a.py", "a", encoding="utf-8") as handle:
            handle.write("# dirty\n")
        result, report = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("dirty", self.codes(report))

    def test_refuses_wrong_branch(self):
        self.git("checkout", "-b", "other")
        result, report = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["branch"], "other")
        self.assertIn("branch", self.codes(report))

    def test_refuses_existing_tag(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", self.initial)
        result, report = self.check("1.2.3")
        self.assertEqual(result.returncode, 1)
        self.assertIn("tag-exists", self.codes(report))
        self.assertIn("not-higher", self.codes(report))
        self.assertEqual(report["version"], "1.2.3")

    def test_refuses_no_commits_in_scope(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", "HEAD")
        result, report = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no-commits", self.codes(report))
        self.assertEqual(report["commits"], 0)

    def test_refuses_disagreeing_versions(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", self.initial)
        data = json.loads((self.root / "plugin.json").read_text(encoding="utf-8"))
        data["version"] = "1.2.4"
        (self.root / "plugin.json").write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
        self.git("add", "-A")
        self.git("commit", "-m", "chore: bump plugin.json only")
        result, report = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("versions-disagree", self.codes(report))
        self.assertEqual(report["current"], "disagree")

    def test_refuses_not_higher_version(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", self.initial)
        result, report = self.check("1.0.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not-higher", self.codes(report))
        self.assertEqual(report["version"], "1.0.0")

    def test_refuses_without_base_tag_or_since(self):
        result, report = self.check()
        self.assertEqual(result.returncode, 1)
        failure = next(f for f in report["failures"] if f["code"] == "no-base")
        self.assertIn("--since", failure["message"])

    def test_refuses_task_in_flight(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", self.initial)
        task = self.root / ".work" / "t1"
        task.mkdir()
        (task / "status.md").write_text(
            "# Status: t1\n- phase: implementing\n- round: 1\n## History\n## Log\n",
            encoding="utf-8",
        )
        result, report = self.check()
        self.assertEqual(result.returncode, 1)
        failure = next(f for f in report["failures"] if f["code"] == "task-in-flight")
        self.assertIn("t1", failure["message"])

    def test_verify_pass_and_fail(self):
        self.write_config(
            checks=[
                [sys.executable, "-c", "pass"],
                [sys.executable, "-c", "import sys; sys.exit(3)"],
            ]
        )
        result = self.run_release("verify", "--target", "demo")
        self.assertEqual(result.returncode, 1)
        lines = result.stdout.decode("utf-8").splitlines()
        self.assertTrue(lines[0].startswith("PASS "), lines)
        self.assertTrue(lines[1].startswith("FAIL "), lines)
        self.assertIn("(exit 3)", lines[1])
        self.assertEqual(lines[-1], "failed: 1 of 2 checks")

    def test_verify_denies_push_check(self):
        self.write_config(checks=[["git", "push", "origin", "main"]])
        result = self.run_release("verify", "--target", "demo")
        self.assertEqual(result.returncode, 1)
        out = result.stdout.decode("utf-8")
        self.assertTrue(out.startswith("DENIED git push origin main: "), out)
        self.assertEqual(out.splitlines()[-1], "failed: 1 of 1 checks")

    def test_apply_updates_files_commits_and_tags(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", self.initial)
        result = self.run_release("apply", "--target", "demo", "--date", "2026-11-01")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        out = result.stdout.decode("utf-8").splitlines()
        self.assertTrue(out[-1].startswith("ok: "), out)
        self.assertIn("chore(release): demo 1.3.0", out[-1])
        self.assertIn("tag demo-1.3.0", out[-1])
        plugin = json.loads((self.root / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], "1.3.0")
        market = json.loads(
            (self.root / "marketplace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(market["plugins"][0]["version"], "1.3.0")
        self.assertEqual(market["plugins"][1]["version"], "0.1.0")
        pyproject = (self.root / "pyproject.toml").read_text(encoding="utf-8")
        project_part, other_part = pyproject.split("[tool.other]")
        self.assertIn('version = "1.3.0"', project_part)
        self.assertIn('version = "9.9.9"', other_part)
        subject = self.git("log", "-1", "--format=%s").stdout.decode().strip()
        self.assertEqual(subject, "chore(release): demo 1.3.0")
        tag_type = self.git("cat-file", "-t", "demo-1.3.0").stdout.decode().strip()
        self.assertEqual(tag_type, "tag")
        changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertTrue(changelog.startswith("# Changelog\n\n## 1.3.0 - 2026-11-01\n"))
        self.assertIn("- feat: add login (", changelog)
        self.assertIn("- fix: repair login (", changelog)
        self.assertNotIn("docs: update readme", changelog)
        self.assertEqual(self.git("status", "--porcelain").stdout.decode().strip(), "")

    def test_apply_prepends_changelog(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", self.initial)
        (self.root / "CHANGELOG.md").write_text(
            "# Changelog\n\n## 1.0.0 - 2026-01-01\n\n- old (aaaaaaa)\n",
            encoding="utf-8",
        )
        self.git("add", "-A")
        self.git("commit", "-m", "docs: seed changelog")
        result = self.run_release("apply", "--target", "demo", "--date", "2026-11-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Changelog\n\n## 1.3.0 - 2026-11-01\n"))
        self.assertIn("\n## 1.0.0 - 2026-01-01\n", text)
        self.assertLess(text.index("## 1.3.0"), text.index("## 1.0.0"))

    def test_apply_entry_file_overrides(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", self.initial)
        entry_file = self.base / "entry.md"
        entry_file.write_text(
            "## 1.3.0 - 2026-11-01\n\n- hand-written entry", encoding="utf-8"
        )
        result = self.run_release(
            "apply",
            "--target",
            "demo",
            "--date",
            "2026-11-01",
            "--entry-file",
            str(entry_file),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("- hand-written entry\n", text)
        self.assertNotIn("- feat: add login", text)

    def test_apply_dry_run_changes_nothing(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", self.initial)
        before_head = self.git("rev-parse", "HEAD").stdout
        before_status = self.git("status", "--porcelain").stdout
        before_tags = self.git("tag", "--list").stdout
        watched = ("plugin.json", "marketplace.json", "pyproject.toml", "package.json")
        before_bytes = {name: (self.root / name).read_bytes() for name in watched}
        result = self.run_release(
            "apply", "--target", "demo", "--dry-run", "--date", "2026-11-01"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout.decode("utf-8")
        self.assertIn("would write: plugin.json: 1.2.3 -> 1.3.0", out)
        self.assertIn("would write: marketplace.json: 1.2.3 -> 1.3.0", out)
        self.assertIn("would write: pyproject.toml: 1.2.3 -> 1.3.0", out)
        self.assertIn("would write: CHANGELOG.md: create", out)
        self.assertIn("would commit: chore(release): demo 1.3.0", out)
        self.assertIn("would tag: demo-1.3.0 (annotated)", out)
        self.assertIn("ok: dry run, nothing changed", out)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout, before_head)
        self.assertEqual(self.git("status", "--porcelain").stdout, before_status)
        self.assertEqual(self.git("tag", "--list").stdout, before_tags)
        for name, blob in before_bytes.items():
            self.assertEqual((self.root / name).read_bytes(), blob)
        self.assertFalse((self.root / "CHANGELOG.md").exists())

    def test_apply_never_pushes(self):
        self.git("tag", "-a", "demo-1.2.3", "-m", "x", self.initial)
        origin = self.base / "origin.git"
        subprocess.run(
            ["git", "init", "--bare", str(origin)],
            cwd=self.base,
            env=self.env,
            capture_output=True,
            check=True,
        )
        self.git("remote", "add", "origin", str(origin))
        before = subprocess.run(
            ["git", "ls-remote", "origin"],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            check=True,
        ).stdout
        result = self.run_release("apply", "--target", "demo", "--date", "2026-11-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = subprocess.run(
            ["git", "ls-remote", "origin"],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(before, after)
        self.assertIn("demo-1.3.0", self.git("tag", "--list").stdout.decode("utf-8"))

    def test_apply_refuses_when_check_fails(self):
        head_before = self.git("rev-parse", "HEAD").stdout
        result = self.run_release("apply", "--target", "demo")
        self.assertEqual(result.returncode, 1)
        out = result.stdout.decode("utf-8")
        self.assertIn("no-base", out)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout, head_before)
        self.assertEqual(self.git("tag", "--list").stdout.decode("utf-8"), "")
        self.assertFalse((self.root / "CHANGELOG.md").exists())
        plugin = json.loads((self.root / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], "1.2.3")


if __name__ == "__main__":
    unittest.main()
