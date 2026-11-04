# Task record: implementer-model-routing

- schema: task-record/1
- task: implementer-model-routing
- approval: user
- rounds: 3
- final-verdict: APPROVED
- risk: unavailable (no merged risk schema)

## Goal

Let task-flow choose the implementer per task, by the size and predicted complexity of the approved plan, instead of one fixed `[roles] implementer`, configured in models.toml, recorded per task and covered by benchmark reporting.

## Acceptance criteria

- AC1: Scalar/no-config implementer selection and the predecessor's default-only table retain their behavior; strict routing validation rejects unknown profiles/keys, disabled targets, invalid bounds and incomplete rules with the originating file/key, and merge/replacement behavior is deterministic. — PASS
- AC2: Measurements count AC lines, exclude fenced examples, parse commands consistently and deduplicate declared paths without filesystem expansion; malformed plans are refused. — PASS
- AC3: Every threshold dimension has below/at/above tests, all dimensions must fit an inclusive band, L handles overflow, and missing band mappings select default. — PASS
- AC4: Task-aware roles returns the selected profile and versioned evidence; taskless roles exposes routing as unresolved with a default, other role fields remain compatible, and previews are read-only. — PASS
- AC5: An approved routed task records band, inputs and selected profile once in status History and in each round's impl JSON, while actual fallback profiles and existing metrics remain intact; mismatched --profile is refused before invocation. — PASS
- AC6: Multiple rounds and interrupted resumes reuse the attempt pin without escalation or duplicate History; plan/rule drift and enabling routing on a started unpinned attempt fail explicitly; an authorized archived epic retry gets a fresh pin without losing prior evidence. — PASS
- AC7: API and inherited CLI profiles follow the existing implementation path with their own limits/fallbacks; preflight refusals preserve artifacts and workflow stops/round advancement remain unchanged. — PASS
- AC8: A fake multi-task run shows different selected profiles and inputs before the start gate, including stacked/epic tasks; preview does not approve or pin a choice and a changed preview returns through the existing gate. — PASS
- AC9: Per-task estimates preserve nullable subscription usage; mixed-profile/fallback External USD totals remain unchanged and idempotent across resume. — PASS
- AC10: Synthetic benchmark rows retain routing evidence and aggregate by computed band/profile with protocol separation and unknown-cost handling; legacy rows remain readable and fixed-profile experiments ignore active routing. — PASS
- AC11: Docs, templates and diagrams match the delivered schema/resolver, fixed-attempt behavior and disabled threshold default; no numeric threshold recommendation claims evidence without qualifying saved repeated results. — PASS
- AC12: The verification list retains all predecessor checks, introduced failures are resolved against baseline, and the diff contains no active configuration selection, versions, changelogs, attribution lines or paid benchmark runs. — PASS

## Rounds

- round 1: CHANGES_REQUESTED — implementer codex/gpt-6.1-sol
- round 2: CHANGES_REQUESTED — implementer codex/gpt-6.1-sol
- round 3: APPROVED — implementer codex/gpt-6.1-sol

## Out of scope

Size thresholds backed by benchmark evidence are not tuned here (samples are small). Suggestions left open: pin-drift also blocks finished tasks when routing rules or a profile are edited mid-run; a comment on the prefix-based origin deletion in `models.load()`.
