# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Run implementer profiles from models.toml on frozen task cases and report cost.

A benchmark run measures one thing: what an implementer profile costs per accepted
task. Each run (case x profile x repeat) drives up to 3 rounds of scripts/implement.py
with scripts/gates.py as the judge, then runs the case's hidden acceptance tests, and
records one row in .work/_bench/<run-id>/results.jsonl; report.md aggregates the rows.

Design decisions, briefly:

- D1 isolation: a run's code directory is a fresh git repository exported from the
  source repository with `git archive` (no history, no result objects), so the
  implementer can never read the hidden tests from git; the source repository's
  working tree, index, refs and worktree list are never touched. Limit: the scratch
  repo has no history and export-ignore attributes apply.
- D2 layout: <out>/<run-id>/runs/<n>/ holds code/ (the scratch repo), .work/<case>/
  (the seeded task folder), .work/_hidden/ (the hidden checks task) and
  .claude/task-flow.models.toml (the per-run profile).
- D3 case format: cases/<id>/case.toml, parsed strictly (unknown key -> refusal).
- D4 profiles: resolved with models.load(--project); each run gets a models file with
  only that profile, fallback = [] and max_cost_usd capped at the remaining budget.
- D5 one run: gates round 0, then rounds 1..3 of implement.py + gates.py; green means
  no introduced failure against checks-0.md; outcomes accepted, round_cap,
  no_progress, blocked, failed, unavailable, cost_cap, error, skipped.
- D6 without --review, review-<r>.md is a fix list built from the introduced failures.
- D7 --review runs the real reviewer through --claude-command (spends Claude limits).
- D8 one JSON row per run in results.jsonl.
- D9 a run is identified by its run id and a suite fingerprint, so `run --extend`
  can add a profile later; run.json records the inputs.
- D10 report.md is deterministic for a given results.jsonl.
- D11 CLI: list, run, report, save.

Limits, stated plainly: the scope rule (in_scope) is a heuristic over the plan text,
and the cost of a subscription profile is not known, so such profiles are compared on
tokens, rounds, time and acceptance instead of dollars.

