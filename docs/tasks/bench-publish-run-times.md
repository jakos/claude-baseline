# Task record: bench-publish-run-times

- schema: task-record/1
- task: bench-publish-run-times
- approval: user
- rounds: 1
- final-verdict: APPROVED
- risk: unavailable (no merged risk schema)

## Goal

Publish run time next to cost and quality in the benchmark tables, for the current published results (run combined-100) and for every later published run, and state on the page how provider load affects the time figures and how to read them.

## Acceptance criteria

- AC1: Every per-size table and All table in latest.md, and the README table, contains exactly one `Seconds per run, median (min-max)` column with matching header, separator and data-cell counts. — PASS
- AC2: A canned group with accepted seconds 100 and 300, a failed row of 1 second and a skipped row produces `200.0 (100.0–300.0)` labelled `time to accepted`; changing `implement_seconds` does not change this result. — PASS
- AC3: A group with no accepted rows computes median and spread from its non-skipped finished rows, labels the result `0 accepted; time until stop`, and never presents it as time to accepted. A skipped-only or absent population shows `n/a`. — PASS
- AC4: Latest Notes and the line below the README table mention provider and network latency, rate-limit waits and retries, variation between repeated cells, comparisons only within one run's table, no cross-day comparisons, and ordering only when model min-max spreads do not overlap. Single-repeat publication explicitly calls each cell's time a single sample, not a measurement; multiple-rep… [truncated] — PASS
- AC5: Canned merged rows with starts supplied out of order produce the correct earliest-to-latest UTC start window. Missing timestamps do not cause publication failure or fabricated dates; partial timestamp coverage is identified. — PASS
- AC6: Merged-run and applicable mixed-turn-limit warnings remain in latest.md and the README block, with corresponding markers beside time figures in both outputs. No generated prose asserts a time ranking. — PASS
- AC7: Regenerated combined-100 per-size figures agree with saved `results.jsonl` total seconds to the documented precision. Its mistral-small All time uses only its accepted M row; S and L use explicitly labelled failure fallback times. Harness version remains `1`, the fingerprint remains `sha256:dbd92482d92322582311aadf791120363e2f61aa46aaa6ed9cae3850543aa91e`, and no new baseline warning appears. — PASS
- AC8: Publishing the same saved run twice produces byte-identical generated files, creates no same-run history page and leaves exactly one combined-100 index row. Generated changes use LF endings. — PASS
- AC9: Both maintained benchmark documents describe the implemented population and interpretation policy, all inherited checks introduce no new failure, and changes contain no dependency additions, saved-result mutations, version changes or changelog entries. — PASS

## Rounds

- round 1: APPROVED — implementer codex/gpt-6.1-sol

## Out of scope

Open reviewer suggestions: the mixed-turn-limit test has no negative assertion for a profile with uniform limits; the mixed-limit check in `summarize` duplicates the existing turn-limit logic. The reviewer did not re-run the real combined-100 publication a second time (same-id test and staged diff were used).
