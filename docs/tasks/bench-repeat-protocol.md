# Task record: bench-repeat-protocol

- schema: task-record/1
- task: bench-repeat-protocol
- approval: user
- rounds: 2
- final-verdict: APPROVED
- risk: unavailable (no merged risk schema)

## Goal

Define a repeatable benchmark protocol and implement missing-cell resume, bounded infrastructure retries, isolated hidden-test probes after every green round, and reporting of initial quality and total work, while preserving historical results.

## Acceptance criteria

- AC1: The protocol states why uncontrolled cache observations do not prove repeat-only comparability; fixes execution concurrency and settings; requires five completed independent repeats per case/profile for descriptive rankings; specifies deterministic median/IQR/min–max; and separates gates-only from reviewed comparisons. — PASS
- AC2: The protocol defines a provisional regression alarm requiring ten independent repeats per compared case/profile, a second independent ten-repeat confirmation batch, and either a hidden-pass/acceptance decrease of at least 20 percentage points in both batches or a median cost/time increase of at least 20% that also exceeds the larger compared IQR in both batches. It labels these operat… [truncated] — PASS
- AC3: Any proposed cold-versus-warm experiment remains manual and unpaid in this task. Its description fixes model/settings/concurrency, alternates or counterbalances conditions, records first-turn versus conversation cache usage, acknowledges provider cache uncertainty, and states request counts plus an explicit price-based cost formula and spending cap. Cached prices remain labelled unver… [truncated] — PASS
- AC4: Resume executes exactly the missing cell identities, retains all existing rows including unsuccessful outcomes, preserves spend accounting, avoids interrupted scratch collisions, and makes zero model calls for a completed run. Conflicting settings, malformed/duplicate rows, and fingerprint mismatches refuse without changing existing files. — PASS
- AC5: Infrastructure retries stop after the configured bound, restore pre-attempt state, record every attempt, and account for known usage and elapsed time without exceeding the available budget. Tests cover retry success, exhaustion, rate limits, dropped connections, and timeout classification. — PASS
- AC6: Tests demonstrate that `max_turns`, `no_progress`, round cap, hidden failure, blocked outcomes, cost caps, authentication/configuration failures, and unknown failures do not trigger infrastructure retries; request-level and harness-level retries have independently bounded counts. — PASS
- AC7: Every green round is probed, including reviewer-rejected rounds. A fake reviewer forces multiple rounds and proves hidden results do not change acceptance or feedback. A passing first probe followed by a non-green final state retains earliest hidden-pass round while final `hidden` is `not_run`. — PASS
- AC8: Leakage tests inspect subsequent implementer requests, reviewer prompts, task files, code files, index, and git objects: no probe commands, hidden output, hidden test code, or newly captured test names are exposed. Probe mutation and timeout tests leave implementation state unchanged and clean temporary copies. — PASS
- AC9: Reports and publication expose initial quality, accepted round, earliest observed hidden pass, final hidden result, total work, spread, coverage, and ranking eligibility. Fixtures cover one/four/five repeats, missing cells, unknown costs, legacy rows, retries, distinct review modes, and incompatible settings. Identical inputs produce byte-identical output. — PASS
- AC10: Harness version 2 changes the fingerprint; extension, resume, merge, and publication cannot silently mix version-1 and version-2 measurements. Existing saved run files remain unchanged, and the regenerated legacy published page states that the new probe requires a new baseline without claiming new measurements. — PASS
- AC11: Both guides document implemented flags, preserved outcome data, review costs and limits, recovery behavior, and hidden isolation. The routing documentation test accepts the established repeat minimum while preserving its other assertions. Changes contain no paid runs, new dependencies, plugin/marketplace version edits, changelog entries, attribution lines, or unrelated formatting; no … [truncated] — PASS
- AC12: Every inherited verification command runs; introduced failures are resolved, with any matching baseline failures reported under the workflow’s pre-existing-failure rule. — PASS

## Rounds

- round 1: CHANGES_REQUESTED — implementer codex/gpt-6.1-sol
- round 2: APPROVED — implementer codex/gpt-6.1-sol

## Out of scope

Cosmetic suggestions left open: a literal "min?max" in `bench/README.md` line 343; `spread` formats large token totals with `:g`; the leakage test does not check later implementer request bodies or git objects; under `--max-cost-usd` any failed attempt on an api/local profile marks billing unknown, so no retries are taken when a cap is set (conservative, documented). No paid benchmark run was made.