Stdlib only; started with `uv run --script plugins/task-flow/bench/bench.py` or
imported as a sibling module by the tests, so it runs on macOS, Linux and Windows.
"""

import argparse
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

import tomllib

BENCH_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = BENCH_DIR.parent
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import launch
import models

HARNESS_VERSION = 1
DEFAULT_CASES_DIR = BENCH_DIR / "cases"
DEFAULT_RESULTS_DIR = BENCH_DIR / "results"
SIZES = ("S", "M", "L")
CASE_KEYS = (
    "id",
    "size",
    "repo",
    "base",
    "result",
    "plan",
    "hidden_tests",
    "commands",
)
REQUIRED_CASE_KEYS = (
    "id",
    "size",
    "repo",
    "base",
    "result",
    "hidden_tests",
    "commands",
)
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TABLE_ROW = re.compile(
    r"^\| (\d+) \| (.*) \| (PASS|FAIL|ERROR) \| ([^|]*) \| ([^|]*) \|$"
)
FAILURE_HEAD = re.compile(r"^### (\d+)\. (.*)$")
FENCE = re.compile(r"^`{3,}$")
NOT_RUN = re.compile(r"^- (\d+)\. (.+)$")
VERDICT = re.compile(r"^## Verdict:\s*(\S+)", re.MULTILINE)
AC_FAIL = re.compile(r"^\|\s*(AC\d+)\s*\|\s*FAIL\b", re.MULTILINE)


class BenchError(Exception):
    """A refusal: exit 2, the reason on stderr, nothing created under --out."""


class CaseError(Exception):
    """An invalid case: the message names the file and the problem."""


# ---------------------------------------------------------------------------- helpers


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def one_line(text: str, limit: int = 300) -> str:
    return " ".join(text.split())[:limit]


def git(cwd: Path, *args: str, input: bytes | None = None, check: bool = True):
    """Run one git command with an argument list; return the CompletedProcess."""
    done = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        input=input,
        timeout=300,
        check=False,
    )
    if check and done.returncode != 0:
        err = done.stderr.decode("utf-8", "replace").strip()
        raise BenchError(f"git {' '.join(args[:2])} failed in {cwd}: {one_line(err)}")
    return done


def resolve_commit(repo: Path, rev: str) -> str:
    done = git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}", check=False)
    if done.returncode != 0:
        err = done.stderr.decode("utf-8", "replace").strip()
        raise BenchError(
            f"cannot resolve {rev!r} to a commit in {repo}: {one_line(err)}"
        )
    return done.stdout.decode("utf-8", "ascii").strip()


def sha256_text_file(path: Path) -> str:
    """SHA-256 of a file's content, CRLF normalized to LF, as UTF-8 text."""
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def toml_value(value) -> str:
    """One TOML value (the resolved profiles contain only these types)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{k} = {toml_value(v)}" for k, v in value.items()) + " }"
        )
    return repr(value)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def rmtree_force(path: Path) -> None:
    """Delete a tree even when git made its object files read-only (Windows)."""

    def handle(func, target, _exc):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    if path.exists():
        shutil.rmtree(path, onexc=handle)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def median(values: list) -> float:
    return float(statistics.median(values)) if values else 0.0


def fmt_num(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"


def fmt_usd(value: float) -> str:
    return f"${value:.4f}"


# ---------------------------------------------------------------------------- cases (D3)


def _case_text(data: dict, key: str, path: Path) -> str:
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise CaseError(f"{path}: {key} must be a non-empty string, got {value!r}")
    return value


def load_case(case_dir) -> dict:
    """Parse and validate one cases/<id>/ folder; raises CaseError naming file+problem."""
    case_dir = Path(case_dir)
    path = case_dir / "case.toml"
    if not path.is_file():
        raise CaseError(f"{path}: no case.toml in {case_dir}")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CaseError(f"{path}: cannot read case.toml: {exc}") from exc
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise CaseError(f"{path}: invalid TOML: {exc}") from exc
    if not isinstance(data, dict):
        raise CaseError(f"{path}: case.toml must be a table of keys")
    for key in data:
        if key not in CASE_KEYS:
            raise CaseError(
                f"{path}: unknown key {key!r} (known keys: {', '.join(CASE_KEYS)})"
            )
    for key in REQUIRED_CASE_KEYS:
        if key not in data:
            raise CaseError(f"{path}: missing required key {key!r}")
    cid = data["id"]
    if not isinstance(cid, str) or not cid or cid != case_dir.name:
        raise CaseError(
            f"{path}: id must be a non-empty string equal to the folder name "
            f"{case_dir.name!r}, got {cid!r}"
        )
    size = data["size"]
    if size not in SIZES:
        raise CaseError(f"{path}: size must be one of {list(SIZES)}, got {size!r}")
    repo = _case_text(data, "repo", path)
    repo_path = Path(repo)
    if not repo_path.is_absolute():
        repo_path = (case_dir / repo_path).resolve()
    if not repo_path.is_dir():
        raise CaseError(f"{path}: repo {repo!r} is not a directory ({repo_path})")
    base = _case_text(data, "base", path)
    result = _case_text(data, "result", path)
    plan = data.get("plan", "plan.md")
    if not isinstance(plan, str) or not plan:
        raise CaseError(f"{path}: plan must be a non-empty file name, got {plan!r}")
    plan_path = case_dir / plan
    if not plan_path.is_file():
        raise CaseError(f"{path}: plan file {plan!r} not found in {case_dir}")
    hidden = data["hidden_tests"]
    if (
        not isinstance(hidden, list)
        or not hidden
        or not all(isinstance(p, str) and p for p in hidden)
    ):
        raise CaseError(
            f"{path}: hidden_tests must be a non-empty list of non-empty strings, "
            f"got {hidden!r}"
        )
    for entry in hidden:
        bad = (
            entry.startswith("/")
            or "\\" in entry
            or re.match(r"^[A-Za-z]:", entry)
            or ".." in Path(entry).parts
            or entry.strip() != entry
        )
        if bad:
            raise CaseError(
                f"{path}: hidden_tests entry {entry!r} must be a repo-relative POSIX "
                "path (no '..', no absolute path, no backslash)"
            )
    commands = data["commands"]
    if (
        not isinstance(commands, list)
        or not commands
        or not all(isinstance(c, str) and c.strip() for c in commands)
    ):
        raise CaseError(
            f"{path}: commands must be a non-empty list of command lines, "
            f"got {commands!r}"
        )
    return {
        "id": cid,
        "size": size,
        "repo": repo_path,
        "repo_raw": repo,
        "base": base,
        "result": result,
        "plan": plan_path,
        "hidden_tests": list(hidden),
        "commands": list(commands),
        "case_dir": case_dir,
    }


def load_cases(cases_dir: Path) -> dict:
    """Every valid case folder under cases_dir, keyed by id (CaseError on invalid)."""
    cases: dict[str, dict] = {}
    if not cases_dir.is_dir():
        return cases
    for child in sorted(cases_dir.iterdir()):
        if child.is_dir() and (child / "case.toml").is_file():
            case = load_case(child)
            cases[case["id"]] = case
    return cases


def select_cases(cases_dir: Path, spec: str | None) -> dict:
    all_cases = load_cases(cases_dir)
    if spec is None or spec == "all":
        return all_cases
    ids = [s.strip() for s in spec.split(",") if s.strip()]
    if not ids:
        raise BenchError("--cases is empty")
    unknown = [i for i in ids if i not in all_cases]
    if unknown:
        known = ", ".join(sorted(all_cases)) or "none"
        raise BenchError(
            f"unknown case {unknown[0]!r} in --cases (known cases: {known})"
        )
    return {i: all_cases[i] for i in ids}


def hidden_files(case: dict) -> list[str]:
    """The hidden_tests entries expanded to files at the result commit."""
    files: list[str] = []
    for entry in case["hidden_tests"]:
        done = git(
            case["repo"],
            "ls-tree",
            "-r",
            "--name-only",
            case["result_commit"],
            "--",
            entry,
            check=False,
        )
        if done.returncode != 0:
            err = done.stderr.decode("utf-8", "replace").strip()
            raise BenchError(
                f"case {case['id']}: git ls-tree at {case['result_commit']} failed "
                f"for {entry!r}: {one_line(err)}"
            )
        found = [
            line for line in done.stdout.decode("utf-8", "replace").splitlines() if line
        ]
        if not found:
            raise BenchError(
                f"case {case['id']}: hidden_tests entry {entry!r} matches no file at "
                f"result {case['result_commit']}"
            )
        files.extend(found)
    return files


# ---------------------------------------------------------------------------- fingerprint (D9)


def fingerprint(cases: dict, repeat: int, review: bool) -> tuple[str, dict]:
    """The suite fingerprint and its payload; commits are resolved to full ids."""
    entries = {}
    for cid in sorted(cases):
        case = cases[cid]
        entries[cid] = {
            "case_toml_sha256": sha256_text_file(case["case_dir"] / "case.toml"),
            "plan_sha256": sha256_text_file(case["plan"]),
            "base": resolve_commit(case["repo"], case["base"]),
            "result": resolve_commit(case["repo"], case["result"]),
        }
    payload = {
        "harness_version": HARNESS_VERSION,
        "repeat": int(repeat),
        "review": bool(review),
        "cases": entries,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(), payload


def fingerprint_diff(old_payload: dict, new_payload: dict) -> str | None:
    """The first differing component of two fingerprint payloads, or None."""
    for key in ("harness_version", "repeat", "review"):
        if old_payload.get(key) != new_payload.get(key):
            return f"{key} differs (the run has {old_payload.get(key)!r}, now {new_payload.get(key)!r})"
    old_cases, new_cases = old_payload.get("cases", {}), new_payload.get("cases", {})
    for cid in sorted(set(old_cases) | set(new_cases)):
        if cid not in old_cases:
            return f"case {cid} is new"
        if cid not in new_cases:
            return f"case {cid} is gone"
        for field in ("case_toml_sha256", "plan_sha256", "base", "result"):
            if old_cases[cid].get(field) != new_cases[cid].get(field):
                return (
                    f"case {cid}: {field} differs (the case's case.toml or plan.md "
                    "changed since the run was created)"
                )
    return None


# ---------------------------------------------------------------------------- export (D1)


def export_base(repo: Path, base: str, dest: Path) -> str:
    """Export <base> of <repo> into a fresh git repository at <dest>; return its commit."""
    repo, dest = Path(repo), Path(dest)
    if dest.exists():
        rmtree_force(dest)
    dest.mkdir(parents=True)
    archive = git(repo, "archive", "--format=tar", base).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        tar.extractall(dest, filter="data")
    git(dest, "init", "-q", "-b", "bench")
    git(dest, "-c", "core.autocrlf=false", "add", "-A")
    tree = git(dest, "write-tree").stdout.decode("ascii").strip()
    commit_text = (
        f"tree {tree}\n"
        "author task-flow bench <bench@localhost> 0 +0000\n"
        "committer task-flow bench <bench@localhost> 0 +0000\n"
        f"\nbench base {base}\n"
    )
    done = git(
        dest,
        "hash-object",
        "-t",
        "commit",
        "-w",
        "--stdin",
        input=commit_text.encode("utf-8"),
    )
    commit = done.stdout.decode("ascii").strip()
    git(dest, "update-ref", "refs/heads/bench", commit)
    return commit


# ---------------------------------------------------------------------------- checks files (D5)


def _strip_code(cell: str) -> str:
    cell = cell.strip()
    match = re.match(r"^`+(.*?)`+$", cell, re.DOTALL)
    return match.group(1) if match else cell


def parse_checks(text: str) -> dict:
    """Parse a checks-<n>.md: number -> {command, result, exit, excerpt}."""
    checks: dict[int, dict] = {}
    section = "table"
    current: dict | None = None
    fence: int | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line.strip()[3:].lower()
            current, fence = None, None
            continue
        if section == "failures":
            head = FAILURE_HEAD.match(line)
            if head:
                number = int(head.group(1))
                current = checks.setdefault(
                    number,
                    {
                        "command": _strip_code(head.group(2)),
                        "result": "FAIL",
                        "exit": "-",
                        "excerpt": [],
                    },
                )
                fence = None
                continue
            if current is None:
                continue
            mark = FENCE.match(line)
            if mark:
                if fence is None:
                    fence = len(mark.group(0))
                elif len(mark.group(0)) == fence:
                    current, fence = None, None
                continue
            if fence is not None:
                current["excerpt"].append(line)
            continue
        if section == "not run":
            entry = NOT_RUN.match(line)
            if entry:
                number = int(entry.group(1))
                head, _sep, reason = entry.group(2).partition(" — ")
                checks[number] = {
                    "command": _strip_code(head),
                    "result": "NOTRUN",
                    "exit": "-",
                    "excerpt": [reason],
                }
            continue
        row = TABLE_ROW.match(line)
        if row:
            number = int(row.group(1))
            checks[number] = {
                "command": _strip_code(row.group(2)),
                "result": row.group(3),
                "exit": row.group(4).strip(),
                "excerpt": [],
            }
    return checks


def introduced(checks0: dict, checksn: dict) -> list[int]:
    """The numbers whose FAIL/ERROR in checks-<n> is not pre-existing in checks-0."""
    out = []
    for number in sorted(checksn):
        row = checksn[number]
        if row["result"] not in ("FAIL", "ERROR"):
            continue
        base = checks0.get(number)
        if (
            base is None
            or base["result"] not in ("FAIL", "ERROR")
            or base["command"] != row["command"]
            or not all(line in base["excerpt"] for line in row["excerpt"])
        ):
            out.append(number)
    return out


def fix_list_review(round_n: int, checksn: dict, failing: list[int]) -> str:
    """A contract-shaped review-<n>.md whose Fix list is one item per introduced failure."""
    lines = [
        f"# Review: round {round_n}",
        "",
        "## Acceptance criteria",
        "not checked: bench runs without a reviewer",
        "",
        "## Pre-existing failures",
    ]
    preexisting = [
        n
        for n in sorted(checksn)
        if checksn[n]["result"] in ("FAIL", "ERROR") and n not in failing
    ]
    if preexisting:
        for number in preexisting:
            lines.append(
                f"- #{number} `{checksn[number]['command']}` (pre-existing in checks-0.md)"
            )
    else:
        lines.append("none")
    lines += [
        "",
        "## Findings",
        "none",
        "",
        "## Scope",
        "not checked",
        "",
        "## Verdict: CHANGES_REQUESTED",
        "",
        "## Fix list",
    ]
    for number in failing:
        row = checksn[number]
        lines.append(
            f"Make `{row['command']}` pass (checks-{round_n}.md #{number}, "
            f"{row['result']}, exit {row['exit']}):"
        )
        lines.extend(row["excerpt"][:30])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- scope (D5 step 4)


def _section_lines(plan_text: str, name: str) -> list[str]:
    lines, inside = [], False
    for line in plan_text.splitlines():
        if line.startswith("## "):
            inside = line.strip().lower() == f"## {name}"
            continue
        if inside:
            lines.append(line)
    return lines


def plan_tokens(plan_text: str) -> list[str]:
    """The tokens of the plan's Profiles and Steps sections: backtick spans plus
    comma/whitespace-separated words containing '/' or '.'."""
    tokens: list[str] = []
    for section in ("profiles", "steps"):
        text = "\n".join(_section_lines(plan_text, section))
        for match in re.finditer(r"`([^`]*)`", text):
            token = match.group(1).strip()
            if token and token not in tokens:
                tokens.append(token)
        for word in re.split(r"[,\s]+", text):
            if ("/" in word or "." in word) and word not in tokens:
                tokens.append(word)
    return tokens


def in_scope(path: str, plan_text: str) -> bool:
    """True when a plan token equals the path, is a parent directory of it, or
    fnmatch-matches it. A heuristic, never used to fail a run."""
    path = path.replace("\\", "/")
    for token in plan_tokens(plan_text):
        token = token.strip("`")
        if not token:
            continue
        if path == token or path.startswith(token.rstrip("/") + "/"):
            return True
        if fnmatch.fnmatchcase(path, token):
            return True
    return False


# ---------------------------------------------------------------------------- report (D10)


REPORT_HEADER = (
    "| Profile | Billing | Runs | Accepted | Round 1 | Within 3 | Rounds (median) | "
    "Hidden pass | Out of scope | Tokens in / cached / out (median per run) | "
    "Cost per run median (min–max) | Cost per accepted | Seconds median (min–max) |"
)


def _profile_rows(rows: list, size: str) -> list[str]:
    seen: list[str] = []
    for row in rows:
        if row["size"] == size and row["profile"] not in seen:
            seen.append(row["profile"])
    return seen


def _size_table(rows: list, size: str) -> list[str]:
    lines = [REPORT_HEADER, "| " + " --- | " * 12 + "--- |"]
    for name in _profile_rows(rows, size):
        prows = [r for r in rows if r["size"] == size and r["profile"] == name]
        runs = len(prows)
        accepted = [r for r in prows if r["outcome"] == "accepted"]
        round1 = len([r for r in accepted if r["accepted_round"] == 1])
        done = [r for r in prows if r["outcome"] != "skipped"]
        partial = [
            r for r in prows if r["outcome"] in ("skipped", "unavailable", "cost_cap")
        ]
        label = name + (" (partial)" if partial else "")
        billing = next(
            (r["billing"] for r in prows if r["outcome"] != "skipped"),
            prows[0]["billing"],
        )
        known = next(
            (r["cost_known"] for r in prows if r["outcome"] != "skipped"),
            prows[0]["cost_known"],
        )
        rounds_med = fmt_num(median([r["rounds"] for r in done]))
        hidden_pass = f"{sum(1 for r in done if r['hidden'] == 'pass')}/{len(done)}"
        out_of_scope = sum(len(r["out_of_scope"]) for r in done)
        tokens = " / ".join(
            fmt_num(median([r["tokens"][k] for r in done]))
            for k in ("input", "cached", "output")
        )
        if known:
            costs = [r["cost_usd"] + r["review_cost_usd"] for r in done]
            per_run = (
                f"{fmt_usd(median(costs))} ({fmt_usd(min(costs))}–{fmt_usd(max(costs))})"
                if costs
                else "n/a"
            )
            if accepted:
                total = sum(r["cost_usd"] + r["review_cost_usd"] for r in prows)
                per_accepted = fmt_usd(total / len(accepted))
            else:
                per_accepted = "n/a (0 accepted)"
        else:
            per_run = per_accepted = "subscription"
        seconds = [r["seconds"] for r in done]
        secs = (
            f"{median(seconds):.1f}s ({min(seconds):.1f}s–{max(seconds):.1f}s)"
            if seconds
            else "n/a"
        )
        lines.append(
            f"| {label} | {billing} | {runs} | {len(accepted)} | {round1} | "
            f"{len(accepted)} | {rounds_med} | {hidden_pass} | {out_of_scope} | "
            f"{tokens} | {per_run} | {per_accepted} | {secs} |"
        )
    return lines


def render_report(rows: list, meta: dict) -> str:
    """The deterministic report.md for one run's rows (D10)."""
    rows = sorted(rows, key=lambda r: r["n"])
    lines = [
        f"# Benchmark report: {meta['run_id']}",
        "",
        f"- fingerprint: `{meta['fingerprint']}`",
        f"- harness version: {meta['harness_version']}",
        f"- repeat: {meta['repeat']}",
        f"- review: {'on' if meta['review'] else 'off'}",
        f"- cases: {', '.join(meta['cases'])}",
        "",
    ]
    sizes = [s for s in SIZES if any(r["size"] == s for r in rows)]
    for size in [*sizes, "All"]:
        lines.append(f"## {size}")
        lines.append("")
        lines.extend(_size_table(rows, size))
        lines.append("")
    lines.append("## Notes")
    notes: list[str] = []
    if not meta["review"]:
        notes.append("review cost is 0 because no reviewer ran (`--review` was off)")
    for row in rows:
        if row["outcome"] != "skipped" and not row["cost_known"]:
            notes.append(
                f"profile `{row['profile']}` bills by subscription, so its cost per "
                "token is unknown; it is compared on tokens, rounds, time and acceptance"
            )
            break
    partial: dict[str, list[str]] = {}
    for row in rows:
        if row["outcome"] in ("skipped", "unavailable", "cost_cap"):
            partial.setdefault(row["profile"], []).append(
                f"{row['outcome']}: {row['reason']}"
            )
    for name in sorted(partial):
        reasons = "; ".join(sorted(set(partial[name])))
        notes.append(f"profile `{name}` is partial — {reasons}")
    if notes:
        for note in notes:
            lines.append(f"- {note}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Failures")
    failures = [r for r in rows if r["outcome"] != "accepted" or r["hidden"] == "fail"]
    if failures:
        for row in failures:
            lines.append(
                f"- n={row['n']} case={row['case']} profile={row['profile']} "
                f"repeat={row['repeat']} outcome={row['outcome']} hidden={row['hidden']} "
                f"— {one_line(str(row['reason']), 200)}"
            )
    else:
        lines.append("none")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- the runner


