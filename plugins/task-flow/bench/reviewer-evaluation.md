# Manual reviewer comparison

No authorized live reviewer comparison results are recorded here. This protocol makes
no recommendation about external reviewer quality. Contract validators and fake-model
tests measure artifact consistency and publication safety, not semantic defect detection.
Do not send repository data to a model without explicit user confirmation of the
candidates, inputs and number of runs.

## Frozen inputs

Use temporary fixture repositories only. Reuse the source shape in
`tests/test_role_run.py` and contract fixtures in `tests/test_role_contracts.py` (paths
relative to the plugin). Freeze a valid one-AC plan requesting `feature(value)` to add
one for every integer, including negative input. Freeze passing baseline/current checks
and identical Review checklists for every reviewer. Include complete source, changed-file
list and base diff; exclude status Log, implementer notes and previous reviews. Record
a digest of all supplied bytes before each invocation and keep it identical between
candidates for a case. The automated planted fixtures run locally in test_role_run.py.

## Planted cases and clean control

Create four separate temporary directories from the same frozen case:

| Case | Deliberate change | Expected semantic finding |
| --- | --- | --- |
| implementation-defect | `return value - 1` | Wrong result for input 0 |
| missing-acceptance | Return unchanged value for negative input | Missing required negative-input behavior |
| out-of-scope | Correct feature plus unrelated extra source file | Unrequested source addition |
| clean-control | Only correct `return value + 1` | No planted defect; track false positives |

Keep the ordinary passing checks identical so the reviewer must inspect behavior and
scope. Separately run an independent local oracle against each source: input 0 must
produce 1; input -2 must produce -1; no unrelated source file is allowed. Confirm each
planted variant fails its oracle and the clean control passes before authorizing any
model execution. Contract tests deliberately accept a well-formed but semantically wrong
review when checks alone cannot disprove it; do not count that as defect detection.

## Authorized runs and scoring

After explicit authorization, run each candidate on each frozen case with the same
plan, diff, complete files, checks and checklists. Use fresh invocation IDs and no prior
review context. Record the candidate's actual model/transport metadata and independence
warnings. Save raw returned reviews and validator outcomes separately; allow the runner's
one artifact repair, counting its usage. Preserve invalid and failed attempts in the
results. Repeat the same number of trials per candidate; record the chosen count before
execution. Have a human judge findings against the private planted-defect key.

| Candidate / trial | Case / defect | Detected with correct evidence (0/1) | False positive (0/1) | Contract accepted (yes/no) | Verdict | Input / cached / output tokens | USD known / unknown | Repairs / fallbacks | Duration |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pending authorized run | implementation-defect | unmeasured | unmeasured | unmeasured | unmeasured | unknown | unknown | unmeasured | unmeasured |
| pending authorized run | missing-acceptance | unmeasured | unmeasured | unmeasured | unmeasured | unknown | unknown | unmeasured | unmeasured |
| pending authorized run | out-of-scope | unmeasured | unmeasured | unmeasured | unmeasured | unknown | unknown | unmeasured | unmeasured |
| pending authorized run | clean-control | not applicable | unmeasured | unmeasured | unmeasured | unknown | unknown | unmeasured | unmeasured |

Compute per-defect recall and clean-control false positives separately from validator
rejection. Report attempts, failures, repair/fallback counts and usage per invocation,
including billable failed attempts. Preserve null cost/usage as unknown alongside known
subtotals. Subscription billing and profile aliases do not establish independent
providers. Publish no ranking or recommendation until authorized results and the frozen
input digests, oracle checks, scoring decisions and trial counts are recorded.
