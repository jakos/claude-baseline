# Task record: external-review-backlog

- schema: task-record/1
- task: external-review-backlog
- approval: user
- rounds: 1
- final-verdict: APPROVED
- risk: unavailable (no merged risk schema)

## Goal

Let `[roles]` send the backlog and reviewer stages to a configured API or CLI profile, using the runner and validators built for the planner, with a reviewer independence check. Claude stays the default for both roles.

## Acceptance criteria

- AC1: Missing role configuration and existing Claude role values preserve backlog/reviewer defaults and legacy budget behavior; invalid independence values are rejected and project configuration overrides user configuration. — PASS
- AC2: Backlog validation rejects duplicate/unsafe IDs, forward or missing dependencies, malformed sections, more than ten tasks, and invalid bootstrap dependencies; valid blocked output is recognized without task creation. — PASS
- AC3: API and fake CLI backlog runs publish validated output; malformed envelopes, symlink escapes and an injected second-replacement failure leave both pre-existing outputs byte-identical, or both absent when originally absent. — PASS
- AC4: Autopilot and goal-only epic procedures select configured backlog execution, preserve their approval gates, and leave Claude selection unchanged without opt-in. — PASS
- AC5: Reviewer preflight refuses round zero, mismatched rounds, missing checks, unresolved base and incomplete required inputs before model contact or artifact replacement. — PASS
- AC6: Reviewer prompt fixtures contain the diff, touched files and profile checklists, contain no excluded narrative markers, and API reads/searches/commands cannot recover excluded artifacts through alternate paths or symlinks. — PASS
- AC7: Reviews with missing, duplicate or unknown AC rows, empty evidence, wrong rounds or malformed sections are rejected; introduced failures, failed criteria and blocking findings cannot be approved; matching unrelated baseline failures can be approved. — PASS
- AC8: Independence compares actual current-round metadata and every fallback candidate; warn mode reports matches/unknowns, strict modes refuse prohibited matches and unknown identities before contacting that candidate. Profile aliases alone cannot establish independence. — PASS
- AC9: API and fake CLI reviewer executions preserve existing review bytes on invalid output or transport failure, permit exactly one artifact repair, publish valid reviews, and do not mutate task status. — PASS
- AC10: External backlog and reviewer roles retain read-only command and path boundaries; doctor reports their configured execution methods without contacting models. — PASS
- AC11: Backlog spend appears at run level, reviewer spend contributes to its task, repeated resume does not double count, and null usage/cost remains unknown while known subtotals survive. — PASS
- AC12: Budget scope defaults to Claude tokens; all scope adds external reported tokens once, never adds USD to tokens, handles unknown usage explicitly, persists on resume, and affects only between-task decisions. — PASS
- AC13: Changed docs and diagrams describe implemented flags and configuration, unchanged defaults, publication guarantees and CLI isolation limits; the evaluation protocol includes planted defects and a clean control without claiming unmeasured reviewer quality. — PASS
- AC14: All inherited verification commands run with no introduced FAIL or ERROR, and changes remain within the named files without version or changelog edits. — PASS

## Rounds

- round 1: APPROVED — implementer codex/gpt-6.1-sol

## Out of scope

Reviewer suggestions left open: a backlog with `BLOCKED: yes` and tasks still validates; strict reviewer independence refuses whenever the implementer is Claude or has no impl record, which makes `different-model` hard to use with the default setup; the shared repair cap and unknown-usage handling also change planner behavior; `run_cli` in `role_run.py` has no caller; the `review-*.md` exclusion regex would also refuse a legitimately touched file such as `docs/review-guide.md`.