def resolve_project(project_arg: str | None) -> Path:
    if project_arg:
        path = Path(project_arg).resolve()
        if not path.is_dir():
            raise BenchError(f"--project is not a directory: {project_arg}")
        return path
    done = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, check=False
    )
    if done.returncode != 0:
        raise BenchError("cannot find the repository root; pass --project <dir>")
    return Path(done.stdout.decode("utf-8", "replace").strip())


class Runner:
    """One `bench.py run` (fresh or --extend): drives the runs and writes the rows."""

    def __init__(self, args, project: Path, cases: dict, profile_names: list):
        self.args = args
        self.project = project
        self.cases = cases
        self.profile_names = profile_names
        self.out = (
            Path(args.out).resolve() if args.out else project / ".work" / "_bench"
        )
        self.run_id = args.extend or args.run_id or utc_now().strftime("%Y%m%dT%H%M%SZ")
        if not RUN_ID.match(self.run_id):
            raise BenchError(f"invalid run id {self.run_id!r}")
        self.run_dir = self.out / self.run_id
        self.repeat = args.repeat
        self.review = bool(args.review)
        self.review_model = args.review_model
        self.max_cost_usd = args.max_cost_usd
        self.timeout_seconds = args.timeout_seconds
        self.keep_code = args.keep_code
        self.profiles, _roles = models.load(project)
        self.results_path = self.run_dir / "results.jsonl"
        self.rows: list[dict] = []
        self.spent = 0.0
        self.unavailable: dict[str, int] = {}
        self.cost_capped = False
        self.fingerprint = ""
        self.meta: dict = {}

    def log(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    # ---------------------------------------------------------------- models file (D4)

    def settings_for(self, name: str, remaining: float | None) -> dict:
        profile = self.profiles[name]
        settings: dict = {}
        for key in models.PROFILE_KEYS:
            value = profile[key]
            if key == "max_cost_usd" and remaining is not None:
                value = min(value, remaining) if value is not None else remaining
            if value is None:
                continue
            if key == "fallback":
                value = []
            elif key == "prompt_extras" and value:
                value = [
                    str(
                        Path(p).expanduser()
                        if Path(p).expanduser().is_absolute()
                        else self.project / p
                    )
                    for p in value
                ]
            settings[key] = value
        return settings

    def write_models(self, run_dir: Path, name: str, remaining: float | None) -> dict:
        settings = self.settings_for(name, remaining)
        lines = [f"[profiles.{name}]"]
        for key, value in settings.items():
            lines.append(f"{key} = {toml_value(value)}")
        write_text(
            run_dir / ".claude" / "task-flow.models.toml", "\n".join(lines) + "\n"
        )
        return settings

    def remaining_budget(self) -> float | None:
        if self.max_cost_usd is None:
            return None
        return max(0.0, self.max_cost_usd - self.spent)

    # ---------------------------------------------------------------- subprocesses

    def gates(self, task_dir: Path, worktree: Path, round_n: int):
        cmd = [
            sys.executable,
            str(SCRIPTS / "gates.py"),
            "--task-dir",
            str(task_dir),
            "--worktree",
            str(worktree),
            "--round",
            str(round_n),
        ]
        if self.timeout_seconds is not None:
            cmd += ["--timeout-seconds", str(self.timeout_seconds)]
        return subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace", check=False
        )

    def implement(self, task_dir: Path, worktree: Path, round_n: int, name: str):
        cmd = [
            sys.executable,
            str(SCRIPTS / "implement.py"),
            "--task-dir",
            str(task_dir),
            "--worktree",
            str(worktree),
            "--round",
            str(round_n),
            "--profile",
            name,
            "--heartbeat",
            "0",
        ]
        return subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace", check=False
        )

    # ---------------------------------------------------------------- reviewer (D7)

    def reviewer_body(self) -> tuple[str, str]:
        text = (PLUGIN_ROOT / "agents" / "reviewer.md").read_text(encoding="utf-8")
        model = "opus"
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                frontmatter, text = text[:end], text[end + 4 :]
                match = re.search(r"^model:\s*(\S+)", frontmatter, re.MULTILINE)
                if match:
                    model = match.group(1)
        body = text.strip().replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN_ROOT.as_posix())
        return body, model

    def review_round(self, round_n: int, task: Path, code: Path, base: str):
        """Run the reviewer through --claude-command; return (verdict, cost, tokens, error)."""
        body, default_model = self.reviewer_body()
        prompt = (
            f"Review round {round_n} of the task-flow task in {task} (worktree {code}, "
            f"base commit {base}). Read the plan, the diff and checks-{round_n}.md, then "
            f"write {task}/review-{round_n}.md in the format of "
            "skills/workflow/SKILL.md and return the verdict line."
        )
        argv = [str(a) for a in self.args.claude_command] + [
            "-p",
            prompt,
            "--output-format",
            "json",
            "--model",
            self.review_model or default_model,
            "--append-system-prompt",
            body,
            "--allowedTools",
            "Read",
            "Grep",
            "Glob",
            "Write",
            "Bash(git diff:*)",
            "Bash(git status:*)",
            "Bash(git show:*)",
            "--add-dir",
            str(task),
        ]
        remaining = self.remaining_budget()
        if self.max_cost_usd is not None:
            argv += ["--max-budget-usd", f"{remaining:.6f}"]
        program, rest = argv[0], argv[1:]
        if os.name == "nt" and launch.is_cmd_script(program):
            popen = [
                launch.cmd_command_line(
                    program,
                    " " + subprocess.list2cmdline(rest),
                    launch.cmd_exe(os.environ),
                )
            ]
        else:
            popen = [program, *rest]
        done = subprocess.run(
            popen,
            cwd=code,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            check=False,
        )
        cost, tokens = 0.0, {"input": 0, "cached": 0, "output": 0}
        try:
            payload = json.loads(done.stdout)
            if isinstance(payload, dict):
                cost = float(payload.get("total_cost_usd") or 0.0)
                usage = payload.get("usage") or {}
                tokens = {
                    "input": int(usage.get("input_tokens") or 0),
                    "cached": int(usage.get("cache_read_input_tokens") or 0),
                    "output": int(usage.get("output_tokens") or 0),
                }
        except (ValueError, TypeError):
            pass
        review_path = task / f"review-{round_n}.md"
        if done.returncode != 0:
            return (
                None,
                cost,
                tokens,
                (
                    f"reviewer exited {done.returncode}: {one_line(done.stderr or done.stdout, 200)}"
                ),
            )
        if not review_path.is_file():
            return None, cost, tokens, f"the reviewer wrote no {review_path.name}"
        verdict = VERDICT.search(review_path.read_text(encoding="utf-8"))
        if not verdict:
            return None, cost, tokens, f"no ## Verdict: line in {review_path}"
        return verdict.group(1), cost, tokens, None

    # ---------------------------------------------------------------- one run (D5)

    def seed_task(
        self, run_dir: Path, case: dict, scratch_base: str
    ) -> tuple[Path, Path]:
        task = run_dir / ".work" / case["id"]
        hidden = run_dir / ".work" / "_hidden"
        write_text(task / "plan.md", case["plan"].read_text(encoding="utf-8"))
        write_text(
            task / "status.md",
            "\n".join(
                [
                    f"# Status: {case['id']}",
                    "- phase: implementing",
                    "- round: 0",
                    f"- base: {scratch_base}",
                    f"- plan-approved: yes (bench {self.run_id})",
                    "## History",
                    f"- {iso_now()} bench run {self.run_id}: seeded from case {case['id']}",
                    "## Log",
                    "",
                ]
            ),
        )
        hidden_plan = "\n".join(
            [
                f"# Plan: hidden checks for case {case['id']}",
                "## Goal",
                "Run the case's hidden acceptance commands on the implemented code.",
                "## Verification commands",
                "```",
                *case["commands"],
                "```",
                "",
            ]
        )
        write_text(hidden / "plan.md", hidden_plan)
        return task, hidden

    def run_one(self, case: dict, name: str, repeat: int, n: int) -> dict:
        started = time.monotonic()
        started_at = iso_now()
        run_dir = self.run_dir / "runs" / str(n)
        code = run_dir / "code"
        scratch_base = export_base(case["repo"], case["base_commit"], code)
        task, hidden_task = self.seed_task(run_dir, case, scratch_base)
        plan_text = (task / "plan.md").read_text(encoding="utf-8")

        gates0 = self.gates(task, code, 0)
        if gates0.returncode != 0:
            return self.finish_row(
                case,
                name,
                repeat,
                n,
                started,
                started_at,
                "error",
                f"gates.py refused round 0: {one_line(gates0.stderr.strip(), 200)}",
                rounds=0,
                green=False,
            )
        checks0 = parse_checks((task / "checks-0.md").read_text(encoding="utf-8"))

        outcome, reason = None, ""
        accepted_round = None
        rounds_done = 0
        last_green = False
        failing_sets: list[set] = []
        tokens = {"input": 0, "cached": 0, "output": 0}
        cache_tokens = {"read": 0, "write": 0}
        cost = 0.0
        review_cost = 0.0
        review_tokens = {"input": 0, "cached": 0, "output": 0}
        implement_seconds = 0.0
        settings = self.settings_for(name, self.remaining_budget())

        for round_n in range(1, 4):
            self.write_models(run_dir, name, self.remaining_budget())
            done = self.implement(task, code, round_n, name)
            rounds_done = round_n
            if done.returncode in (0, 1, 3):
                # implement.py writes impl-<n>.json on exit 0, 1 and 3, so a
                # failed, cost-capped or blocked round still spent tokens and
                # money: count its metrics before branching on the exit code.
                metrics_path = task / f"impl-{round_n}.json"
                if metrics_path.exists():
                    metrics = read_json(metrics_path)
                    for key in tokens:
                        tokens[key] += metrics.get("tokens", {}).get(key, 0)
                    cache = metrics.get("cache_tokens") or {}
                    cache_tokens["read"] += cache.get("read", 0)
                    cache_tokens["write"] += cache.get("write", 0)
                    cost += metrics.get("cost_usd", 0.0)
                    implement_seconds += metrics.get("duration_seconds", 0.0)
            if done.returncode == 0:
                settings = self.settings_for(name, self.remaining_budget())
                gatesn = self.gates(task, code, round_n)
                if gatesn.returncode != 0:
                    outcome, reason = (
                        "error",
                        (
                            f"gates.py refused round {round_n}: {one_line(gatesn.stderr.strip(), 200)}"
                        ),
                    )
                    break
                checksn = parse_checks(
                    (task / f"checks-{round_n}.md").read_text(encoding="utf-8")
                )
                failing = introduced(checks0, checksn)
                green = not failing
                last_green = green
                failing_set: set = set(failing)
                if green and not self.review:
                    outcome, accepted_round = "accepted", round_n
                    break
                if green:
                    verdict, rcost, rtok, error = self.review_round(
                        round_n, task, code, scratch_base
                    )
                    review_cost += rcost
                    for key in review_tokens:
                        review_tokens[key] += rtok[key]
                    if error:
                        outcome, reason = "error", error
                        break
                    if verdict == "APPROVED":
                        outcome, accepted_round = "accepted", round_n
                        break
                    review_text = (task / f"review-{round_n}.md").read_text(
                        encoding="utf-8"
                    )
                    failing_set |= set(AC_FAIL.findall(review_text))
                elif round_n < 3:
                    write_text(
                        task / f"review-{round_n}.md",
                        fix_list_review(round_n, checksn, failing),
                    )
                failing_sets.append(failing_set)
                if round_n >= 2 and failing_set and failing_set == failing_sets[-2]:
                    outcome = "no_progress"
                    reason = (
                        f"no progress: the failing set of round {round_n} equals round "
                        f"{round_n - 1}'s ({sorted(map(str, failing_set))})"
                    )
                    break
                if round_n == 3:
                    outcome = "round_cap"
                    reason = f"round 3 was not accepted (failing: {sorted(map(str, failing_set))})"
                    break
            elif done.returncode == 2:
                outcome = "error"
                reason = one_line(done.stdout.strip(), 300)
                break
            elif done.returncode == 3:
                outcome = "blocked"
                reason = one_line(done.stdout.strip(), 300)
                break
            else:
                line = one_line(done.stdout.strip(), 300)
                reason = line.removeprefix("failed: ")
                if reason.startswith("all profiles unavailable"):
                    outcome = "unavailable"
                elif reason.startswith("max_cost_usd"):
                    outcome = "cost_cap"
                else:
                    outcome = "failed"
                break
        if outcome is None:
            outcome, reason = "error", "the round loop ended without an outcome"

        out_of_scope = self.out_of_scope(code, plan_text)
        hidden = self.run_hidden(case, code, hidden_task, last_green)
        return self.finish_row(
            case,
            name,
            repeat,
            n,
            started,
            started_at,
            outcome,
            reason,
            rounds=rounds_done,
            accepted_round=accepted_round,
            green=last_green,
            hidden=hidden,
            out_of_scope=out_of_scope,
            tokens=tokens,
            cache_tokens=cache_tokens,
            cost=cost,
            review_cost=review_cost,
            review_tokens=review_tokens,
            implement_seconds=implement_seconds,
            settings=settings,
        )

    def out_of_scope(self, code: Path, plan_text: str) -> list[str]:
        done = git(code, "diff", "--cached", "--name-only", "HEAD", check=False)
        if done.returncode != 0:
            return []
        paths = [
            line for line in done.stdout.decode("utf-8", "replace").splitlines() if line
        ]
        return sorted(p for p in paths if not in_scope(p, plan_text))

    def run_hidden(self, case: dict, code: Path, hidden_task: Path, green: bool) -> str:
        if not green:
            return "not_run"
        for rel in case["hidden_files"]:
            data = git(case["repo"], "show", f"{case['result_commit']}:{rel}").stdout
            target = code / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        done = self.gates(hidden_task, code, 1)
        if done.returncode != 0:
            return "fail"
        checks = parse_checks((hidden_task / "checks-1.md").read_text(encoding="utf-8"))
        bad = sum(
            1 for row in checks.values() if row["result"] in ("FAIL", "ERROR", "NOTRUN")
        )
        return "pass" if bad == 0 else "fail"

    def finish_row(
        self,
        case,
        name,
        repeat,
        n,
        started,
        started_at,
        outcome,
        reason,
        rounds,
        green,
        hidden="not_run",
        out_of_scope=None,
        tokens=None,
        cache_tokens=None,
        cost=0.0,
        review_cost=0.0,
        review_tokens=None,
        implement_seconds=0.0,
        accepted_round=None,
        settings=None,
    ) -> dict:
        profile = self.profiles[name]
        row = {
            "run_id": self.run_id,
            "n": n,
            "case": case["id"],
            "size": case["size"],
            "profile": name,
            "repeat": repeat,
            "outcome": outcome,
            "reason": reason,
            "rounds": rounds,
            "accepted_round": accepted_round,
            "green": green,
            "hidden": hidden,
            "out_of_scope": out_of_scope or [],
            "tokens": tokens or {"input": 0, "cached": 0, "output": 0},
            "cache_tokens": cache_tokens or {"read": 0, "write": 0},
            "cost_usd": round(cost, 6),
            "review_cost_usd": round(review_cost, 6),
            "review_tokens": review_tokens or {"input": 0, "cached": 0, "output": 0},
            "cost_known": profile["billing"] in ("api", "local"),
            "billing": profile["billing"],
            "model": profile["model"],
            "settings": settings
            if settings is not None
            else self.settings_for(name, None),
            "seconds": round(time.monotonic() - started, 3),
            "implement_seconds": round(implement_seconds, 3),
            "started_at": started_at,
            "fingerprint": self.fingerprint,
            "harness_version": HARNESS_VERSION,
        }
        return row

    def skipped_row(self, case, name, repeat, n, reason) -> dict:
        started = time.monotonic()
        row = self.finish_row(
            case,
            name,
            repeat,
            n,
            started,
            iso_now(),
            "skipped",
            reason,
            rounds=0,
            green=False,
        )
        return row

    # ---------------------------------------------------------------- the whole run

    def append_row(self, row: dict) -> None:
        self.rows.append(row)
        with open(self.results_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        self.spent += row["cost_usd"] + row["review_cost_usd"]
        if row["outcome"] == "unavailable":
            self.unavailable.setdefault(row["profile"], row["n"])
        if row["outcome"] == "cost_cap":
            self.cost_capped = True
        self.log(
            f"run {row['n']}: case {row['case']} profile {row['profile']} "
            f"repeat {row['repeat']} -> {row['outcome']} ({one_line(str(row['reason']), 120)})"
        )

    def run(self) -> int:
        next_n = len(self.rows) + 1
        for repeat in range(1, self.repeat + 1):
            for cid in sorted(self.cases):
                case = self.cases[cid]
                for name in self.profile_names:
                    n = next_n
                    next_n += 1
                    if name in self.unavailable:
                        row = self.skipped_row(
                            case,
                            name,
                            repeat,
                            n,
                            f"profile unavailable in run {self.unavailable[name]}",
                        )
                    elif self.cost_capped or (
                        self.max_cost_usd is not None
                        and self.spent >= self.max_cost_usd
                    ):
                        row = self.skipped_row(case, name, repeat, n, "cost cap")
                    else:
                        row = self.run_one(case, name, repeat, n)
                    if not self.keep_code:
                        rmtree_force(self.run_dir / "runs" / str(n) / "code")
                    self.append_row(row)
        report = render_report(self.rows, self.meta)
        write_text(self.run_dir / "report.md", report)
        accepted = sum(1 for r in self.rows if r["outcome"] == "accepted")
        total = sum(r["cost_usd"] + r["review_cost_usd"] for r in self.rows)
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(
            f"bench {self.run_id}: {len(self.rows)} runs, {accepted} accepted, "
            f"{fmt_usd(total)} -> {self.run_dir / 'report.md'}"
        )
        return 0


# ---------------------------------------------------------------------------- commands


def cmd_list(args) -> int:
    cases_dir = Path(args.cases_dir) if args.cases_dir else DEFAULT_CASES_DIR
    found = 0
    if cases_dir.is_dir():
        for child in sorted(cases_dir.iterdir()):
            if child.is_dir() and (child / "case.toml").is_file():
                try:
                    case = load_case(child)
                except CaseError as exc:
                    print(f"bench: {exc}", file=sys.stderr)
                    return 2
                found += 1
                print(
                    f"{case['id']}  {case['size']}  {case['repo_raw']}  "
                    f"{case['base']}  {case['result']}"
                )
    if not found:
        print(f"no cases in {cases_dir}")
    return 0


def _split_profiles(spec: str) -> list[str]:
    names = [s.strip() for s in spec.split(",") if s.strip()]
    if not names:
        raise BenchError("--profiles is empty")
    if len(set(names)) != len(names):
        raise BenchError(f"duplicate profile in --profiles: {spec!r}")
    return names


def cmd_run(args) -> int:
    project = resolve_project(args.project)
    cases_dir = Path(args.cases_dir) if args.cases_dir else DEFAULT_CASES_DIR
    extend = args.extend
    out = Path(args.out).resolve() if args.out else project / ".work" / "_bench"

    if extend:
        run_dir = out / extend
        if not (run_dir / "run.json").is_file():
            raise BenchError(f"no run {extend!r} under {out} (nothing to extend)")
        meta = read_json(run_dir / "run.json")
        if args.repeat is not None and args.repeat != meta["repeat"]:
            raise BenchError(
                f"--repeat differs from run {extend} (the run has {meta['repeat']}, "
                f"got {args.repeat})"
            )
        if args.review and not meta["review"]:
            raise BenchError(
                f"--review differs from run {extend} (the run was created without --review)"
            )
        if args.cases is not None and args.cases != "all":
            wanted = {s.strip() for s in args.cases.split(",") if s.strip()}
            if wanted != set(meta["cases"]):
                raise BenchError(
                    f"--cases differs from run {extend} (the run has {sorted(meta['cases'])})"
                )
        repeat = meta["repeat"]
        review = bool(meta["review"])
        cases = select_cases(
            cases_dir, args.cases if args.cases is not None else ",".join(meta["cases"])
        )
        if not cases:
            raise BenchError(f"no cases in {cases_dir}")
    else:
        repeat = args.repeat if args.repeat is not None else 1
        review = bool(args.review)
        cases = select_cases(cases_dir, args.cases)
        if not cases:
            raise BenchError(f"no cases in {cases_dir}")

    for case in cases.values():
        case["base_commit"] = resolve_commit(case["repo"], case["base"])
        case["result_commit"] = resolve_commit(case["repo"], case["result"])
        case["hidden_files"] = hidden_files(case)

    profiles_all, _roles = models.load(project)
    names = _split_profiles(args.profiles)
    for name in names:
        if name not in profiles_all:
            known = ", ".join(sorted(profiles_all)) or "none"
            raise BenchError(f"unknown profile {name!r} (known profiles: {known})")
        profile = profiles_all[name]
        if not profile["enabled"]:
            raise BenchError(f"profile {name!r} is disabled (enabled = false)")
        if profile["api_key_env"] and not models.api_key(profile):
            raise BenchError(
                f"profile {name!r}: environment variable {profile['api_key_env']} is not set"
            )

    fp, payload = fingerprint(cases, repeat, review)

    if extend:
        if not RUN_ID.match(extend):
            raise BenchError(f"invalid run id {extend!r}")
        old_payload = {
            "harness_version": meta.get("harness_version"),
            "repeat": meta.get("repeat"),
            "review": meta.get("review"),
            "cases": meta.get("case_entries", {}),
        }
        if meta.get("fingerprint") != fp:
            diff = fingerprint_diff(old_payload, payload) or "the suite changed"
            raise BenchError(f"fingerprint mismatch: {diff}")
        for name in names:
            if name in meta["profiles"]:
                raise BenchError(f"profile {name!r} is already in run {extend}")
        run_id = extend
        run_dir = out / run_id
        meta["profiles"] = [*meta["profiles"], *names]
        meta["fingerprint"] = fp
        write_text(
            run_dir / "run.json", json.dumps(meta, indent=2, sort_keys=True) + "\n"
        )
        rows = [
            json.loads(line)
            for line in (run_dir / "results.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
    else:
        run_id = args.run_id or utc_now().strftime("%Y%m%dT%H%M%SZ")
        if not RUN_ID.match(run_id):
            raise BenchError(f"invalid run id {run_id!r}")
        run_dir = out / run_id
        if run_dir.exists():
            raise BenchError(
                f"run id {run_id!r} already exists under {out} (use --extend {run_id})"
            )
        meta = {
            "run_id": run_id,
            "created": iso_now(),
            "harness_version": HARNESS_VERSION,
            "repeat": repeat,
            "review": review,
            "cases": sorted(cases),
            "case_entries": payload["cases"],
            "profiles": names,
            "fingerprint": fp,
        }
        run_dir.mkdir(parents=True)
        write_text(
            run_dir / "run.json", json.dumps(meta, indent=2, sort_keys=True) + "\n"
        )
        (run_dir / "results.jsonl").write_text("", encoding="utf-8", newline="\n")
        rows = []

    runner = Runner(args, project, cases, names)
    runner.out = out
    runner.run_id = run_id
    runner.run_dir = run_dir
    runner.repeat = repeat
    runner.review = review
    runner.results_path = run_dir / "results.jsonl"
    runner.rows = rows
    runner.spent = sum(r["cost_usd"] + r["review_cost_usd"] for r in rows)
    runner.fingerprint = fp
    runner.meta = meta
    return runner.run()


def cmd_report(args) -> int:
    project = resolve_project(getattr(args, "project", None))
    out = Path(args.out).resolve() if args.out else project / ".work" / "_bench"
    run_dir = out / args.run_id
    if (
        not (run_dir / "results.jsonl").is_file()
        or not (run_dir / "run.json").is_file()
    ):
        raise BenchError(f"no run {args.run_id!r} under {out}")
    meta = read_json(run_dir / "run.json")
    rows = [
        json.loads(line)
        for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = render_report(rows, meta)
    write_text(run_dir / "report.md", report)
    print(run_dir / "report.md")
    return 0


def cmd_save(args) -> int:
    project = resolve_project(getattr(args, "project", None))
    out = Path(args.out).resolve() if args.out else project / ".work" / "_bench"
    results_dir = (
        Path(args.results_dir).resolve() if args.results_dir else DEFAULT_RESULTS_DIR
    )
    run_dir = out / args.run_id
    for name in ("results.jsonl", "run.json", "report.md"):
        if not (run_dir / name).is_file():
            raise BenchError(f"no {name} in {run_dir} (run {args.run_id!r} first)")
    target = results_dir / args.run_id
    if target.exists():
        raise BenchError(f"target already exists: {target}")
    target.mkdir(parents=True)
    for name in ("results.jsonl", "run.json", "report.md"):
        shutil.copyfile(run_dir / name, target / name)
    print(f"saved {args.run_id} -> {target}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.py",
        description="Run implementer profiles on frozen cases and report cost per accepted task",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="list the cases in --cases-dir")
    list_p.add_argument("--cases-dir", default=None)

    run_p = sub.add_parser("run", help="run profiles on cases and write results")
    run_p.add_argument("--profiles", required=True)
    run_p.add_argument("--cases", default=None, help="id,id or all (default: all)")
    run_p.add_argument("--repeat", type=int, default=None)
    run_p.add_argument("--max-cost-usd", type=float, default=None)
    run_p.add_argument("--review", action="store_true", default=None)
    run_p.add_argument("--review-model", default=None)
    run_p.add_argument("--claude-command", default=None)
    run_p.add_argument("--run-id", default=None)
    run_p.add_argument("--extend", default=None)
    run_p.add_argument("--out", default=None)
    run_p.add_argument("--cases-dir", default=None)
    run_p.add_argument("--project", default=None)
    run_p.add_argument("--timeout-seconds", type=float, default=None)
    run_p.add_argument("--keep-code", action="store_true")

    report_p = sub.add_parser("report", help="rewrite report.md from results.jsonl")
    report_p.add_argument("run_id")
    report_p.add_argument("--out", default=None)
    report_p.add_argument("--project", default=None)

    save_p = sub.add_parser("save", help="copy a run into bench/results/<run-id>/")
    save_p.add_argument("run_id")
    save_p.add_argument("--out", default=None)
    save_p.add_argument("--results-dir", default=None)
    save_p.add_argument("--project", default=None)

    args = parser.parse_args(argv)
    if args.command == "run":
        if args.repeat is not None and args.repeat < 1:
            parser.error("--repeat must be >= 1")
        if args.max_cost_usd is not None and args.max_cost_usd < 0:
            parser.error("--max-cost-usd must be >= 0")
        if args.timeout_seconds is not None and args.timeout_seconds <= 0:
            parser.error("--timeout-seconds must be > 0")
        if args.claude_command is not None:
            try:
                parsed = json.loads(args.claude_command)
                if (
                    not isinstance(parsed, list)
                    or not parsed
                    or not all(isinstance(a, str) and a for a in parsed)
                ):
                    raise ValueError("must be a non-empty JSON list of strings")
                args.claude_command = parsed
            except ValueError as exc:
                parser.error(f"--claude-command: {exc}")
        else:
            args.claude_command = ["claude"]
    try:
        if args.command == "list":
            return cmd_list(args)
        if args.command == "run":
            return cmd_run(args)
        if args.command == "report":
            return cmd_report(args)
        return cmd_save(args)
    except BenchError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    except CaseError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    except models.ConfigError as exc:
        print(f"bench: invalid models.toml: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
