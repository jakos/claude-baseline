# Task record: bench-cli-profiles

- schema: task-record/1
- task: bench-cli-profiles
- approval: user
- rounds: 1
- final-verdict: APPROVED
- risk: unavailable (no merged risk schema)

## Goal

Let the benchmark run agent command-line tools (Codex first, then Gemini CLI, Copilot CLI, OpenCode and `claude -p`) next to API profiles on the same frozen cases, so subscription and API options can be compared.

## Acceptance criteria

- AC1: Execution confirms both dependencies are merged into the actual base or precede this task in its stack; otherwise implementation stops before source edits. — PASS
- AC2: Every requested tool has a dated, linked official-source matrix covering headless invocation, output format, isolation, authentication, billing and subscription terms; unresolved or prohibited routes cannot appear as ready-to-run subscription recommendations. — PASS
- AC3: Fake CLI tests demonstrate isolated per-cell configuration, no automatic inheritance of personal configuration or unrelated secrets, sanitized provenance capture and unchanged ordinary implementer defaults. — PASS
- AC4: Changed CLI version, argv, model, usage format or isolation settings reject incompatible resume/extension without mutation; adding a distinct compatible profile remains supported. Legacy missing provenance is explicit. — PASS
- AC5: A fake CLI that commits its implementation still has all changes measured against the original base. Tests separately cover staged, unstaged and untracked edits, test modifications/deletions/additions, and no hidden-probe leakage. — PASS
- AC6: Missing usage remains null; Codex usage is counted once; configured limit exhaustion is unavailable and does not trigger infrastructure retries. Resume preserves these completed outcomes and totals include attempt wall time. — PASS
- AC7: Report and publication fixtures show separate API and subscription cost information, tool versions, quality metrics, coverage, rounds and time. Unknown cost is never rendered as zero, incompatible measurements are not ranked together, and repeated rendering is byte-identical. — PASS
- AC8: All five preset examples conform to the implemented schema and documented transport restrictions, remain disabled/commented, contain no credentials, and distinguish documentation checks from actual authenticated testing. — PASS
- AC9: Both guides and the flow diagram describe CLI autonomy, hidden-test/network limitations, `--keep-code`, disk retention and cleanup. The manual checklist schedules Codex before 2026-11-25 and states costs, quotas, limits and confirmation requirements. — PASS
- AC10: No authenticated benchmark, real login or model call occurs during implementation or verification; frozen cases and saved historical results remain unchanged. No CLI results are presented as measured without a corresponding saved run. — PASS
- AC11: Every inherited verification command runs with no introduced failure or error; any remaining baseline failure is identified using the workflow’s matching-evidence rule. — PASS

## Rounds

- round 1: APPROVED — implementer codex/gpt-6.1-sol

## Out of scope

Open reviewer suggestions: `inspect_state` uses `git add -A`, so a tool-created file matching an ignore pattern is not reported; a corrupted `.git` aborts the whole run with `BenchError` instead of recording an error cell; a CLI profile without isolation is now refused as unavailable (a behaviour change the docs should state more plainly); one paragraph in `docs/task-flow/benchmark.md` should be split. The matrix's source links were not opened (no network).
