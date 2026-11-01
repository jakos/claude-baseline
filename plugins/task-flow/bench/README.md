# Benchmark harness (`bench.py`)

`bench.py` answers one question: **what does an implementer profile cost per accepted
task?** It runs the profiles from `models.toml` on frozen task cases — each case ×
profile × repeat — in fully isolated scratch repositories. Each run drives up to 3
rounds of `scripts/implement.py` with `scripts/gates.py` as the judge, then runs the
case's hidden acceptance tests, and records one row in
`.work/_bench/<run-id>/results.jsonl`. `report.md` aggregates the rows into cost per
accepted task, acceptance in round 1 and within 3 rounds, rounds to green, hidden-test
pass rate, out-of-scope changes, tokens, time, median and spread, and the failures with
reasons.

Cost per accepted task is `(implementation cost + review cost) / accepted runs`: a
cheap model that never passes the gates is not a bargain.

The harness is stdlib-only and runs on macOS, Linux and Windows:

```
uv run --script plugins/task-flow/bench/bench.py <command>
```

The real cases arrive in a later task (`bench-cases-and-report`): `bench/cases/` may be
empty here, and `list` then prints `no cases in <dir>` and exits 0. `bench/results/`
holds runs saved with `save`.

## Cases

A case is one folder `cases/<id>/` with a `case.toml` (parsed strictly with `tomllib` —
an unknown key is a refusal naming the file and the key) and a plan file:

| key | meaning |
| --- | --- |
| `id` | string, must equal the folder name |
| `size` | `"S"`, `"M"` or `"L"` — the report has one table per size |
| `repo` | path of the source repository; relative paths resolve against the case folder, so `"."` or `"../../../.."` work |
| `base` | commit-ish of the starting state, resolved with `git rev-parse --verify <x>^{commit}` |
| `result` | commit-ish holding the reference solution and the hidden tests |
| `plan` | the plan file in the case folder (default `"plan.md"`) |
| `hidden_tests` | non-empty list of repo-relative POSIX paths (no `..`, no absolute path, no backslash); a directory expands to its files at `result` via `git ls-tree -r --name-only` |
| `commands` | non-empty list of command lines run on the hidden tests — one program per line, no shell, the same rule as `gates.py` |

```toml
# cases/calc-fix/case.toml
id = "calc-fix"
size = "S"
repo = "../../../.."          # this repository
base = "main"                  # resolved to a full commit id at run time
result = "<result-sha>"        # commit with the fixed code and the hidden tests
plan = "plan.md"
hidden_tests = ["tests/test_hidden.py"]   # or a directory, expanded at result
commands = [
  "uv run --script tests/test_hidden.py",
]
```

`list` prints one line per case — `id  size  repo  base  result` — and exits 2 with the
reason on stderr for an invalid case.

## Commands

```
uv run --script plugins/task-flow/bench/bench.py list [--cases-dir D]

uv run --script plugins/task-flow/bench/bench.py run \
    --profiles a,b [--cases id,id|all] [--repeat N] [--max-cost-usd X] [--review] \
    [--review-model M] [--claude-command JSON] [--run-id ID] [--extend ID] [--out D] \
    [--cases-dir D] [--project D] [--timeout-seconds S] [--keep-code]

uv run --script plugins/task-flow/bench/bench.py report <run-id> [--out D] [--project D]

uv run --script plugins/task-flow/bench/bench.py save <run-id> [--out D] [--results-dir D] [--project D]
```

Defaults: `--cases all`, `--repeat 1`, run id UTC `YYYYMMDDTHHMMSSZ`, `--out`
`<git toplevel of --project>/.work/_bench`, `--cases-dir` the `cases/` directory next
to `bench.py`, `--project` the git toplevel of the current directory, `--results-dir`
`bench/results/`, `--claude-command` `["claude"]` (a `.cmd` shim on Windows is started
through `scripts/launch.py`).

