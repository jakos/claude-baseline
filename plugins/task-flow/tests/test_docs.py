# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for the docs/ tree: pages, stubs, links, anchors, code-block counts.

Run: uv run --script plugins/task-flow/tests/test_docs.py

The split that created docs/ moved the detailed sections of README.md,
plugins/task-flow/README.md and plugins/task-flow/bench/README.md verbatim; each
moved heading stays in its source file with a "Moved to ..." stub line, so existing
anchors keep working. This test pins the result: every page exists and is indexed,
every mapped heading occurs exactly once on its target page, every source heading is
mapped or kept, every stub links to its page, every relative link and anchor resolves,
and the fenced code blocks are conserved. A later task that adds or removes a
(non-Mermaid) code block under docs/ or in a README updates BLOCKS_AFTER in the same
change; one that moves a heading updates MOVES and KEPT.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCES = [
    "README.md",
    "plugins/task-flow/README.md",
    "plugins/task-flow/bench/README.md",
]
PAGES = [
    "docs/README.md",
    "docs/dev-baseline.md",
    "docs/development.md",
    "docs/task-flow/overview.md",
    "docs/task-flow/setup.md",
    "docs/task-flow/commands.md",
    "docs/task-flow/autopilot.md",
    "docs/task-flow/configuration.md",
    "docs/task-flow/external-implementer.md",
    "docs/task-flow/gates.md",
    "docs/task-flow/enforcement.md",
    "docs/task-flow/profiles.md",
    "docs/task-flow/releasing.md",
    "docs/task-flow/benchmark.md",
]
SCOPE = SOURCES + ["GETTING-STARTED.md"] + PAGES
PLUGIN_README = "plugins/task-flow/README.md"
BENCH_README = "plugins/task-flow/bench/README.md"

