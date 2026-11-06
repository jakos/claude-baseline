# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Bounded validation of task-flow plan, requirements, backlog and review artifacts.

The planner role (Claude subagent or external profile) must return exactly the plan.md
contract of skills/workflow/SKILL.md, so the trusted runner — not the model — can check
the artifact before publishing it. This module is that check, kept separate from the
runner (scripts/role_run.py) so the contract is unit-testable without a model, a
process or a repository.

validate_plan(text, gate_commands=..., stack=...) returns a Validation(ok, blocked,
reasons):

- ok False means the artifact is invalid; reasons are bounded, human-readable strings
  naming the broken rule (the runner feeds them back in its single repair attempt).
- blocked True means the artifact is a legitimate `BLOCKED: yes` plan: the Risks /
  open questions section starts with `BLOCKED: yes`, so the plan stopped on genuine
  unresolved questions. A blocked plan still needs the exact section skeleton and a
  non-empty Goal, but it does not invent ordinary completion criteria, so Steps,
  Acceptance criteria and Verification commands may be empty. ok stays True: the
  artifact is valid, the task is blocked.

Rules enforced on plan.md (SKILL.md → plan.md):

- one `# Plan: <title>` first line, then exactly the nine `##` sections, in order, no
  duplicates and no extra ones; a fenced block wrapping the whole artifact, a missing
  or duplicate section, an oversized artifact or a truncation marker are invalid;
- `## Steps`: numbered `1.`, `2.`, ... lines, sequential, non-empty when not blocked;
- `## Profiles`: one `<name>: <paths>` line per profile, names from the known set
  (python, docs, config, generic), unique;
- `## Acceptance criteria`: column-zero `AC<n>:` lines only — a list bullet or a
  numbered item before an AC label (`- AC1:`, `1. AC1:`) is invalid — labels unique and
  sequential from AC1, each ending in a non-empty `Verify: <test | command | manual: …>`
  reference on the same line;
- `## Verification commands`: every non-blank, non-`#` line of the first fenced block
  must parse with gates.split_command (one program plus arguments, no shell);