- `run` exit 0 means the run finished and `report.md` was written — outcomes are data.
  stdout is exactly one line: `bench <run-id>: <runs> runs, <accepted> accepted,
  $<cost> -> <report path>`. Progress goes to stderr. Exit 2 with the reason on stderr
  and nothing created under `--out` is a refusal: bad arguments, an unknown, disabled
  or keyless profile, an unknown or invalid case, a missing commit, an existing run id
  without `--extend`, a fingerprint mismatch.
- `--review` (off by default) runs the real reviewer (`agents/reviewer.md`) through
  `--claude-command` with `--model <--review-model, default the reviewer's frontmatter
  model>` and `--max-budget-usd <remaining>` when a cap is set. It spends Claude limits.
- `--keep-code` keeps each run's scratch `code/` repository (deleted otherwise).
- `--timeout-seconds S` is passed to `gates.py` as the per-command timeout.
- `report <run-id>` rewrites `report.md` from `results.jsonl` (byte-identical for the
  same rows).
- `save <run-id>` copies `results.jsonl`, `run.json` and `report.md` into
  `<results-dir>/<run-id>/` and refuses (exit 2) when the target exists.

## Run layout and outputs

`<out>/<run-id>/` holds `run.json` (run id, creation time, `harness_version`,
`repeat`, `review`, `cases`, the per-case fingerprint entries, `profiles`,
`fingerprint`), `results.jsonl` (one JSON object per line, keys sorted) and
`report.md`. Each run `runs/<n>/` (n = 1, 2, … across the whole run, continued by
`--extend`) contains:

```
runs/<n>/
  code/                        the scratch repository (see Isolation)
  .work/<case-id>/             the seeded task folder: plan.md, status.md, then the
                               checks-*.md, impl-*.json and review-*.md of the run
  .work/_hidden/               plan.md whose Verification commands are the case's
                               commands, and its checks-1.md
  .claude/task-flow.models.toml  the per-run models file (only this one profile)
```

Runs are ordered repeat outer, then case, then profile, so a cost cap leaves comparable
partial data across profiles. Each row of `results.jsonl` has: `run_id`, `n`, `case`,
`size`, `profile`, `repeat`, `outcome` (`accepted`, `round_cap`, `no_progress`,
`blocked`, `failed`, `unavailable`, `cost_cap`, `error`, `skipped`), `reason`,
`rounds`, `accepted_round`, `green`, `hidden` (`pass` / `fail` / `not_run`),
`out_of_scope`, `tokens` `{input, cached, output}`, `cache_tokens` `{read, write}`,
`cost_usd` (implementation), `review_cost_usd`, `review_tokens`, `cost_known`,
`billing`, `model`, `settings` (the resolved profile at run time, prices included, no
secrets), `seconds`, `implement_seconds`, `started_at`, `fingerprint`,
`harness_version`.

One run does: export the base, seed `status.md` with `plan-approved: yes`, run
`gates.py --round 0`, then rounds 1..3 of `implement.py` each followed by `gates.py`.
Green means no introduced failure against `checks-0.md` (the contract's pre-existing
rule). Green without `--review` is `accepted` at that round; with `--review` the
reviewer's `APPROVED` accepts it. Otherwise round r < 3 gets a synthesized
`review-<r>.md` whose Fix list names each introduced failure with its excerpt, and the
contract's no-progress rule (equal failing sets in rounds r-1 and r, r ≥ 2) and round
cap apply. Out-of-scope paths (`git diff --cached --name-only HEAD` against the plan's
`## Profiles` / `## Steps` tokens) are recorded. Only when the final round's gates were
green are the hidden test files written into `code/` from
`git show <result>:<path>` and the hidden commands run.

## Isolation and safety

- **Export, not `git worktree`.** Each run's `code/` is a fresh git repository built
  from `git archive --format=tar <base>` (extracted with `tarfile`, `filter="data"`),
  then `git init -q -b bench`, `git add -A` and a base commit written with plumbing
  only (`write-tree`, `hash-object -t commit -w --stdin`, `update-ref`). A `git
  worktree` would share the source repository's object store, so the implementer could
  read the hidden tests with `git show <result>:...`; the export has no history and no
  result objects. The source repository's working tree, index, refs and worktree list
  are never touched, and the real `.work/<id>/` folders are never read or written —
  output goes to `--out`.