# (source, heading text, target page) — every heading the split moved.
MOVES = [
    ("README.md", "Install once, globally", "docs/dev-baseline.md"),
    ("README.md", "What's in it", "docs/dev-baseline.md"),
    ("README.md", "Skills", "docs/dev-baseline.md"),
    ("README.md", "Agent", "docs/dev-baseline.md"),
    ("README.md", "Hooks", "docs/dev-baseline.md"),
    ("README.md", "Template", "docs/dev-baseline.md"),
    ("README.md", "Evals", "docs/dev-baseline.md"),
    ("README.md", "These are opinions, not truth", "docs/dev-baseline.md"),
    ("README.md", "Companions worth installing", "docs/dev-baseline.md"),
    ("README.md", "Working on this repo", "docs/development.md"),
    ("README.md", "Reviewing this repo itself", "docs/development.md"),
    ("README.md", "Layout", "docs/development.md"),
    (PLUGIN_README, "Install", "docs/task-flow/setup.md"),
    (PLUGIN_README, "Permissions", "docs/task-flow/setup.md"),
    (PLUGIN_README, "Doctor", "docs/task-flow/setup.md"),
    (PLUGIN_README, "Commands", "docs/task-flow/commands.md"),
    (PLUGIN_README, "Autopilot", "docs/task-flow/autopilot.md"),
    (PLUGIN_README, "Releasing", "docs/task-flow/releasing.md"),
    (PLUGIN_README, "What enforces what", "docs/task-flow/enforcement.md"),
    (PLUGIN_README, "External implementer", "docs/task-flow/external-implementer.md"),
    (PLUGIN_README, "Routing", "docs/task-flow/external-implementer.md"),
    (PLUGIN_README, "Providers", "docs/task-flow/external-implementer.md"),
    (
        PLUGIN_README,
        "Configuration: `models.toml`",
        "docs/task-flow/configuration.md",
    ),
    (
        PLUGIN_README,
        "Post-edit feedback and prompt extras",
        "docs/task-flow/external-implementer.md",
    ),
    (
        PLUGIN_README,
        "Choosing Claude models per role",
        "docs/task-flow/configuration.md",
    ),
    (
        PLUGIN_README,
        "What it does and records",
        "docs/task-flow/external-implementer.md",
    ),
    (PLUGIN_README, "Limits, stated plainly", "docs/task-flow/external-implementer.md"),
    (PLUGIN_README, "Rolling back", "docs/task-flow/external-implementer.md"),
    (
        PLUGIN_README,
        "Deterministic gates (scripts/gates.py)",
        "docs/task-flow/gates.md",
    ),
    (PLUGIN_README, "Benchmark", "docs/task-flow/benchmark.md"),
    (PLUGIN_README, "Project configuration", "docs/task-flow/configuration.md"),
    (PLUGIN_README, "Profiles", "docs/task-flow/profiles.md"),
    (PLUGIN_README, "Adding a profile", "docs/task-flow/profiles.md"),
    (PLUGIN_README, "Layout", "docs/task-flow/overview.md"),
    (
        BENCH_README,
        "Re-running, adding a model and publishing",
        "docs/task-flow/benchmark.md",
    ),
]
# (source, heading text) — every heading that stays in its source file.
KEPT = [
    ("README.md", "claude-baseline"),
    ("README.md", "Second plugin: task-flow"),
    ("README.md", "License"),
    (PLUGIN_README, "task-flow"),
    (BENCH_README, "Benchmark harness (`bench.py`)"),
    (BENCH_README, "Cases"),
    (BENCH_README, "Hidden-test rules"),
    (BENCH_README, "Adding a case"),
    (BENCH_README, "Commands"),
    (BENCH_README, "Running a first pass"),
    (BENCH_README, "Reading the outputs"),
    (BENCH_README, "When to re-run"),
    (BENCH_README, "Adding a profile"),
    (BENCH_README, "Run layout and outputs"),
    (BENCH_README, "Isolation and safety"),
    (BENCH_README, "Repeats, medians and spread"),
    (BENCH_README, "Re-running over time and the suite fingerprint"),
    (BENCH_README, "Durability"),
    (BENCH_README, "Limits, stated plainly"),
]
# Fenced code blocks (info string "mermaid" excluded), measured on the base and after
# the split; a later task that adds or removes one updates BLOCKS_AFTER here.
BLOCKS_BEFORE = {
    "README.md": 10,
    PLUGIN_README: 25,
    BENCH_README: 9,
}
BLOCKS_AFTER = {
    "README.md": 1,
    PLUGIN_README: 0,
    BENCH_README: 8,
    "docs/README.md": 0,
    "docs/dev-baseline.md": 6,
    "docs/development.md": 3,
    "docs/task-flow/overview.md": 2,
    "docs/task-flow/setup.md": 3,
    "docs/task-flow/commands.md": 0,
    "docs/task-flow/autopilot.md": 0,
    "docs/task-flow/configuration.md": 2,
    "docs/task-flow/external-implementer.md": 13,
    "docs/task-flow/gates.md": 3,
    "docs/task-flow/enforcement.md": 0,
    "docs/task-flow/profiles.md": 0,
    "docs/task-flow/releasing.md": 1,
    "docs/task-flow/benchmark.md": 2,
}

FENCE = re.compile(r"\s*(`{3,}|~{3,})\s*([^`\s]*)")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*$")
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]*)\)")
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
SECRET = re.compile(r"sk-[A-Za-z0-9]{16,}")
PERSONAL_PATHS = ("C:\\Users\\", "C:/Users/", "/Users/", "/home/", "-----BEGIN")