- when `gate_commands` is given (the actual stack base's project gate, as argv lists),
  every inherited gate command must appear in the plan's verification list, so a plan
  cannot drop inherited checks — including the earlier stack tasks' test files. A
  blocked plan with an empty verification list is exempt (a BLOCKED: yes plan has no
  ordinary completion criteria to verify); a blocked plan that does list commands is
  still checked;
- when `stack` is given (the machine-built dependency manifest, {"satisfied": [...]}),
  the plan may not mark a satisfied earlier stack prerequisite as missing just because
  its source is unmerged on the current checkout.

Stdlib only (plus gates, imported from this directory), started by `uv run --script` or
imported as a sibling module by scripts/role_run.py, so it runs on macOS, Linux and
Windows without Git Bash.
"""

import re
import sys
from dataclasses import dataclass, field

import gates

# The nine `##` sections of plan.md, in the exact order SKILL.md requires.
REQUIRED_SECTIONS = [
    "Goal",
    "Context",
    "Profiles",
    "Steps",
    "Acceptance criteria",
    "Verification commands",
    "Project rules",
    "Out of scope",
    "Risks / open questions",
]
KNOWN_PROFILES = {"python", "docs", "config", "generic"}
TITLE = re.compile(r"^# Plan: \S.*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
STEP = re.compile(r"^(\d+)\.\s+\S")
PROFILE_LINE = re.compile(r"^`?([A-Za-z][\w-]*)`?\s*:\s*\S")
AC_LINE = re.compile(r"^AC(\d+):\s*(\S.*)$")
# A list bullet or a numbered item in front of an AC label is not a column-zero label.
BULLET_AC = re.compile(r"^\s*(?:[-*]|\d+\.)\s*AC\d+\s*:")
VERIFY = "Verify:"
MAX_ARTIFACT_CHARS = 200_000
TRUNCATION_MARKERS = (
    re.compile(r"\[\d+ characters truncated\]"),
    re.compile(r"\.\.\.\[\d+ more (?:lines|characters)\]"),
    re.compile(r"\[\d+ more lines\]"),
)
BLOCKED_LINE = "BLOCKED: yes"


@dataclass
class Validation:
    """The verdict on one candidate artifact: ok, blocked and the bounded reasons."""

    ok: bool
    blocked: bool = False
    reasons: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def _sections(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Split into {section name: body lines}; "" is the text before the first `##`.

    Heading lines themselves are not part of a body, and headings inside fenced code
    blocks do not count, so a fenced example cannot fake a section. Returns (sections,
    order) with every `##` heading in file order, duplicates included.
    """
    sections: dict[str, list[str]] = {"": []}
    order: list[str] = []
    fence: tuple[str, int] | None = None
    current = ""
    for line in text.splitlines():
        match = FENCE.match(line)
        if fence is not None:
            if (
                match
                and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= fence[1]
            ):
                fence = None
            sections[current].append(line)
            continue
        if match:
            fence = (match.group(1)[0], len(match.group(1)))
            sections[current].append(line)
            continue
        heading = HEADING.match(line)
        if heading and heading.group(1) == "##":
            current = heading.group(2)
            order.append(current)
            sections.setdefault(current, [])
            continue
        sections[current].append(line)
    return sections, order


def unfenced(lines: list[str]) -> list[str]:
    """Return declaration lines outside fenced examples."""
    result = []
    fence: tuple[str, int] | None = None
    for line in lines:
        match = FENCE.match(line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= fence[1]:
                fence = None
        elif match:
            fence = (match[1][0], len(match[1]))
        else:
            result.append(line)
    return result


def _command_lines(body: list[str]) -> list[str]:
    """The non-blank, non-`#` lines of the first fenced block of a section body."""
    commands: list[str] = []
    fence: tuple[str, int] | None = None
    for line in body:
        match = FENCE.match(line)
        if fence is not None:
            if (
                match
                and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= fence[1]
            ):
                break
            if line.strip() and not line.lstrip().startswith("#"):
                commands.append(line.strip())
            continue
        if match:
            fence = (match.group(1)[0], len(match.group(1)))
    return commands


def parse_verification_commands(
    text: str, *, body: list[str] | None = None
) -> list[str]:
    """The plan's first `## Verification commands` fenced block lines (gates format)."""
    sections, _order = (
        _sections(text) if body is None else ({"Verification commands": body}, [])
    )
    return _command_lines(sections.get("Verification commands", []))


def _check_sections(
    text: str, reasons: list[str]
) -> tuple[dict[str, list[str]], list[str]]:
    lines = text.splitlines()
    if not lines or not TITLE.match(lines[0]):
        reasons.append("the artifact must start with a `# Plan: <title>` first line")
    if text.lstrip().startswith("```") or text.lstrip().startswith("~~~"):
        reasons.append(
            "the artifact is wrapped in a code fence; return the raw Markdown"
        )
    sections, order = _sections(text)
    if order != REQUIRED_SECTIONS:
        if set(order) == set(REQUIRED_SECTIONS) and len(order) == len(
            REQUIRED_SECTIONS
        ):
            reasons.append(
                "the `##` sections must appear in the exact order "
                + " -> ".join(REQUIRED_SECTIONS)
                + f"; got {' -> '.join(order)}"
            )
        else:
            missing = [s for s in REQUIRED_SECTIONS if s not in order]
            extra = [s for s in order if s not in REQUIRED_SECTIONS]
            duplicate = [s for s in set(order) if order.count(s) > 1]
            if missing:
                reasons.append(f"missing section(s): {missing}")
            if extra:
                reasons.append(f"unknown section(s): {extra}")
            if duplicate:
                reasons.append(f"duplicate section(s): {duplicate}")
    # exactly one `#` title, and no heading deeper than `##` outside fences
    titles = 0
    fence: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        match = FENCE.match(line)
        if fence is not None:
            if (
                match
                and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= fence[1]
            ):
                fence = None
            continue
        if match:
            fence = (match.group(1)[0], len(match.group(1)))
            continue
        heading = HEADING.match(line)
        if not heading:
            continue
        if heading.group(1) == "#":
            titles += 1
            if index != 0:
                reasons.append("a second `#` title line is not allowed")
        elif len(heading.group(1)) >= 3:
            reasons.append(
                f"a {heading.group(1)} heading is not allowed: {line[:60]!r}"
            )
    if fence is not None:
        reasons.append("the plan contains an unclosed code fence")
    if titles == 0:
        reasons.append("the artifact has no `# Plan:` title")
    return sections, order


def _check_steps(body: list[str], blocked: bool, reasons: list[str]) -> None:
    body = unfenced(body)
    steps = [line for line in body if line.strip()]
    if not steps:
        if not blocked:
            reasons.append("## Steps is empty; a plan needs numbered steps")
        return
    numbers = []
    for line in steps:
        match = STEP.match(line)
        if not match:
            reasons.append(f"## Steps: not a numbered step line: {line[:60]!r}")
            continue
        numbers.append(int(match.group(1)))
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        reasons.append(f"## Steps: step numbers must be 1..n in order, got {numbers}")


def _check_profiles(body: list[str], reasons: list[str]) -> None:
    body = unfenced(body)
    names = []
    for line in body:
        if not line.strip():
            continue
        match = PROFILE_LINE.match(line)
        if not match:
            reasons.append(
                f"## Profiles: not a `<profile>: <paths>` line: {line[:60]!r}"
            )
            continue
        name = match.group(1)
        if name not in KNOWN_PROFILES:
            reasons.append(
                f"## Profiles: unknown profile {name!r} (known: {sorted(KNOWN_PROFILES)})"
            )
        elif name in names:
            reasons.append(f"## Profiles: duplicate profile {name!r}")
        else:
            names.append(name)


def _check_criteria(body: list[str], blocked: bool, reasons: list[str]) -> None:
    body = unfenced(body)
    labels: list[int] = []
    for line in body:
        if not line.strip():
            continue
        if BULLET_AC.match(line):
            reasons.append(
                "## Acceptance criteria: an AC label must start at column zero, not as "
                f"a list item or numbered line: {line.strip()[:60]!r}"
            )
            continue
        match = AC_LINE.match(line)
        if not match:
            if line.startswith((" ", "\t")):
                continue  # continuation of the previous criterion
            reasons.append(
                "## Acceptance criteria: expected a column-zero `AC<n>: ...` line, got "
                f"{line[:60]!r}"
            )
            continue
        number = int(match.group(1))
        labels.append(number)
        text = match.group(2)
        verify_at = text.find(VERIFY)
        if verify_at == -1:
            reasons.append(f"AC{number}: missing a `Verify:` reference")
        elif not text[verify_at + len(VERIFY) :].strip():
            reasons.append(f"AC{number}: the `Verify:` reference is empty")
    if not labels:
        if not blocked:
            reasons.append(
                "## Acceptance criteria is empty; a plan needs AC1, AC2, ... "
                "(a BLOCKED: yes plan may leave it empty)"
            )
        return
    if labels != list(range(1, len(labels) + 1)):
        reasons.append(
            f"## Acceptance criteria: labels must be unique and sequential from AC1, "
            f"got AC{labels[0]}..{labels[-1]} ({len(labels)} labels: {labels})"
        )


def _check_commands(body: list[str], blocked: bool, reasons: list[str]) -> list[str]:
    commands = _command_lines(body)
    if not commands:
        if not blocked:
            reasons.append("## Verification commands has no fenced command block")
        return []
    for line in commands:
        try:
            gates.split_command(line)
        except ValueError as exc:
            reasons.append(f"verification command {line!r} is refused: {exc}")
    return commands


def _check_gate(
    commands: list[str], gate_commands: list[list[str]] | None, reasons: list[str]
) -> None:
    if not gate_commands:
        return
    plan_argvs = []
    for line in commands:
        try:
            plan_argvs.append(tuple(gates.split_command(line)))
        except ValueError:
            continue
    missing = [argv for argv in gate_commands if tuple(argv) not in set(plan_argvs)]
    if missing:
        shown = "; ".join(" ".join(argv) for argv in missing[:5])
        reasons.append(
            "the verification list omits inherited checks of the actual stack base's "
            f"project gate ({len(missing)} missing, first: {shown}); retain every "
            "inherited check, including earlier stack tasks' test files"
        )


def _check_stack(text: str, stack: dict | None, reasons: list[str]) -> None:
    if not stack:
        return
    satisfied = stack.get("satisfied", [])
    for prerequisite in satisfied:
        pattern = re.compile(
            rf"\b{re.escape(prerequisite)}\b[^.\n]{{0,80}}\bmissing\b"
            rf"|\bmissing\b[^.\n]{{0,80}}\b{re.escape(prerequisite)}\b",
            re.IGNORECASE,
        )
        if pattern.search(text):
            reasons.append(
                f"the plan marks the declared earlier stack prerequisite "
                f"{prerequisite!r} as missing; the dependency manifest marks it "
                "satisfied (unmerged on this checkout is not missing) — do not dismiss "
                "it, and do not dismiss genuine unresolved design questions either"
            )


def validate_plan(
    text: str,
    *,
    gate_commands: list[list[str]] | None = None,
    stack: dict | None = None,
) -> Validation:
    """Validate one candidate plan.md artifact; see the module docstring for the rules."""
    reasons: list[str] = []
    if not isinstance(text, str) or not text.strip():
        return Validation(False, reasons=["the artifact is empty"])
    if len(text) > MAX_ARTIFACT_CHARS:
        return Validation(
            False,
            reasons=[
                (
                    f"the artifact is {len(text)} characters, over the "
                    f"{MAX_ARTIFACT_CHARS} limit"
                )
            ],
        )
    for marker in TRUNCATION_MARKERS:
        if marker.search(text):
            reasons.append("the artifact contains a truncation marker; return it whole")
            break
    sections, _order = _check_sections(text, reasons)
    blocked = False
    risks = sections.get("Risks / open questions", [])
    risk_lines = [line for line in risks if line.strip()]
    if risk_lines and risk_lines[0].strip() == BLOCKED_LINE:
        blocked = True
    if (
        not blocked
        and risk_lines
        and risk_lines[0].strip().upper().startswith("BLOCKED:")
    ):
        reasons.append(
            "a BLOCKED line under Risks / open questions must be exactly `BLOCKED: yes` "
            f"on the first line, got {risk_lines[0].strip()[:60]!r}"
        )
    goal = [line for line in sections.get("Goal", []) if line.strip()]
    if not goal:
        reasons.append("## Goal is empty")
    _check_profiles(sections.get("Profiles", []), reasons)
    _check_steps(sections.get("Steps", []), blocked, reasons)
    _check_criteria(sections.get("Acceptance criteria", []), blocked, reasons)
    commands = _check_commands(
        sections.get("Verification commands", []), blocked, reasons
    )
    if not blocked and not commands:
        reasons.append("a non-blocked plan needs verification commands")
    # A legitimate BLOCKED: yes plan may leave the verification list empty — it does
    # not invent ordinary completion criteria — so the inherited gate is not enforced
    # against an empty list; a blocked plan that does list commands is still checked.
    if not (blocked and not commands):
        _check_gate(commands, gate_commands, reasons)
    _check_stack(text, stack, reasons)
    return Validation(not reasons, blocked=blocked, reasons=reasons, commands=commands)


def _artifact_errors(text: str) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return ["the artifact is empty"]
    if len(text) > MAX_ARTIFACT_CHARS:
        return ["the artifact exceeds the size limit"]
    if any(marker.search(text) for marker in TRUNCATION_MARKERS):
        return ["the artifact contains a truncation marker"]
    if text.lstrip().startswith(("```", "~~~")):
        return ["return raw Markdown, without a wrapping fence"]
    return []


def validate_requirements(text: str, *, original: str = "") -> Validation:
    """Preserve supplied requirements verbatim; cite every repository addition."""
    reasons = _artifact_errors(text)
    if original and original not in text:
        reasons.append("existing requirements content must be preserved verbatim")
    if not any(line.strip() and not line.startswith("#") for line in text.splitlines()):
        reasons.append("requirements need non-heading content")
    additions = text.replace(original, "", 1) if original else text
    for line in additions.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if not re.match(r"^\s*[-*] \S", line) or not re.search(
            r"[\w./\\-]+:\d+\b", line
        ):
            reasons.append(
                "each repository-derived requirement must be a bullet citing file:line"
            )
    return Validation(not reasons, reasons=reasons[:10])


def backlog_envelope(text: str) -> dict[str, str]:
    """Exact JSON object, with no duplicate, missing or additional members."""
    import json

    if not isinstance(text, str) or len(text) > 2 * MAX_ARTIFACT_CHARS + 10_000:
        raise ValueError("backlog JSON envelope exceeds the size limit")

    def unique(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate envelope member: {key}")
            result[key] = value
        return result

    try:
        data = json.loads(text, object_pairs_hook=unique)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid backlog JSON envelope: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"requirements.md", "backlog.md"}:
        raise ValueError("envelope must contain only requirements.md and backlog.md")
    if not all(isinstance(value, str) for value in data.values()):
        raise ValueError("envelope values must be Markdown strings")
    return data


def validate_backlog(text: str, *, run_id: str, thin: bool) -> Validation:
    reasons = _artifact_errors(text)
    sections, order = _sections(text)
    if not text.startswith(f"# Backlog: {run_id}\n"):
        reasons.append("backlog title must match the requested run ID")
    if [line for line in unfenced(text.splitlines()) if line.startswith("# ")] != [
        f"# Backlog: {run_id}"
    ]:
        reasons.append("backlog must have exactly one title")
    if order != ["Tasks", "Risks / open questions"]:
        reasons.append(
            "backlog needs exactly Tasks and Risks / open questions in order"
        )
    risks = "\n".join(sections.get("Risks / open questions", [])).strip()
    blocked = risks.startswith("BLOCKED: yes\n") and bool(
        risks.split("\n", 1)[1].strip()
    )
    if risks.startswith("BLOCKED:") and not blocked:
        reasons.append("blocked backlog needs BLOCKED: yes followed by a question")
    tasks = "\n".join(sections.get("Tasks", []))
    chunks = re.split(r"^### (\d+)\. ([^\n]+)\n?", tasks, flags=re.MULTILINE)
    if chunks[0].strip():
        reasons.append("unexpected content before the first task")
    count = (len(chunks) - 1) // 3
    if count > 10 or (not count and not blocked):
        reasons.append("backlog must have 1..10 tasks (zero only when blocked)")
    seen = []
    for index in range(count):
        number, task_id, body = chunks[1 + index * 3 : 4 + index * 3]
        if int(number) != index + 1:
            reasons.append("task numbering must be sequential from 1")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", task_id) or task_id in seen:
            reasons.append("task IDs must be unique safe kebab slugs")
        headings = re.findall(r"^(#{1,6}) (.*)$", body, re.MULTILINE)
        if headings != [("####", name) for name in ("Goal", "Context", "Constraints")]:
            reasons.append(
                f"{task_id}: expected Goal, Context and Constraints headings"
            )
        parts = re.split(
            r"^#### (?:Goal|Context|Constraints)\s*$", body, flags=re.MULTILINE
        )
        if len(parts) != 4 or any(not part.strip() for part in parts[1:]):
            reasons.append(f"{task_id}: task sections must be non-empty")
        dep = re.fullmatch(r"\s*- depends-on: (.+)\s*", parts[0])
        dependencies = (
            []
            if dep and dep[1].strip() == "-"
            else (dep[1].strip().split(", ") if dep else ["invalid"])
        )
        if len(set(dependencies)) != len(dependencies) or any(
            d not in seen for d in dependencies
        ):
            reasons.append(f"{task_id}: dependencies must name unique earlier tasks")
        if thin:
            if index == 0 and (task_id != "bootstrap-project" or dependencies):
                reasons.append(
                    "thin repository must start with independent bootstrap-project"
                )
            if index > 0 and "bootstrap-project" not in dependencies:
                reasons.append(
                    "every later thin-repository task must depend on bootstrap-project"
                )
            if index == 0 and (
                len(parts) != 4
                or "requirements.md" not in parts[2]
                or "dev-baseline:project-bootstrap" not in parts[2]
            ):
                reasons.append(
                    "bootstrap Context must name requirements.md and the optional bootstrap skill"
                )
        elif task_id == "bootstrap-project":
            reasons.append("non-thin repository must not include bootstrap-project")
        seen.append(task_id)
    return Validation(not reasons, blocked=blocked, reasons=reasons[:10])


@dataclass
class CheckEvidence:
    results: dict[tuple[str, ...], str]
    failures: dict[tuple[str, ...], set[str]]


def parse_checks(text: str, *, round_number: int, commands: list[str]) -> CheckEvidence:
    """Parse bounded checker evidence; missing/ambiguous evidence is a refusal."""
    if _artifact_errors(text) or not text.startswith(
        f"# Checks: round {round_number}\n"
    ):
        raise ValueError("invalid checks artifact or mismatched checks round")
    sections, order = _sections(text)
    if order != ["Failures", "Not run"]:
        raise ValueError("checks require Failures and Not run sections")
    results = {}
    numbered = {}
    for line in unfenced(sections[""]):
        match = re.fullmatch(
            r"\| (\d+) \| `+(.+?)`+ \| (PASS|FAIL|ERROR) \| ([^|]*) \| ([^|]*) \|", line
        )
        if not match:
            if re.match(r"\|\s*\d+\s*\|", line):
                raise ValueError("malformed checks row")
            continue
        identity = tuple(
            gates.split_command(match[2].strip().replace(chr(92) + "|", "|"))
        )
        exit_value = match[4].strip()
        if (match[3] == "PASS" and exit_value != "0") or (
            match[3] != "PASS" and exit_value == "0"
        ):
            raise ValueError("checks result contradicts exit code")
        if not re.fullmatch(r"-?\d+|-", exit_value):
            raise ValueError("malformed checks exit code")
        if identity in results or int(match[1]) in numbered:
            raise ValueError("duplicate checks command or number")
        results[identity] = match[3]
        numbered[int(match[1])] = identity
    expected = [tuple(gates.split_command(command)) for command in commands]
    if list(results) != expected or list(numbered) != list(range(1, len(expected) + 1)):
        raise ValueError("checks do not cover the plan commands exactly")
    failures = {}
    body = "\n".join(sections["Failures"])
    parts = re.split(r"^### (\d+)\. `+(.+?)`+\s*$", body, flags=re.MULTILINE)
    if parts[0].strip().lower() not in ("", "none"):
        raise ValueError("unrecognized failure evidence")
    for index in range((len(parts) - 1) // 3):
        number, command, detail = parts[1 + index * 3 : 4 + index * 3]
        identity = tuple(gates.split_command(command))
        evidence = {
            line.strip()
            for line in detail.splitlines()
            if line.strip() and not re.fullmatch(r"`{3,}|~{3,}", line.strip())
        }
        if (
            numbered.get(int(number)) != identity
            or identity in failures
            or results.get(identity) not in ("FAIL", "ERROR")
            or not evidence
            or len(evidence) > 35
            or not any(
                re.search(
                    r"test_\w+|\w+(?:Error|Exception)\b|\S+:\d+(?::\d+)?:\s*\S|(?:error|fatal|refused|cannot|not found|timed out)[: ]+\S.*",
                    line,
                    re.IGNORECASE,
                )
                for line in evidence
            )
            or any("truncated" in line.lower() for line in evidence)
        ):
            raise ValueError("malformed or insufficient failure evidence")
        failures[identity] = evidence
    if set(failures) != {
        key for key, value in results.items() if value in ("FAIL", "ERROR")
    }:
        raise ValueError("every failing command needs bounded failure evidence")
    return CheckEvidence(results, failures)


def classify_checks(
    current: CheckEvidence, baseline: CheckEvidence | None
) -> tuple[set, set]:
    preexisting = set()
    introduced = set()
    for command, evidence in current.failures.items():
        if (
            baseline
            and baseline.results.get(command) in ("FAIL", "ERROR")
            and evidence <= baseline.failures.get(command, set())
        ):
            preexisting.add(command)
        else:
            introduced.add(command)
    return preexisting, introduced


def validate_review(
    text: str,
    *,
    round_number: int,
    plan: str,
    current: CheckEvidence,
    baseline: CheckEvidence | None = None,
) -> Validation:
    reasons = _artifact_errors(text)
    sections, order = _sections(text)
    headings = [line for line in unfenced(text.splitlines()) if HEADING.match(line)]
    if (
        any(line.startswith("###") for line in headings)
        or sum(line.startswith("# ") for line in headings) != 1
    ):
        reasons.append(
            "review headings must follow the exact title and section hierarchy"
        )
    verdicts = [name for name in order if name.startswith("Verdict:")]
    verdict = verdicts[0].partition(": ")[2] if len(verdicts) == 1 else ""
    expected = [
        "Acceptance criteria",
        "Pre-existing failures",
        "Findings",
        "Scope",
        f"Verdict: {verdict}",
        "Fix list",
    ]
    if order != expected or verdict not in ("APPROVED", "CHANGES_REQUESTED"):
        reasons.append(
            "review must have the exact workflow sections and one valid verdict"
        )
    if not text.startswith(f"# Review: round {round_number}\n"):
        reasons.append("review title must match the requested round")
    plan_sections, _ = _sections(plan)
    labels = [
        f"AC{m[1]}"
        for line in unfenced(plan_sections.get("Acceptance criteria", []))
        if (m := AC_LINE.match(line))
    ]
    rows = []
    failed = []
    for line in sections.get("Acceptance criteria", []):
        if not line.strip() or line.strip() in (
            "| AC | Result | Evidence |",
            "| --- | --- | --- |",
        ):
            continue
        match = re.fullmatch(r"\| (AC\d+) \| (PASS|FAIL) \| (\S.*?) \|", line)
        if not match:
            reasons.append(
                "each AC needs exactly one PASS/FAIL row with non-empty evidence"
            )
            continue
        rows.append(match[1])
        if not re.search(
            r"(?::\d+|test_\w+|checks-\d+|exit\s+(?:code\s*)?\d+)", match[3]
        ):
            reasons.append(
                f"{match[1]}: evidence needs file:line, test name or command output"
            )
        if match[2] == "FAIL":
            failed.append(match[1])
    if sorted(rows) != sorted(labels) or len(rows) != len(set(rows)):
        reasons.append(
            "review must cover every plan AC exactly once with no unknown ACs"
        )
    findings = [line for line in sections.get("Findings", []) if line.strip()]
    blocking = False
    if not findings:
        reasons.append("Findings must contain numbered entries or none")
    if findings != ["none"]:
        for number, line in enumerate(findings, 1):
            match = re.fullmatch(r"(\d+)\. \[(blocking|suggestion)\] (\S.*)", line)
            if not match or int(match[1]) != number:
                reasons.append(
                    "findings must be sequential numbered blocking/suggestion entries, or none"
                )
            elif match[2] == "blocking":
                blocking = True
                if not re.search(r":\d+", match[3]):
                    reasons.append(
                        "blocking findings must include file:line evidence and a breaking state"
                    )
    scope = "\n".join(sections.get("Scope", [])).strip()
    if not scope:
        reasons.append("Scope is required")
    fixes = [line for line in sections.get("Fix list", []) if line.strip()]
    if fixes != ["none"]:
        for number, line in enumerate(fixes, 1):
            if not re.match(rf"^{number}\. \S", line):
                reasons.append(
                    "Fix list must be sequential non-empty instructions, or none"
                )
    preexisting, introduced = classify_checks(current, baseline)
    prior = "\n".join(sections.get("Pre-existing failures", [])).strip()
    if preexisting:
        for command in preexisting:
            if " ".join(command) not in prior or not any(
                item in prior for item in current.failures[command]
            ):
                reasons.append(
                    "Pre-existing failures must name each command and matching baseline evidence"
                )
        if "checks-0" not in prior:
            reasons.append("Pre-existing failures must cite checks-0")
    elif prior != "none":
        reasons.append(
            "Pre-existing failures must be none when no failure matches baseline"
        )
    must_fix = bool(introduced or failed or blocking or scope.lower() != "none")
    if verdict == "APPROVED" and must_fix:
        reasons.append(
            "approval contradicts introduced failures, failed ACs, blocking findings or scope"
        )
    if verdict == "CHANGES_REQUESTED" and (not fixes or fixes == ["none"]):
        reasons.append("CHANGES_REQUESTED needs a concrete Fix list")
    if verdict == "APPROVED" and fixes != ["none"]:
        reasons.append("APPROVED must have Fix list none")
    return Validation(not reasons, reasons=reasons[:10])


def main(argv: list[str] | None = None) -> int:
    """Validate one plan.md file: `role_contracts.py <plan.md> [--gate <task-flow.md>]`.

    Exit 0 when the artifact is valid (blocked or not), 1 when invalid, 2 on usage
    errors. The verdict and the bounded reasons go to stdout; nothing is written.
    """
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Validate a task-flow plan.md artifact against the workflow contract"
    )
    parser.add_argument("plan", help="the plan.md file to validate")
    parser.add_argument(
        "--gate",
        help="a .claude/task-flow.md whose Verification commands are the inherited gate",
    )
    args = parser.parse_args(argv)
    try:
        text = Path(args.plan).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"role_contracts.py: cannot read {args.plan}: {exc}", file=sys.stderr)
        return 2
    gate_commands = None
    if args.gate:
        try:
            gate_text = Path(args.gate).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"role_contracts.py: cannot read {args.gate}: {exc}", file=sys.stderr)
            return 2
        try:
            gate_commands = _gate_argvs(gate_text)
        except ValueError as exc:
            print(
                f"role_contracts.py: {args.gate}: refused command line: {exc}",
                file=sys.stderr,
            )
            return 2
    verdict = validate_plan(text, gate_commands=gate_commands)
    print(
        json.dumps(
            {
                "ok": verdict.ok,
                "blocked": verdict.blocked,
                "reasons": verdict.reasons,
                "commands": len(verdict.commands),
            },
            ensure_ascii=False,
        )
    )
    return 0 if verdict.ok else 1


def _gate_argvs(gate_md_text: str) -> list[list[str]]:
    """The Verification commands block of a .claude/task-flow.md as argv lists."""
    sections, _order = _sections(gate_md_text)
    return [
        gates.split_command(line)
        for line in _command_lines(sections.get("Verification commands", []))
    ]


if __name__ == "__main__":
    sys.exit(main())