- **Hidden tests stay hidden.** The hidden files never exist in `code/`, in the task
  folder or in any prompt before the final green round; they are written into `code/`
  only afterwards, as bytes from `git show <result>:<path>`.
- **`--max-cost-usd X` caps the whole run.** Each run's models file gets
  `max_cost_usd` = the smaller of the profile's own value and the remaining budget, and
  `fallback = []` — a measurement measures one profile, a silent fallback would
  attribute another model's work to it. Runs after a cap (or an `unavailable` profile)
  are recorded as `skipped`, never dropped.
- Every profile is resolved with `models.load(--project)` before anything runs; an
  unknown, disabled or keyless profile is a refusal.

## Report

`report.md` is deterministic for a given `results.jsonl` (rows sorted by `n`; the same
rows give byte-identical output; `report <run-id>` regenerates it). Heading with the run
id, fingerprint, harness version, repeat, review on/off and cases; then one table per
size (`S`, `M`, `L`, then `All`), one row per profile:

`Profile | Billing | Runs | Accepted | Round 1 | Within 3 | Rounds (median) | Hidden pass
| Out of scope | Tokens in / cached / out (median per run) | Cost per run median (min–max)
| Cost per accepted | Seconds median (min–max)`

Cost per accepted = (sum of `cost_usd` + `review_cost_usd`) / accepted runs, `n/a
(0 accepted)` when none. Skipped runs count in Runs but not in the medians. `## Notes`
says when review cost is 0 because `--review` was off, marks a subscription profile
(cost per token unknown; compared on tokens, rounds, time and acceptance) and marks a
profile `(partial)` with the reason. `## Failures` lists every non-accepted row and
every accepted row with hidden `fail`: n, case, profile, repeat, outcome, reason.

## Repeats, medians and spread

`--repeat N` runs each case × profile N times (order: repeat outer, then case, then
profile). The report shows medians and min–max for cost per run, tokens per run and
seconds, and cost per accepted over all repeats — the spread is the point of repeating.

## Re-running over time and adding a model

A run is identified by its run id and a **suite fingerprint**: `run.json` records the
inputs, and the fingerprint is `sha256:` + SHA-256 of the canonical JSON of the harness
version, `repeat`, the review flag and, per case, `{case_toml_sha256, plan_sha256,
base, result}` (commits resolved to full ids; file hashes over LF-normalized UTF-8
text). Hashing the whole `case.toml` means a changed hidden test list or command
changes the fingerprint.

```
uv run --script plugins/task-flow/bench/bench.py run --extend <run-id> --profiles <new>
```

recomputes the fingerprint from the current case files with the stored repeat and review
flag; on mismatch it exits 2 naming the first differing component (case id and field,
or repeat / harness version). It refuses a profile already in the run and `--cases` /
`--repeat` / `--review` values that differ from the run; otherwise it appends rows with n
continuing and regenerates `report.md`. Raise `HARNESS_VERSION` (currently `1`, a
constant in `bench.py`) whenever a change alters what is measured.

## Limits, stated plainly

- The scratch repository has **no git history** (the implementer sees none), and
  `export-ignore` attributes apply to the export.
- The scope rule behind `out_of_scope` is a heuristic over the plan's text (a token of
  `## Profiles` / `## Steps` that equals the path, is a parent directory of it, or
  fnmatch-matches it). It is recorded, never used to fail a run.
- A `billing = "subscription"` profile has no known cost per token, so it shows
  `subscription` in both cost columns and is compared on tokens, rounds, time and
  acceptance instead of dollars.
- Without `--review`, `review-<r>.md` is synthesized from the introduced failures, not
  written by a reviewer; `--review` drives the real `claude` CLI (flags as of
  `claude --help`), which spends Claude limits — a manual `--review` run is the only
  real check of those flags.
- bench adds a tool; it changes no command, stop, round cap or file format of the
  workflow, and it is never run by the gates — only its test
  (`tests/test_bench.py`) is.