def strip_code(text):
    """Blank out fenced blocks and inline code spans; keep everything else."""
    out = []
    fence = None
    for line in text.splitlines():
        m = FENCE.match(line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
                out.append("")
                continue
            out.append(re.sub(r"`[^`]*`", "", line))
        else:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
                fence = None
            out.append("")
    return "\n".join(out)


def heading_spans(text):
    """[(line index, level, text)] of headings outside fenced code, raw text kept."""
    result = []
    raw_lines = text.splitlines()
    for i, blanked in enumerate(strip_code(text).splitlines()):
        if blanked == "" and raw_lines[i].strip() != "":
            continue  # inside a fenced block
        m = HEADING.match(raw_lines[i])
        if m:
            result.append((i, len(m.group(1)), m.group(2)))
    return result


def headings(text):
    """All headings outside fenced code as (level, text), in file order."""
    return [(level, h) for _i, level, h in heading_spans(text)]


def slug(heading, seen=None):
    """GitHub's anchor slug; with `seen`, repeated headings get -1, -2, ..."""
    s = re.sub(r"[^\w\s-]", "", heading.lower()).replace(" ", "-")
    if seen is None:
        return s
    n = seen.get(s, 0)
    seen[s] = n + 1
    return s if n == 0 else f"{s}-{n}"


def links(text):
    """Targets of inline links and images outside code, in file order."""
    targets = []
    for line in strip_code(text).splitlines():
        for m in LINK.finditer(line):
            targets.append(m.group(1))
    return targets


def count_blocks(text):
    """Fenced code blocks outside other fences, at any indentation; mermaid excluded."""
    count = 0
    fence = None
    for line in text.splitlines():
        m = FENCE.match(line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
                if m.group(2) != "mermaid":
                    count += 1
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
            fence = None
    return count


class DocsTest(unittest.TestCase):
    def setUp(self):
        if not (ROOT / ".claude-plugin" / "marketplace.json").is_file():
            self.skipTest("not the claude-baseline repository (installed plugin copy?)")

    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def heading_lines(self, rel):
        """[(line index, level, text)] of headings outside code."""
        return heading_spans(self.read(rel))

    # --- AC1: pages exist and the index lists them -----------------------------------

    def test_pages_exist_and_index_lists_them(self):
        for page in PAGES:
            self.assertTrue((ROOT / page).is_file(), f"missing page {page}")
        index_links = [t.split("#", 1)[0] for t in links(self.read("docs/README.md"))]
        for page in PAGES:
            if page == "docs/README.md":
                continue
            self.assertIn(page[len("docs/") :], index_links, f"index lacks {page}")

    # --- AC2: every moved heading occurs exactly once on its target ------------------

    def test_moved_headings_once_in_target(self):
        for source, heading, target in MOVES:
            texts = [h for _level, h in headings(self.read(target))]
            self.assertEqual(
                texts.count(heading), 1, f"{heading!r} not exactly once in {target}"
            )

    # --- AC3: every source heading is mapped or kept ----------------------------------

    def test_every_source_heading_is_mapped(self):
        for source in SOURCES:
            mapped = {h for s, h, _t in MOVES if s == source}
            kept = {h for s, h in KEPT if s == source}
            for _level, heading in headings(self.read(source)):
                self.assertIn(
                    heading,
                    mapped | kept,
                    f"{source}: unmapped heading {heading!r}",
                )

    # --- AC4: the old place keeps a stub linking to the target ------------------------

    def test_old_place_has_stub(self):
        for source, heading, target in MOVES:
            raw_lines = self.read(source).splitlines()
            found = [
                i for i, _level, h in heading_spans(self.read(source)) if h == heading
            ]
            self.assertEqual(len(found), 1, f"{source}: heading {heading!r} not once")
            j = found[0] + 1
            while j < len(raw_lines) and not raw_lines[j].strip():
                j += 1
            stub = raw_lines[j]
            self.assertTrue(stub.startswith("Moved to ["), f"{source}: {stub!r}")
            target_link = links(stub)[0]
            path, _, anchor = target_link.partition("#")
            resolved = ((ROOT / source).parent / path).resolve()
            self.assertEqual(resolved, (ROOT / target).resolve(), stub)
            self.assertEqual(anchor, slug(heading), stub)
        # the plugin README intro is a stub under `# task-flow`
        raw_lines = self.read(PLUGIN_README).splitlines()
        title = next(i for i, line in enumerate(raw_lines) if line == "# task-flow")
        j = title + 1
        while j < len(raw_lines) and not raw_lines[j].strip():
            j += 1
        stub = raw_lines[j]
        self.assertTrue(stub.startswith("Moved to ["), stub)
        path = links(stub)[0].partition("#")[0]
        self.assertEqual(
            ((ROOT / PLUGIN_README).parent / path).resolve(),
            (ROOT / "docs/task-flow/overview.md").resolve(),
            stub,
        )

    # --- AC5: relative links resolve ---------------------------------------------------

    def test_relative_links_resolve(self):
        for rel in SCOPE:
            base = (ROOT / rel).parent
            for target in links(self.read(rel)):
                if SCHEME.match(target):
                    continue
                path = target.partition("#")[0]
                if not path:
                    continue
                resolved = (base / path).resolve()
                self.assertTrue(resolved.exists(), f"{rel}: broken link {target}")

    # --- AC6: anchors match a heading slug of the target ------------------------------

    def test_anchors_resolve(self):
        for rel in SCOPE:
            base = (ROOT / rel).parent
            for target in links(self.read(rel)):
                if SCHEME.match(target):
                    continue
                path, _, anchor = target.partition("#")
                if not anchor:
                    continue
                dest = base / path if path else ROOT / rel
                if dest.suffix != ".md" or not dest.is_file():
                    continue
                seen = {}
                slugs = {
                    slug(h, seen) for _level, h in headings(dest.read_text("utf-8"))
                }
                self.assertIn(anchor, slugs, f"{rel}: broken anchor {target}")

    # --- AC7: fenced block counts ------------------------------------------------------

    def test_fenced_block_counts(self):
        for rel in SOURCES + PAGES:
            self.assertEqual(
                count_blocks(self.read(rel)), BLOCKS_AFTER[rel], f"block count {rel}"
            )
        self.assertEqual(sum(BLOCKS_AFTER.values()), sum(BLOCKS_BEFORE.values()))

    # --- AC8: helpers ------------------------------------------------------------------

    def test_slug_matches_github(self):
        self.assertEqual(
            slug("Configuration: `models.toml`"), "configuration-modelstoml"
        )
        self.assertEqual(slug("What's in it"), "whats-in-it")
        self.assertEqual(
            slug("Deterministic gates (scripts/gates.py)"),
            "deterministic-gates-scriptsgatespy",
        )
        seen = {}
        self.assertEqual(slug("Repeats", seen), "repeats")
        self.assertEqual(slug("Repeats", seen), "repeats-1")
        self.assertEqual(slug("Repeats", seen), "repeats-2")

    def test_code_is_ignored_and_indented_fences_counted(self):
        text = "```\n# not a heading\n[a](b.md)\n```\n# Real\n`[c](d.md)`\n[e](f.md)\n"
        self.assertEqual(headings(text), [(1, "Real")])
        self.assertEqual(links(text), ["f.md"])
        indented = "- item\n  ```\n  code\n  ```\n```\ntop\n```\n"
        self.assertEqual(count_blocks(indented), 2)
        self.assertEqual(count_blocks("```mermaid\ngraph\n```\n"), 0)

    # --- AC9: benchmark history markers -------------------------------------------------

    def test_benchmark_history_markers(self):
        publish = self.read("plugins/task-flow/bench/publish.py")

        def marker(name):
            m = re.search(rf'^{name} = "([^"]+)"', publish, re.MULTILINE)
            self.assertIsNotNone(m, f"{name} not found in publish.py")
            return m.group(1)

        start, end = marker("HISTORY_START"), marker("HISTORY_END")
        page = "docs/task-flow/benchmark.md"
        text = self.read(page)
        lines = text.splitlines()
        # exactly once outside code (the moved prose mentions them in inline code),
        # and alone on their own line
        self.assertEqual(strip_code(text).count(start), 1)
        self.assertEqual(strip_code(text).count(end), 1)
        starts = [i for i, line in enumerate(lines) if line.strip() == start]
        ends = [i for i, line in enumerate(lines) if line.strip() == end]
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(ends), 1)
        self.assertLess(starts[0], ends[0])
        rerun = [
            i
            for i, _level, h in self.heading_lines(page)
            if h == "Re-running, adding a model and publishing"
        ]
        self.assertEqual(len(rerun), 1)
        self.assertLess(
            rerun[0], starts[0], "Re-running section must sit outside markers"
        )

    # --- AC11: no personal paths or secrets in docs/ -------------------------------------

    def test_no_personal_paths_or_secrets(self):
        for page in PAGES:
            text = self.read(page)
            for pattern in PERSONAL_PATHS:
                self.assertNotIn(pattern, text, f"{page}: contains {pattern!r}")
            self.assertIsNone(
                SECRET.search(text), f"{page}: matches a secret-looking string"
            )


if __name__ == "__main__":
    unittest.main()
