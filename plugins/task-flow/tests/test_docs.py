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
change and names its own BLOCKS_ADDED entry (or increases an existing one) —
docs-with-diagrams did that for the setup guide; one that moves a heading updates MOVES
and KEPT. The diagram helpers (mermaid_blocks, diagram_block, diagram_nodes) pin the
`<!-- diagram:... -->` markers and the Mermaid syntax subset; readme-front-page reuses
diagram_block to copy the diagrams onto the front page.
"""

import json
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

# Added by the task readme-front-page, which turned the root README into a short front
# page and retired the "Moved to ..." stubs in both READMEs.
FRONT_PAGE = "README.md"
# readme-front-page: line caps for the two READMEs; the front-page cap excludes the lines
# strictly between the bench:latest markers, because publish.py controls those.
FRONT_PAGE_MAX_LINES = 150
PLUGIN_README_MAX_LINES = 40
# readme-front-page: the bench:latest marker pair of the front page; must equal
# README_START / README_END in plugins/task-flow/bench/publish.py.
BENCH_LATEST = ("<!-- bench:latest:start -->", "<!-- bench:latest:end -->")
# readme-front-page: the two files whose MOVES rows no longer need a stub in place.
STUBS_RETIRED = (FRONT_PAGE, PLUGIN_README)
# readme-front-page: command prefix -> documentation URL of the external tool, for every
# non-task-flow slash command and code-block command line of the front-page quick start.
QUICK_START_TOOLS = {
    "/plugin marketplace add": "https://code.claude.com/docs/en/plugins/host-marketplace",
    "/plugin install": "https://code.claude.com/docs/en/plugins",
}

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
# readme-front-page retired both README stub files: for them KEPT lists exactly the new
# front page's / plugin README's own headings; MOVES stays the record of the split.
KEPT = [
    ("README.md", "claude-baseline"),
    ("README.md", "task-flow"),
    ("README.md", "Architecture"),
    ("README.md", "Task flow"),
    ("README.md", "What makes it different"),
    ("README.md", "Quick start"),
    ("README.md", "Benchmark"),
    ("README.md", "Documentation"),
    ("README.md", "dev-baseline"),
    ("README.md", "License"),
    (PLUGIN_README, "task-flow"),
    (PLUGIN_README, "Quick reference"),
    (PLUGIN_README, "Documentation"),
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
    PLUGIN_README: 1,
    BENCH_README: 8,
    "docs/README.md": 0,
    "docs/dev-baseline.md": 6,
    "docs/development.md": 3,
    "docs/task-flow/overview.md": 2,
    "docs/task-flow/setup.md": 8,
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
# Non-Mermaid fenced blocks added by later tasks, per file, on top of BLOCKS_BEFORE;
# the conservation check in test_fenced_block_counts adds them. Added by the task
# docs-with-diagrams: the five setup-guide blocks (two plugin-dir shells, two key-setting
# shells, one models.toml example).
BLOCKS_ADDED = {
    "docs/task-flow/setup.md": 5,
    # Added by the task readme-front-page: the front page keeps one fenced block (the
    # quick-start install block, delta 0 against the post-split count), and the plugin
    # README gains its five-line quick-reference block.
    "README.md": 0,
    PLUGIN_README: 1,
}
# The pages that hold the `<!-- diagram:<name> -->` marker pairs; readme-front-page
# copies the blocks between them onto the front page.
DIAGRAM_SOURCES = {
    "architecture": "docs/task-flow/overview.md",
    "task-flow": "docs/task-flow/commands.md",
}
# Minimum count of Mermaid blocks per page (docs-with-diagrams drew six diagrams).
MERMAID_MIN = {
    "docs/task-flow/overview.md": 1,
    "docs/task-flow/commands.md": 1,
    "docs/task-flow/external-implementer.md": 3,
    "docs/task-flow/benchmark.md": 1,
}
MAX_NODES = 12
# The setup guide's section headings, in order.
SETUP_HEADINGS = [
    "What you need",
    "Install",
    "Starting Claude Code with the plugin directories",
    "Permissions",
    "Keep ANTHROPIC_API_KEY out of the Claude Code session",
    "Mistral account and API key",
    "Setting the key",
    "models.toml for the external implementer",
    "Doctor",
    "First smoke test",
    "Running a task and an autopilot run with the external implementer",
    "What to look at in /usage",
    "Troubleshooting",
]
# Repository paths that exist only at run time; the path check skips them. Each entry
# needs its reason as a comment. None so far: every path named in the docs exists in the
# repository.
RUNTIME_PATHS = set()

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


def fenced_blocks(text):
    """[(info, body)] of the fenced blocks outside other fences, mermaid included."""
    blocks = []
    fence = None
    info = ""
    body = []
    for line in text.splitlines():
        m = FENCE.match(line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
                info = m.group(2)
                body = []
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
            blocks.append((info, "\n".join(body)))
            fence = None
        else:
            body.append(line)
    return blocks


def outside_fences(text):
    """The text with fenced blocks blanked but inline code kept."""
    out = []
    fence = None
    for line in text.splitlines():
        m = FENCE.match(line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
                out.append("")
                continue
            out.append(line)
        else:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
                fence = None
            out.append("")
    return "\n".join(out)


def mermaid_blocks(text):
    """Bodies of the fenced blocks whose info string is `mermaid`, in file order."""
    return [body for info, body in fenced_blocks(text) if info == "mermaid"]


def diagram_block(text, name):
    """The Mermaid body between the `<!-- diagram:<name> -->` and
    `<!-- /diagram:<name> -->` markers.

    Raises ValueError unless each marker occurs exactly once, alone on its line, start
    before end, with exactly one Mermaid block and only blank lines between them.
    readme-front-page reuses this helper to copy the diagrams onto the front page.
    """
    start = f"<!-- diagram:{name} -->"
    end = f"<!-- /diagram:{name} -->"
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == start]
    ends = [i for i, line in enumerate(lines) if line.strip() == end]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError(f"diagram {name!r}: each marker must occur exactly once")
    if starts[0] >= ends[0]:
        raise ValueError(f"diagram {name!r}: the start marker must precede the end")
    region = lines[starts[0] + 1 : ends[0]]
    nonblank = [i for i, line in enumerate(region) if line.strip()]
    if not nonblank:
        raise ValueError(f"diagram {name!r}: no block between the markers")
    first, last = nonblank[0], nonblank[-1]
    open_m = FENCE.match(region[first])
    if not open_m or open_m.group(2) != "mermaid":
        raise ValueError(
            f"diagram {name!r}: the block between the markers is not Mermaid"
        )
    close_m = FENCE.match(region[last])
    if (
        not close_m
        or close_m.group(1)[0] != open_m.group(1)[0]
        or len(close_m.group(1)) < len(open_m.group(1))
        or close_m.group(2)
    ):
        raise ValueError(f"diagram {name!r}: the Mermaid block is not closed")
    fences = [i for i, line in enumerate(region) if FENCE.match(line)]
    if fences != [first, last]:
        raise ValueError(f"diagram {name!r}: only blank lines around one Mermaid block")
    blocks = mermaid_blocks("\n".join(region))
    if len(blocks) != 1:
        raise ValueError(
            f"diagram {name!r}: exactly one Mermaid block between the markers"
        )
    return blocks[0]


def diagram_nodes(block):
    """The node ids of a Mermaid block: quoted strings and %% comments stripped, the
    flowchart/subgraph/end/classDef/class lines skipped, and every id that is directly
    followed by `[`, `(` or `{` collected."""
    ids = set()
    for line in block.splitlines():
        code = re.sub(r'"[^"]*"', "", line.split("%%", 1)[0])
        if re.match(r"^(flowchart|subgraph|end|classDef|class)\b", code.strip()):
            continue
        ids.update(re.findall(r"([A-Za-z][A-Za-z0-9_]*)(?=[\[\(\{])", code))
    return ids


def section_of(text, heading_text):
    """The body of the one section whose heading is `heading_text`, up to the next one."""
    spans = heading_spans(text)
    starts = [i for i, _level, h in spans if h == heading_text]
    if len(starts) != 1:
        raise AssertionError(f"heading {heading_text!r} occurs {len(starts)} times")
    lines = text.splitlines()
    later = [i for i, _level, _h in spans if i > starts[0]]
    end = min(later) if later else len(lines)
    return "\n".join(lines[starts[0] + 1 : end])


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
            if source in STUBS_RETIRED:
                # readme-front-page retired the stubs in both READMEs; their MOVES rows
                # stay as the record of where the sections went.
                continue
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
        # the plugin README intro is a stub under `# task-flow` (retired by
        # readme-front-page, so only checked while the stubs exist)
        if PLUGIN_README not in STUBS_RETIRED:
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
        self.assertEqual(
            sum(BLOCKS_AFTER.values()),
            sum(BLOCKS_BEFORE.values()) + sum(BLOCKS_ADDED.values()),
        )

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

    # --- AC1: diagram markers wrap exactly one Mermaid block ---------------------------

    def test_diagram_markers_wrap_one_mermaid_block(self):
        for name, source in DIAGRAM_SOURCES.items():
            text = self.read(source)
            for marker in (f"<!-- diagram:{name} -->", f"<!-- /diagram:{name} -->"):
                found = [
                    line
                    for page in PAGES
                    for line in self.read(page).splitlines()
                    if line.strip() == marker
                ]
                self.assertEqual(found, [marker], f"{marker} must occur once in docs/")
            body = diagram_block(text, name)
            self.assertIn(body, mermaid_blocks(text))
            self.assertTrue(body.lstrip().startswith("flowchart"))

    def test_diagram_block_rejects_bad_markers(self):
        good = (
            '<!-- diagram:x -->\n```mermaid\nflowchart TD\n  a["b"]\n```\n'
            "<!-- /diagram:x -->\n"
        )
        self.assertEqual(diagram_block(good, "x"), 'flowchart TD\n  a["b"]')
        duplicated = (
            "<!-- diagram:x -->\n<!-- diagram:x -->\n```mermaid\nflowchart TD\n```\n"
            "<!-- /diagram:x -->\n"
        )
        with self.assertRaises(ValueError):
            diagram_block(duplicated, "x")
        missing_end = "<!-- diagram:x -->\n```mermaid\nflowchart TD\n```\n"
        with self.assertRaises(ValueError):
            diagram_block(missing_end, "x")
        two_blocks = (
            "<!-- diagram:x -->\n```mermaid\nflowchart TD\n```\n```mermaid\n"
            "flowchart LR\n```\n<!-- /diagram:x -->\n"
        )
        with self.assertRaises(ValueError):
            diagram_block(two_blocks, "x")
        not_mermaid = "<!-- diagram:x -->\n```toml\n[roles]\n```\n<!-- /diagram:x -->\n"
        with self.assertRaises(ValueError):
            diagram_block(not_mermaid, "x")

    # --- AC2: the required diagrams exist -----------------------------------------------

    def test_required_diagrams_present(self):
        for page, minimum in MERMAID_MIN.items():
            self.assertGreaterEqual(
                len(mermaid_blocks(self.read(page))),
                minimum,
                f"{page}: too few diagrams",
            )

    # --- AC3: every Mermaid block follows the conventions -------------------------------

    def test_diagrams_follow_conventions(self):
        # readme-front-page copies the two source diagrams onto the front page, so its
        # Mermaid blocks are checked too.
        for page in PAGES + [FRONT_PAGE]:
            for block in mermaid_blocks(self.read(page)):
                lines = block.splitlines()
                self.assertIn(
                    lines[0].strip(),
                    ("flowchart TD", "flowchart LR"),
                    f"{page}: first line",
                )
                self.assertLessEqual(
                    len(diagram_nodes(block)), MAX_NODES, f"{page}: too many nodes"
                )
                for line in lines:
                    if "%%{init" in line:
                        self.fail(f"{page}: init directive in {line!r}")
                    code = line.split("%%", 1)[0]
                    self.assertIsNone(
                        re.search(r"\bstyle\b|linkStyle|\bclick\b", code),
                        f"{page}: forbidden directive in {line!r}",
                    )
                    for label in re.findall(r'"[^"]*"', code):
                        self.assertNotRegex(label, "[<>]", f"{page}: {label!r}")
                    remainder = re.sub(
                        r'\(\["|\["|\{"', "", re.sub(r'"[^"]*"', '""', code)
                    )
                    self.assertIsNone(
                        re.search(r"[\[{(]", remainder),
                        f"{page}: unquoted label in {line!r}",
                    )
                if "planned" in block:
                    self.assertIn("classDef planned", block, page)
                    self.assertIn("stroke-dasharray", block, page)

    def test_diagram_nodes_counts_ids_not_labels(self):
        block = 'flowchart TD\n  a["x (y)"] --> b{"z"}\n  b -.-> c(["w"])'
        self.assertEqual(diagram_nodes(block), {"a", "b", "c"})

    # --- AC5: the setup guide covers its topics ------------------------------------------

    def test_setup_guide_covers_required_topics(self):
        page = "docs/task-flow/setup.md"
        text = self.read(page)
        page_headings = [h for _level, h in headings(text)]
        self.assertEqual(
            [h for h in page_headings if h in SETUP_HEADINGS], SETUP_HEADINGS
        )
        ps_blocks = [
            body
            for info, body in fenced_blocks(text)
            if info == "powershell" and "CLAUDE_CODE_PLUGIN_DIRS" in body
        ]
        self.assertTrue(ps_blocks, "no powershell block with CLAUDE_CODE_PLUGIN_DIRS")
        self.assertTrue(
            any(
                "dev-baseline" in b and "task-flow" in b and ";" in b for b in ps_blocks
            ),
            "the two plugin directories must be ; separated in the powershell block",
        )
        keep_out = section_of(
            text, "Keep ANTHROPIC_API_KEY out of the Claude Code session"
        )
        self.assertIn("ANTHROPIC_API_KEY", keep_out)
        for needle in (
            "$env:MISTRAL_API_KEY",
            "export MISTRAL_API_KEY",
            ".claude/task-flow.models.toml",
            "/task-flow:task-doctor",
            "/usage",
        ):
            self.assertIn(needle, text, f"{page}: missing {needle!r}")
        troubleshooting = section_of(text, "Troubleshooting")
        table = [
            line
            for line in troubleshooting.splitlines()
            if line.lstrip().startswith("|")
        ]
        separator = re.compile(r"^\|[\s\-|:]+\|$")
        body_rows = [line for line in table if not separator.match(line.strip())][
            1:
        ]  # the first row is the header
        self.assertGreaterEqual(
            len(body_rows), 4, "the troubleshooting table needs 4 rows"
        )
        joined = "\n".join(body_rows)
        for needle in ("MISTRAL_API_KEY", "600", "__pycache__", "refused"):
            self.assertIn(needle, joined, f"troubleshooting table: missing {needle!r}")

    # --- AC6: every slash command in the link scope has its skill ------------------------

    def test_slash_commands_exist(self):
        for rel in SCOPE:
            for m in re.finditer(
                r"/task-flow:([a-z][a-z0-9-]*)", outside_fences(self.read(rel))
            ):
                skill = (
                    ROOT / "plugins" / "task-flow" / "skills" / m.group(1) / "SKILL.md"
                )
                self.assertTrue(skill.is_file(), f"{rel}: missing {skill}")

    # --- AC7: every repository path in a code span under docs/ exists -------------------

    def test_repo_paths_exist(self):
        plugin_prefixes = (
            "scripts/",
            "tests/",
            "bench/",
            "skills/",
            "agents/",
            "templates/",
        )
        for page in sorted((ROOT / "docs").rglob("*.md")):
            rel = page.relative_to(ROOT).as_posix()
            text = outside_fences(page.read_text(encoding="utf-8"))
            # R6: bare prefixes resolve inside the plugin the page declares as its base
            # ("... are inside `plugins/<dir>/`"), plugins/task-flow/ by default.
            base_m = re.search(r"are inside `(plugins/[a-z-]+)/`", text)
            plugin_base = base_m.group(1) if base_m else "plugins/task-flow"
            for m in re.finditer(r"`([^`\n]*)`", text):
                for token in m.group(1).split():
                    if (
                        any(c in token for c in "<*…")
                        or "..." in token
                        or token.startswith("~")
                        or token in RUNTIME_PATHS
                    ):
                        continue
                    if token.startswith(("plugins/", ".claude/")):
                        target = ROOT / token
                    elif token.startswith(plugin_prefixes):
                        target = ROOT / plugin_base / token
                    else:
                        continue
                    self.assertTrue(target.exists(), f"{rel}: {token} does not exist")
                    if token.endswith("/"):
                        self.assertTrue(
                            target.is_dir(), f"{rel}: {token} is not a directory"
                        )

    # --- readme-front-page: the front page's diagrams -----------------------------------

    def test_front_page_diagrams_match_sources(self):
        text = self.read(FRONT_PAGE)
        for name, source in DIAGRAM_SOURCES.items():
            for marker in (f"<!-- diagram:{name} -->", f"<!-- /diagram:{name} -->"):
                found = [line for line in text.splitlines() if line.strip() == marker]
                self.assertEqual(
                    found, [marker], f"{marker} must occur once in {FRONT_PAGE}"
                )
            self.assertEqual(
                diagram_block(text, name),
                diagram_block(self.read(source), name),
                f"front-page diagram {name!r} differs from {source}",
            )

    def test_front_page_diagram_drift_detected(self):
        text = self.read(FRONT_PAGE)
        for name, source in DIAGRAM_SOURCES.items():
            block = diagram_block(text, name)
            mutated_block = block[0].upper() + block[1:]  # one character changed
            self.assertNotEqual(mutated_block, block)
            mutated = text.replace(block, mutated_block, 1)
            self.assertNotEqual(
                diagram_block(mutated, name),
                diagram_block(self.read(source), name),
                f"a one-character drift in the {name!r} copy must fail the comparison",
            )

    # --- readme-front-page: the bench:latest block ---------------------------------------

    def _bench_marker_lines(self, text):
        lines = text.splitlines()
        starts = [i for i, line in enumerate(lines) if line.strip() == BENCH_LATEST[0]]
        ends = [i for i, line in enumerate(lines) if line.strip() == BENCH_LATEST[1]]
        return lines, starts, ends

    def test_front_page_bench_block(self):
        text = self.read(FRONT_PAGE)
        lines, starts, ends = self._bench_marker_lines(text)
        self.assertEqual(len(starts), 1, BENCH_LATEST[0])
        self.assertEqual(len(ends), 1, BENCH_LATEST[1])
        self.assertLess(starts[0], ends[0])
        region = "\n".join(lines[starts[0] + 1 : ends[0]])
        if (ROOT / "docs/task-flow/benchmark/latest.md").is_file():
            self.assertIn("benchmark/latest.svg", region)
        else:
            self.assertIn("No benchmark has been published yet", region)
            self.assertIn("docs/task-flow/benchmark.md", links(region))

    def test_front_page_accepted_by_publish(self):
        publish_path = ROOT / "plugins/task-flow/bench/publish.py"
        if not publish_path.is_file():
            self.skipTest("plugins/task-flow/bench/publish.py is absent")
        text = self.read(FRONT_PAGE)
        src = publish_path.read_text(encoding="utf-8")

        def literal(name):
            m = re.search(rf'^{name} = "([^"]+)"', src, re.MULTILINE)
            self.assertIsNotNone(m, f"{name} not found in publish.py")
            return m.group(1)

        self.assertEqual((literal("README_START"), literal("README_END")), BENCH_LATEST)
        import importlib.util

        spec = importlib.util.spec_from_file_location("publish", publish_path)
        publish = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publish)
        out = publish.replace_block(text, *BENCH_LATEST, "dummy body")

        def outside(t):
            ls = t.splitlines(keepends=True)
            s = next(i for i, line in enumerate(ls) if line.strip() == BENCH_LATEST[0])
            e = next(i for i, line in enumerate(ls) if line.strip() == BENCH_LATEST[1])
            return "".join(ls[: s + 1]), "".join(ls[e:])

        self.assertEqual(
            outside(out),
            outside(text),
            "replace_block changed text outside the markers",
        )

    # --- readme-front-page: the quick start ----------------------------------------------

    def test_quick_start_commands_exist(self):
        text = self.read(FRONT_PAGE)
        quick = section_of(text, "Quick start")
        open_text = outside_fences(quick)
        for m in re.finditer(r"/task-flow:([a-z][a-z0-9-]*)", open_text):
            skill = ROOT / "plugins" / "task-flow" / "skills" / m.group(1) / "SKILL.md"
            self.assertTrue(skill.is_file(), f"quick start: missing {skill}")
        # readme-front-page: a slash command starts a token, so a "/" inside a URL
        # (https://…) or a path (docs/…) is not a command.
        for m in re.finditer(
            r"(?<![\w/:])/[a-z][a-z0-9-]*(?::[a-z][a-z0-9-]*)?", open_text
        ):
            command = m.group(0)
            if command.startswith("/task-flow:"):
                continue
            line_start = open_text.rfind("\n", 0, m.start()) + 1
            self.assertTrue(
                any(open_text[line_start:].startswith(p) for p in QUICK_START_TOOLS),
                f"quick start: slash command {command!r} has no QUICK_START_TOOLS prefix",
            )
        for _info, body in fenced_blocks(quick):
            for line in body.splitlines():
                if not line.strip() or line.startswith("/task-flow:"):
                    continue
                self.assertTrue(
                    any(line.startswith(p) for p in QUICK_START_TOOLS),
                    f"quick start code block: {line!r} has no QUICK_START_TOOLS prefix",
                )
        marketplace = json.loads(
            (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        plugin_names = {p["name"] for p in marketplace["plugins"]}
        for m in re.finditer(r"([a-z][a-z0-9-]*)@([a-z][a-z0-9-]*)", quick):
            plugin, market = m.groups()
            self.assertIn(
                plugin, plugin_names, f"quick start: unknown plugin {plugin!r}"
            )
            self.assertEqual(
                market, marketplace["name"], "quick start: wrong marketplace"
            )
        for m in re.finditer(r"`([^`\n]*)`", open_text):
            for token in m.group(1).split():
                if token.startswith(("plugins/", ".claude/", "docs/")):
                    self.assertTrue(
                        (ROOT / token).exists(), f"quick start: {token} does not exist"
                    )
        self.assertIn("/task-flow:task-run", quick)
        if (
            ROOT / "plugins" / "task-flow" / "skills" / "task-doctor" / "SKILL.md"
        ).is_file():
            self.assertIn("/task-flow:task-doctor", quick)

    # --- readme-front-page: length limits ------------------------------------------------

    def test_readme_length_limits(self):
        text = self.read(FRONT_PAGE)
        lines, starts, ends = self._bench_marker_lines(text)
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(ends), 1)
        counted = len(lines) - (
            ends[0] - starts[0] - 1
        )  # lines strictly between markers
        self.assertLessEqual(counted, FRONT_PAGE_MAX_LINES, f"{FRONT_PAGE} is too long")
        self.assertLessEqual(
            len(self.read(PLUGIN_README).splitlines()),
            PLUGIN_README_MAX_LINES,
            f"{PLUGIN_README} is too long",
        )

    # --- readme-front-page: front-page structure -----------------------------------------

    def test_front_page_structure(self):
        text = self.read(FRONT_PAGE)
        heads = [h for level, h in headings(text) if level in (2, 3)]
        self.assertEqual(
            heads,
            [
                "task-flow",
                "Architecture",
                "Task flow",
                "What makes it different",
                "Quick start",
                "Benchmark",
                "Documentation",
                "dev-baseline",
                "License",
            ],
        )
        different = section_of(text, "What makes it different")
        bullets = [
            line for line in different.splitlines() if line.lstrip().startswith("- ")
        ]
        self.assertEqual(
            len(bullets), 5, "What makes it different must have five bullets"
        )
        table = [line for line in text.splitlines() if line.lstrip().startswith("|")]
        self.assertTrue(
            any("`task-flow`" in line for line in table), "no task-flow row"
        )
        self.assertTrue(
            any("`dev-baseline`" in line for line in table), "no dev-baseline row"
        )

    # --- readme-front-page: the plugin README --------------------------------------------

    def test_plugin_readme_overview(self):
        text = self.read(PLUGIN_README)
        reference = section_of(text, "Quick reference")
        blocks = fenced_blocks(reference)
        self.assertEqual(
            len(blocks), 1, "Quick reference must have exactly one code block"
        )
        command_lines = blocks[0][1].splitlines()
        self.assertEqual(
            len(command_lines), 5, "the quick reference has exactly five lines"
        )
        for line in command_lines:
            m = re.match(r"/task-flow:([a-z][a-z0-9-]*)", line)
            self.assertIsNotNone(m, f"not a /task-flow: command: {line!r}")
            skill = ROOT / "plugins" / "task-flow" / "skills" / m.group(1) / "SKILL.md"
            self.assertTrue(skill.is_file(), f"plugin README: missing {skill}")
        documentation = section_of(text, "Documentation")
        targets = links(documentation)
        self.assertTrue(targets, "plugin README: Documentation has no links")
        for target in targets:
            self.assertTrue(
                target.startswith("../../docs/task-flow/"),
                f"plugin README link {target!r} does not point into docs/task-flow/",
            )
            resolved = ((ROOT / PLUGIN_README).parent / target).resolve()
            self.assertTrue(resolved.is_file(), f"plugin README: broken link {target}")


if __name__ == "__main__":
    unittest.main()
