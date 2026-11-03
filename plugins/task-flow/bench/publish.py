"""Publish a saved benchmark run as committed pages (P1–P9 of the publish plan).

`bench.py publish [--run <id>]` reads one saved run from
`<results-dir>/<run-id>/{run.json,results.jsonl}` (P1: with no `--run`, the newest
saved run by `created`, then run id) and writes, deterministically — the same inputs
give byte-identical files:

- `<docs-dir>/benchmark/latest.md` and `latest.svg`: the run's setup, one table per
  size plus `All`, and an SVG chart drawn with the standard library only (P6, P7);
- `<docs-dir>/benchmark/history/<date>-<run-id>.md` + `.svg`: the previously published
  latest page, copied with its two generated relative links rewritten (P5);
- `<docs-dir>/benchmark.md`: the history index between the `bench:history` markers,
  created when the file is missing (P4, P5);
- the block between the `bench:latest` markers in the README (P4, P8).

Rules: the rows must be comparable — one fingerprint and one harness version, equal to
run.json's (P2a); a run older than the published latest is refused (P2b); a different
fingerprint is a new baseline and is labelled, never mixed (P2c). All inputs are
checked before the first write, so a refusal changes no file; a README or an existing
benchmark.md without exactly one well-ordered marker pair is refused with an
instruction (P4). Publishing the same run twice in a row changes no file.

Limits: the chart shows `All` sizes only (the per-size detail lives in the tables); a
subscription profile's cost is not compared (it is shown as `subscription`); publish
never runs the benchmark — it spends nothing and reads only the saved run.

Stdlib only; started through `bench.py publish` or imported as a sibling module by the
tests, so it runs on macOS, Linux and Windows.
"""

import json
import os
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR))

import bench

README_START = "<!-- bench:latest:start -->"
README_END = "<!-- bench:latest:end -->"
HISTORY_START = "<!-- bench:history:start -->"
HISTORY_END = "<!-- bench:history:end -->"
PAGE_META = re.compile(
    r"^<!-- bench:page run=(\S+) date=(\S+)(?: created=(\S+))? "
    r"fingerprint=(\S+) profiles=([^ ]*) -->$"
)
REQUIRED_RUN_KEYS = (
    "run_id",
    "created",
    "harness_version",
    "repeat",
    "review",
    "cases",
    "profiles",
    "fingerprint",
)
TABLE_HEADER = (
    "| Profile | Model | Runs | Accepted | Rounds (median) | Hidden pass | "
    "Tokens per accepted (in / cached / out) | Cost per run median (min–max) | "
    "Cost per accepted |"
)
CHART_WIDTH = 760
CHART_BAR_X = 180
CHART_BAR_MAX = 320
CHART_VALUE_X = 510
CHART_BAR_H = 18
CHART_ROW = 28
CHART_FONT = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
TOKEN_COLORS = (("input", "#0969da"), ("cached", "#54aeff"), ("output", "#bf8700"))
COST_COLOR = "#1a7f37"
TEXT_COLOR = "#1f2328"


class PublishError(Exception):
    """A refusal: exit 2, the reason on stderr, no file changed."""


class MarkerError(Exception):
    """A marker is missing, duplicated or out of order in a file."""


# ---------------------------------------------------------------------------- loading (P1, P2a)


def load_saved_run(results_dir, run_id) -> tuple[dict, list]:
    """Read `<results_dir>/<run_id>/{run.json,results.jsonl}`; PublishError on any problem."""
    results_dir = Path(results_dir)
    if not bench.RUN_ID.match(str(run_id)):
        raise PublishError(f"invalid run id {run_id!r}")
    run_dir = results_dir / str(run_id)
    run_json = run_dir / "run.json"
    results_jsonl = run_dir / "results.jsonl"
    if not run_json.is_file() or not results_jsonl.is_file():
        raise PublishError(f"no saved run {run_id!r} under {results_dir}")
    try:
        meta = json.loads(run_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublishError(f"{run_json}: invalid JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise PublishError(f"{run_json}: run.json must be a JSON object")
    missing = [key for key in REQUIRED_RUN_KEYS if key not in meta]
    if missing:
        raise PublishError(f"{run_json}: missing key(s): {', '.join(missing)}")
    try:
        text = results_jsonl.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PublishError(f"{results_jsonl}: cannot read: {exc}") from exc
    rows: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PublishError(
                f"{results_jsonl}:{lineno}: invalid JSON line: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise PublishError(f"{results_jsonl}:{lineno}: a row must be a JSON object")
        rows.append(row)
    return meta, rows


def newest_run(results_dir) -> str | None:
    """The newest saved run id (run.json `created`, then run id), or None."""
    results_dir = Path(results_dir)
    if not results_dir.is_dir():
        return None
    best_key: tuple | None = None
    best_name: str | None = None
    for child in sorted(results_dir.iterdir()):
        if not (
            child.is_dir()
            and (child / "run.json").is_file()
            and (child / "results.jsonl").is_file()
        ):
            continue
        try:
            meta = json.loads((child / "run.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublishError(f"{child / 'run.json'}: invalid JSON: {exc}") from exc
        if not isinstance(meta, dict):
            raise PublishError(f"{child / 'run.json'}: run.json must be a JSON object")
        key = (str(meta.get("created", "")), str(meta.get("run_id") or child.name))
        if best_key is None or key > best_key:
            best_key, best_name = key, child.name
    return best_name


def check_comparable(meta: dict, rows: list) -> None:
    """P2a: one fingerprint and harness version across the rows, equal to run.json's."""
    run_id = meta.get("run_id", "?")
    fingerprints = {str(row.get("fingerprint", "")) for row in rows}
    fingerprints.add(str(meta.get("fingerprint", "")))
    if len(fingerprints) > 1:
        listed = ", ".join(sorted(fingerprints))
        raise PublishError(
            f"run {run_id} mixes incomparable suites (fingerprints: {listed}); "
            "start a new baseline with bench.py run and save it"
        )
    versions = {str(row.get("harness_version", "")) for row in rows}
    versions.add(str(meta.get("harness_version", "")))
    if len(versions) > 1:
        listed = ", ".join(sorted(versions))
        raise PublishError(
            f"run {run_id} mixes incomparable harness versions ({listed}); "
            "start a new baseline with bench.py run and save it"
        )


# ---------------------------------------------------------------------------- summary (P3)


def _profile_cell(name: str, prows: list, group_cases: list, repeat: int) -> dict:
    accepted = [r for r in prows if r["outcome"] == "accepted"]
    done = [r for r in prows if r["outcome"] != "skipped"]
    runs = len(prows)
    pct = (100 * len(accepted)) // runs if runs else 0
    reasons: list[str] = []
    for row in prows:
        if row["outcome"] in ("skipped", "unavailable", "cost_cap"):
            reasons.append(f"{row['outcome']}: {row['reason']}")
    expected = len(group_cases) * repeat
    covered = len({(r["case"], r["repeat"]) for r in done})
    if covered < expected:
        reasons.append(
            f"missing {expected - covered} of {expected} (case, repeat) runs"
        )
    partial = bool(reasons)
    tokens = None
    if accepted:
        tokens = {}
        for kind in ("input", "cached", "output"):
            total = sum(
                int(r.get("tokens", {}).get(kind, 0))
                + int(r.get("review_tokens", {}).get(kind, 0))
                for r in done
            )
            tokens[kind] = round(total / len(accepted))  # round-half-even
    known = bench._cost_known(prows) if prows else True
    if not prows:
        model, billing = "", ""
    else:
        model = next((r["model"] for r in prows), "")
        billing = next(
            (r["billing"] for r in prows if r["outcome"] != "skipped"),
            prows[0]["billing"],
        )
    if known and done:
        costs = [r["cost_usd"] + r["review_cost_usd"] for r in done]
        per_run = (
            f"{bench.fmt_usd(bench.median(costs))} "
            f"({bench.fmt_usd(min(costs))}–{bench.fmt_usd(max(costs))})"
        )
    elif known:
        per_run = "n/a"
    else:
        per_run = "subscription"
    per_accepted_text = bench._cost_per_accepted(prows, accepted)
    if known and accepted:
        per_accepted = sum(r["cost_usd"] + r["review_cost_usd"] for r in prows) / len(
            accepted
        )
        cost_label = None
    elif not known:
        per_accepted, cost_label = None, "subscription"
    else:
        per_accepted, cost_label = None, "n/a (0 accepted)"
    return {
        "profile": name,
        "label": name + (" (partial)" if partial else ""),
        "model": model,
        "billing": billing,
        "runs": runs,
        "accepted": len(accepted),
        "accepted_text": f"{len(accepted)}/{runs} ({pct}%)",
        "rounds_text": bench.fmt_num(bench.median([r["rounds"] for r in accepted])),
        "hidden_pass": bench._hidden_pass(done),
        "tokens": tokens,
        "tokens_text": (
            " / ".join(f"{tokens[kind]:,}" for kind in ("input", "cached", "output"))
            if tokens
            else "n/a (0 accepted)"
        ),
        "cost_known": known,
        "cost_per_run_text": per_run,
        "cost_per_accepted_text": per_accepted_text,
        "cost_per_accepted": per_accepted,
        "cost_label": cost_label,
        "partial": partial,
        "partial_reasons": sorted(set(reasons)),
    }


def summarize(meta: dict, rows: list) -> dict:
    """P3: the figures of one saved run, per profile, per size and for `All`."""
    rows = sorted(rows, key=lambda r: r["n"])
    profiles = [str(p) for p in meta.get("profiles", [])]
    for row in rows:
        if row["profile"] not in profiles:
            profiles.append(row["profile"])
    cases = sorted(set(meta.get("cases", [])) | {r["case"] for r in rows})
    case_size: dict[str, str] = {}
    for row in rows:
        case_size.setdefault(row["case"], row["size"])
    repeat = int(meta.get("repeat", 1))
    sizes = [s for s in bench.SIZES if any(r["size"] == s for r in rows)]
    tables: dict[str, list] = {}
    for size in [*sizes, "All"]:
        group_rows = rows if size == "All" else [r for r in rows if r["size"] == size]
        group_cases = (
            cases if size == "All" else [c for c in cases if case_size.get(c) == size]
        )
        tables[size] = [
            _profile_cell(
                name,
                [r for r in group_rows if r["profile"] == name],
                group_cases,
                repeat,
            )
            for name in profiles
        ]
    all_cells = tables["All"]
    profile_info: dict[str, dict] = {}
    for name in profiles:
        row = next(
            (r for r in rows if r["profile"] == name and r["outcome"] != "skipped"),
            next((r for r in rows if r["profile"] == name), None),
        )
        if row is not None:
            profile_info[name] = {
                "model": row.get("model", ""),
                "billing": row.get("billing", ""),
                "settings": row.get("settings") or {},
            }
    chart_parts = []
    for cell in all_cells:
        tokens = cell["tokens"]
        tokens_text = (
            f"in {tokens['input']:,}, cached {tokens['cached']:,}, out {tokens['output']:,}"
            if tokens
            else "n/a (0 accepted)"
        )
        chart_parts.append(
            f"{cell['label']}: tokens {tokens_text}, cost {cell['cost_per_accepted_text']}"
        )
    chart_alt = (
        f"Benchmark run {meta.get('run_id', '')}, all sizes: "
        + "; ".join(chart_parts)
        + "."
    )
    notes: list[str] = []
    if not meta.get("review"):
        notes.append("no reviewer ran, so review cost is 0 (`--review` was off)")
    for cell in all_cells:
        if not cell["cost_known"]:
            notes.append(
                f"profile `{cell['profile']}` bills by subscription, so its cost is "
                "unknown; it is compared on tokens, rounds and acceptance"
            )
        if cell["partial"]:
            notes.append(
                f"profile `{cell['profile']}` is partial — {'; '.join(cell['partial_reasons'])}"
            )
    merged_from = meta.get("merged_from")
    if merged_from:
        notes.append(
            "this run merges rows of the runs "
            + ", ".join(f"`{rid}`" for rid in merged_from)
            + "; each row records its `source_run` in the saved results.jsonl"
        )
    for name in profiles:
        limits: dict = {}
        for row in rows:
            if row["profile"] == name and row["outcome"] != "skipped":
                turns = (row.get("settings") or {}).get("max_turns")
                limits[turns] = limits.get(turns, 0) + 1
        if len(limits) > 1:
            parts = ", ".join(
                f"{count} row(s) at {turns if turns is not None else 'unknown'} turns"
                for turns, count in sorted(limits.items(), key=lambda kv: str(kv[0]))
            )
            notes.append(
                f"profile `{name}` ran with different turn limits ({parts}); "
                "rows are not strictly like for like"
            )
    failures = [r for r in rows if r["outcome"] != "accepted" or r["hidden"] == "fail"]
    return {
        "run_id": meta.get("run_id", ""),
        "created": str(meta.get("created", "")),
        "date": str(meta.get("created", ""))[:10],
        "harness_version": meta.get("harness_version"),
        "fingerprint": str(meta.get("fingerprint", "")),
        "profiles": profiles,
        "cases": cases,
        "case_sizes": {c: case_size.get(c, "?") for c in cases},
        "repeat": repeat,
        "review": bool(meta.get("review")),
        "task_flow_version": meta.get("task_flow_version")
        or "unknown (not recorded by this run)",
        "sizes": sizes,
        "tables": tables,
        "all": all_cells,
        "profile_info": profile_info,
        "notes": notes,
        "failures": failures,
        "rows": rows,
        "chart_alt": chart_alt,
        "new_baseline": False,
        "prev_run_id": None,
    }


# ---------------------------------------------------------------------------- rendering (P4–P8)


def replace_block(text: str, start: str, end: str, body: str) -> str:
    """P4: replace what is between the two marker lines; MarkerError otherwise.

    Each marker must occur exactly once, as a whole line, start before end. Everything
    up to and including the start-marker line and from the end-marker line on is kept
    byte-identical (line endings included); the body uses the start marker's line ending.
    """
    lines = text.splitlines(keepends=True)

    def eol(line: str) -> str:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith(("\n", "\r")):
            return line[-1]
        return ""

    def content(line: str) -> str:
        return line[: len(line) - len(eol(line))] if eol(line) else line

    start_idx = [i for i, line in enumerate(lines) if content(line) == start]
    end_idx = [i for i, line in enumerate(lines) if content(line) == end]
    if len(start_idx) != 1:
        raise MarkerError(
            f"marker {start!r} must occur exactly once on its own line "
            f"(found {len(start_idx)})"
        )
    if len(end_idx) != 1:
        raise MarkerError(
            f"marker {end!r} must occur exactly once on its own line "
            f"(found {len(end_idx)})"
        )
    first, last = start_idx[0], end_idx[0]
    if first >= last:
        raise MarkerError(f"marker {start!r} must come before {end!r}")
    body_text = body if body.endswith("\n") else body + "\n"
    body_text = body_text.replace("\r\n", "\n").replace("\r", "\n")
    if eol(lines[first]) == "\r\n":
        body_text = body_text.replace("\n", "\r\n")
    return "".join(lines[: first + 1]) + body_text + "".join(lines[last:])


def parse_page_meta(text: str) -> dict | None:
    """The `<!-- bench:page ... -->` metadata line of a generated page, or None."""
    first = text.split("\n", 1)[0].rstrip("\r")
    match = PAGE_META.match(first)
    if not match:
        return None
    return {
        "run": match.group(1),
        "date": match.group(2),
        # the full `created` of run.json; pages written before it was stored fall
        # back to the date, which is the first 10 characters of it anyway
        "created": match.group(3) or match.group(2),
        "fingerprint": match.group(4),
        "profiles": [p for p in match.group(5).split(",") if p],
    }


def _table_lines(cells: list) -> list[str]:
    lines = [TABLE_HEADER, "| " + " --- | " * 8 + "--- |"]
    for cell in cells:
        lines.append(
            f"| {cell['label']} | {cell['model']} | {cell['runs']} | "
            f"{cell['accepted_text']} | {cell['rounds_text']} | {cell['hidden_pass']} | "
            f"{cell['tokens_text']} | {cell['cost_per_run_text']} | "
            f"{cell['cost_per_accepted_text']} |"
        )
    return lines


def _profile_setup_line(name: str, info: dict) -> str:
    line = f"- profile `{name}`: model {info['model']}, billing {info['billing']}"
    prices = []
    for key, label in (
        ("price_input_per_mtok", "input"),
        ("price_cached_input_per_mtok", "cached"),
        ("price_output_per_mtok", "output"),
    ):
        if key in info["settings"]:
            prices.append(f"${info['settings'][key]:.4f}/M {label}")
    if prices:
        line += ", prices " + ", ".join(prices)
    return line


def render_latest(summary: dict, prev: dict | None) -> str:
    """P6: the latest.md page for one summarized run (prev = the published latest's meta)."""
    lines = [
        (
            f"<!-- bench:page run={summary['run_id']} date={summary['date']} "
            f"created={summary['created']} fingerprint={summary['fingerprint']} "
            f"profiles={','.join(summary['profiles'])} -->"
        ),
        "",
        "# Benchmark: latest result",
        "",
        "[All benchmark results](../benchmark.md)",
        "",
        "## Setup",
        "",
        f"- run: {summary['run_id']}, {summary['date']}",
        "- cases: "
        + ", ".join(f"{c} ({summary['case_sizes'][c]})" for c in summary["cases"]),
        f"- repeats: each case x profile ran {summary['repeat']} times; medians with min–max",
        f"- review: {'on' if summary['review'] else 'off'}",
    ]
    for name in summary["profiles"]:
        info = summary["profile_info"].get(name)
        if info is not None:
            lines.append(_profile_setup_line(name, info))
    lines += [
        f"- harness version: {summary['harness_version']}",
        f"- fingerprint: `{summary['fingerprint']}`",
        f"- task-flow version: {summary['task_flow_version']}",
        "",
    ]
    if prev and prev.get("fingerprint") != summary["fingerprint"]:
        lines.append(
            f"New baseline: the suite changed since run {prev.get('run')}; results are "
            "not comparable with earlier runs."
        )
        lines.append("")
    lines += [f"![{summary['chart_alt']}](latest.svg)", ""]
    for size in [*summary["sizes"], "All"]:
        lines.append(f"## {size}")
        lines.append("")
        lines.extend(_table_lines(summary["tables"][size]))
        lines.append("")
    lines.append("## Notes")
    lines.append("")
    if summary["notes"]:
        lines.extend(f"- {note}" for note in summary["notes"])
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Failures")
    lines.append("")
    if summary["failures"]:
        for row in summary["failures"]:
            lines.append(
                f"- n={row['n']} case={row['case']} profile={row['profile']} "
                f"repeat={row['repeat']} outcome={row['outcome']} hidden={row['hidden']} "
                f"— {bench.one_line(str(row['reason']), 200)}"
            )
    else:
        lines.append("none")
    return "\n".join(lines) + "\n"


def render_chart(summary: dict) -> str:
    """P7: the deterministic SVG chart (stdlib only) for the `All` figures."""
    cells = summary["all"]
    count = len(cells)
    head1_y = 24
    bars1_y = 44
    head2_y = bars1_y + count * CHART_ROW + 16
    bars2_y = head2_y + 20
    height = bars2_y + count * CHART_ROW + 10
    out = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{CHART_WIDTH}" '
            f'height="{height}" viewBox="0 0 {CHART_WIDTH} {height}" role="img" '
            'aria-labelledby="bench-title bench-desc">'
        ),
        (
            f'  <title id="bench-title">Benchmark chart for run '
            f"{escape(str(summary['run_id']))}</title>"
        ),
        f'  <desc id="bench-desc">{escape(summary["chart_alt"])}</desc>',
        (
            f'  <rect x="0" y="0" width="{CHART_WIDTH}" height="{height}" rx="6" '
            'fill="#ffffff" stroke="#d0d7de"/>'
        ),
        (
            f'  <text x="10" y="{head1_y}" font-family="{CHART_FONT}" font-size="14" '
            f'font-weight="bold" fill="{TEXT_COLOR}">Tokens per accepted task (All sizes)</text>'
        ),
    ]
    for label, color, x in (
        ("input", "#0969da", 430),
        ("cached", "#54aeff", 500),
        ("output", "#bf8700", 580),
    ):
        out.append(
            f'  <rect x="{x}" y="{head1_y - 9}" width="10" height="10" fill="{color}"/>'
        )
        out.append(
            f'  <text x="{x + 14}" y="{head1_y}" font-family="{CHART_FONT}" '
            f'font-size="12" fill="{TEXT_COLOR}">{label}</text>'
        )
    totals = [sum(c["tokens"].values()) if c["tokens"] else 0 for c in cells]
    max_total = max(totals, default=0)
    for i, cell in enumerate(cells):
        y = bars1_y + i * CHART_ROW
        out.append("  <g>")
        out.append(
            f'    <text x="10" y="{y + 13}" font-family="{CHART_FONT}" font-size="12" '
            f'fill="{TEXT_COLOR}">{escape(cell["label"])}</text>'
        )
        x = CHART_BAR_X
        if max_total > 0 and cell["tokens"]:
            for kind, color in TOKEN_COLORS:
                width = round(CHART_BAR_MAX * cell["tokens"][kind] / max_total)
                if width > 0:
                    out.append(
                        f'    <rect x="{x}" y="{y}" width="{width}" '
                        f'height="{CHART_BAR_H}" fill="{color}"/>'
                    )
                    x += width
        if cell["tokens"]:
            value = (
                f"in {cell['tokens']['input']:,} · cached {cell['tokens']['cached']:,} "
                f"· out {cell['tokens']['output']:,}"
            )
        else:
            value = "n/a (0 accepted)"
        out.append(
            f'    <text x="{CHART_VALUE_X}" y="{y + 13}" font-family="{CHART_FONT}" '
            f'font-size="12" fill="{TEXT_COLOR}">{escape(value)}</text>'
        )
        out.append("  </g>")
    out.append(
        f'  <text x="10" y="{head2_y}" font-family="{CHART_FONT}" font-size="14" '
        f'font-weight="bold" fill="{TEXT_COLOR}">Cost per accepted task (All sizes)</text>'
    )
    costs = [
        c["cost_per_accepted"] for c in cells if c["cost_per_accepted"] is not None
    ]
    max_cost = max(costs, default=0.0)
    for i, cell in enumerate(cells):
        y = bars2_y + i * CHART_ROW
        out.append("  <g>")
        out.append(
            f'    <text x="10" y="{y + 13}" font-family="{CHART_FONT}" font-size="12" '
            f'fill="{TEXT_COLOR}">{escape(cell["label"])}</text>'
        )
        if max_cost > 0 and cell["cost_per_accepted"] is not None:
            width = round(CHART_BAR_MAX * cell["cost_per_accepted"] / max_cost)
            if width > 0:
                out.append(
                    f'    <rect x="{CHART_BAR_X}" y="{y}" width="{width}" '
                    f'height="{CHART_BAR_H}" fill="{COST_COLOR}"/>'
                )
        value = (
            bench.fmt_usd(cell["cost_per_accepted"])
            if cell["cost_per_accepted"] is not None
            else (cell["cost_label"] or "n/a (0 accepted)")
        )
        out.append(
            f'    <text x="{CHART_VALUE_X}" y="{y + 13}" font-family="{CHART_FONT}" '
            f'font-size="12" fill="{TEXT_COLOR}">{escape(value)}</text>'
        )
        out.append("  </g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def render_readme_block(summary: dict, rel: str) -> str:
    """P8: the README block body; `rel` is the POSIX path from the README to the docs dir."""
    prefix = "" if rel in ("", ".") else f"{rel}/"
    lines = [
        (
            f"Latest benchmark: run {summary['run_id']}, {summary['date']}, "
            f"{len(summary['cases'])} cases x {summary['repeat']} repeats, "
            f"task-flow {summary['task_flow_version']}."
        )
    ]
    if summary.get("new_baseline"):
        lines.append("")
        lines.append(
            f"New baseline: the suite changed since run {summary.get('prev_run_id')}; "
            "results are not comparable with earlier runs."
        )
    lines.append("")
    lines.append(
        "| Profile | Model | Accepted | Hidden pass | "
        "Tokens per accepted (in / cached / out) | Cost per accepted |"
    )
    lines.append("| " + " --- | " * 5 + "--- |")
    for cell in summary["all"]:
        lines.append(
            f"| {cell['label']} | {cell['model']} | {cell['accepted_text']} | "
            f"{cell['hidden_pass']} | {cell['tokens_text']} | "
            f"{cell['cost_per_accepted_text']} |"
        )
    for note in summary.get("notes", []):
        if note.startswith("this run merges") or "different turn limits" in note:
            lines.append("")
            lines.append(f"Note: {note}")
    lines.append("")
    lines.append(f"![{summary['chart_alt']}]({prefix}benchmark/latest.svg)")
    lines.append("")
    lines.append(
        f"[Details and history]({prefix}benchmark/latest.md) · "
        f"[All results]({prefix}benchmark.md)"
    )
    return "\n".join(lines) + "\n"


def render_history_index(pages: list) -> str:
    """P5: the history index block body (newest first) from the pages' metadata."""
    ordered = sorted(
        pages,
        key=lambda p: (str(p.get("date", "")), str(p.get("run", ""))),
        reverse=True,
    )
    lines = [
        (
            "Rows are comparable only when their fingerprints are equal; a `new baseline` "
            "row marks a suite change."
        ),
        "",
        "| Date | Run | Fingerprint | Profiles | Page |",
        "| " + " --- | " * 4 + "--- |",
    ]
    for i, page in enumerate(ordered):
        fingerprint = str(page.get("fingerprint", ""))
        if fingerprint.startswith("sha256:"):
            short = "sha256:" + fingerprint.split("sha256:", 1)[-1][:12]
        else:
            short = fingerprint
        if (
            i + 1 < len(ordered)
            and str(ordered[i + 1].get("fingerprint", "")) != fingerprint
        ):
            short += " (new baseline)"
        cell = page.get("page") or (
            f"[{page.get('date')}-{page.get('run')}](history/{page.get('date')}-{page.get('run')}.md)"
        )
        lines.append(
            f"| {page.get('date', '')} | {page.get('run', '')} | {short} | "
            f"{','.join(page.get('profiles', []))} | {cell} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- publish (P4, P5, P9)


def _read_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PublishError(f"{path}: cannot read as UTF-8: {exc}") from exc


def _apply_block(
    text: str, start: str, end: str, body: str, path: Path, what: str
) -> str:
    try:
        return replace_block(text, start, end, body)
    except MarkerError as exc:
        raise PublishError(
            f"{path}: {exc}; add these two marker lines on their own lines where the "
            f"{what} block should go:\n{start}\n{end}"
        ) from exc


def _write_if_changed(path: Path, data: bytes) -> None:
    if path.is_file() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _rel_posix(target, start) -> str:
    """POSIX path of `target` relative to `start`; the absolute POSIX path when the two
    are on different drives (Windows), where no relative path exists."""
    try:
        return Path(os.path.relpath(target, start)).as_posix()
    except ValueError:
        return Path(target).as_posix()


def _benchmark_md_template(docs_dir: Path) -> str:
    rel = _rel_posix(BENCH_DIR / "README.md", docs_dir)
    return (
        "# Benchmark\n"
        "\n"
        "Published by `bench.py publish` from the saved runs; the workflow is documented "
        f"in [plugins/task-flow/bench/README.md]({rel}).\n"
        "\n"
        f"{HISTORY_START}\n"
        f"{HISTORY_END}\n"
    )


def publish(results_dir, docs_dir, readme, run_id=None) -> list[str]:
    """P9: check everything, then write everything; return the stdout parts."""
    results_dir = Path(results_dir)
    docs_dir = Path(docs_dir)
    readme = Path(readme)
    if run_id is None:
        run_id = newest_run(results_dir)
        if run_id is None:
            raise PublishError(f"no saved runs under {results_dir}")
    meta, rows = load_saved_run(results_dir, run_id)
    check_comparable(meta, rows)
    summary = summarize(meta, rows)

    bench_pages = docs_dir / "benchmark"
    latest_md = bench_pages / "latest.md"
    latest_svg = bench_pages / "latest.svg"
    history_dir = bench_pages / "history"
    index_md = docs_dir / "benchmark.md"

    prev = None
    prev_text = None
    if latest_md.is_file():
        prev_text = _read_text(latest_md)
        prev = parse_page_meta(prev_text)
        if prev is None:
            raise PublishError(
                f"{latest_md}: no bench:page metadata line on the first line"
            )
    if (
        prev
        and prev.get("run") != run_id
        and (summary["created"], str(run_id))
        < (prev.get("created", ""), prev.get("run", ""))
    ):
        raise PublishError(
            f"run {run_id} is older than the published latest {prev.get('run')} "
            f"({prev.get('created', prev.get('date', ''))}); publish the newest saved run"
        )
    rotate = prev is not None and prev.get("run") != run_id
    hist_md = hist_svg = None
    if rotate:
        hist_md = history_dir / f"{prev['date']}-{prev['run']}.md"
        hist_svg = history_dir / f"{prev['date']}-{prev['run']}.svg"
        if hist_md.exists() or hist_svg.exists():
            raise PublishError(f"history target already exists: {hist_md}")
    summary["new_baseline"] = bool(
        prev and prev.get("fingerprint") != summary["fingerprint"]
    )
    summary["prev_run_id"] = prev.get("run") if prev else None

    if not readme.is_file():
        raise PublishError(f"README not found: {readme}")
    readme_text = _read_text(readme)
    rel = _rel_posix(docs_dir, readme.parent)
    new_readme_text = _apply_block(
        readme_text,
        README_START,
        README_END,
        render_readme_block(summary, rel),
        readme,
        "bench:latest",
    )

    if index_md.is_file():
        index_text = _read_text(index_md)
    else:
        index_text = _benchmark_md_template(docs_dir)

    new_latest_text = render_latest(summary, prev)
    new_svg_text = render_chart(summary)
    rotated_md = rotated_svg = None
    if rotate:
        rotated_md = prev_text.replace(
            "](latest.svg)", f"]({prev['date']}-{prev['run']}.svg)"
        ).replace("](../benchmark.md)", "](../../benchmark.md)")
        if not latest_svg.is_file():
            raise PublishError(
                f"{latest_svg}: the published latest {prev.get('run')} has no chart to rotate"
            )
        rotated_svg = latest_svg.read_bytes()

    pages = [
        {
            "run": summary["run_id"],
            "date": summary["date"],
            "fingerprint": summary["fingerprint"],
            "profiles": summary["profiles"],
            "page": "latest",
        }
    ]
    if rotate:
        pages.append(
            {
                "run": prev["run"],
                "date": prev["date"],
                "fingerprint": prev["fingerprint"],
                "profiles": prev.get("profiles", []),
            }
        )
    if history_dir.is_dir():
        for path in sorted(history_dir.glob("*.md")):
            page = parse_page_meta(_read_text(path))
            if page is not None:
                pages.append(page)
    new_index_text = _apply_block(
        index_text,
        HISTORY_START,
        HISTORY_END,
        render_history_index(pages),
        index_md,
        "bench:history",
    )

    # every check is done; now write
    parts = [f"published {run_id} -> {latest_md}"]
    if rotate:
        _write_if_changed(hist_md, rotated_md.encode("utf-8"))
        _write_if_changed(hist_svg, rotated_svg)
        parts.append(f"rotated {prev['run']} -> {hist_md}")
    if summary["new_baseline"]:
        parts.append("new baseline")
    _write_if_changed(latest_md, new_latest_text.encode("utf-8"))
    _write_if_changed(latest_svg, new_svg_text.encode("utf-8"))
    _write_if_changed(index_md, new_index_text.encode("utf-8"))
    _write_if_changed(readme, new_readme_text.encode("utf-8"))
    return parts
