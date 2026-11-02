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

## Cases

The suite currently has three frozen cases, all built from finished tasks of this
repository:

| case | size | base | result | hidden tests |
| --- | --- | --- | --- | --- |
| `task-list` | S | `4daf419` | `7216e6c` | `tests/test_task_list.py` (13 of 14 methods selected) |
| `finish-prunes-task-folder` | M | `1a82fa1` | `cbe7683` | `tests/test_prune_task.py` (11 of 16 selected) |
| `autopilot-plan-and-run-tasks` | L | `0ed2356` | `80737dd` | `tests/test_autopilot.py` (26 of 39 selected), `tests/test_guard.py` (whole file) |

All three are Python scripts of one plugin, so **the suite is biased towards Python
tooling** until FastAPI-service and configuration cases are added — see "Adding a case"
below; there is an open slot for your own L or non-Python case. Each case's `NOTES.md`
records where it came from, which tests are excluded and why, and every adaptation made
to the copied plan.

A case is one folder `cases/<id>/` with a `case.toml` (parsed strictly with `tomllib` —
an unknown key is a refusal naming the file and the key), a `plan.md` and a `NOTES.md`:

| key | meaning |
| --- | --- |
| `id` | string, must equal the folder name |
| `size` | `"S"`, `"M"` or `"L"` — the report has one table per size |
| `repo` | path of the source repository; relative paths resolve against the case folder, so `"../../../../.."` works for this repository |
| `base` | commit-ish of the starting state, resolved with `git rev-parse --verify <x>^{commit}` |
| `result` | commit-ish holding the reference solution and the hidden tests |
| `plan` | the plan file in the case folder (default `"plan.md"`) |
| `hidden_tests` | non-empty list of repo-relative POSIX paths (no `..`, no absolute path, no backslash); a directory expands to its files at `result` via `git ls-tree -r --name-only` |
| `commands` | non-empty list of command lines run on the hidden tests — one program per line, no shell, the same rule as `gates.py`; they may pass `Class.method` selectors to the test program |

```toml
# cases/task-list/case.toml
id = "task-list"
size = "S"
repo = "../../../../.."          # this repository
base = "4daf4197b4dc24b4c78a501ab4269be89a6d7c4e"
result = "7216e6c09975f164e51686f59eb83a5114beafd5"
plan = "plan.md"
hidden_tests = ["plugins/task-flow/tests/test_task_list.py"]
commands = [
  "uv run --script plugins/task-flow/tests/test_task_list.py TaskListTest.test_hides_done_by_default ...",
]
```

`list` prints one line per case — `id  size  repo  base  result` — and exits 2 with the
reason on stderr for an invalid case.

### Hidden-test rules

- The hidden test paths and the selected test names sit in `case.toml`
  (`hidden_tests` + `commands`); a test method is selected exactly when its name occurs
  in the case's `plan.md`, and a whole file is used only when every test in it is named
  in the plan or already exists, unchanged, at the base.
- Hidden test code never appears in the case's plan: `check-case` refuses a plan whose
  lines leak a hidden line (a line of a hidden file at `result` that is not in the same
  file at `base` and is at least 30 characters long after stripping).
- The case folder is invisible to the implementer as long as the case base predates it:
  the run's scratch repository is exported from `base` only, so neither the case folder
  nor the hidden files exist in it until the hidden commands run after the last round.

### Adding a case

```
uv run --script plugins/task-flow/bench/bench.py add-case \
    --repo <path> --base <rev> --result <rev> --plan <file> --size S|M|L --id <id> \
    --command "<hidden command line>" [--command ...] [--hidden-test <path> ...] \
    [--cases-dir D] [--force-leak]
```

`add-case` writes the case folder from any repository and commit pair: it validates the
id (`^[a-z0-9][a-z0-9-]*$`, folder must not exist), the size, and that both revisions
resolve to commits with `base` an ancestor of `result` (full SHAs are written). It
prints the test paths the result commit changes as `candidate: <path>` (paths matching
`test_*.py`, `*_test.py`, `*.test.*`, `*.spec.*` or under a `tests/` / `test/`
directory) and uses them as the hidden-test candidates unless `--hidden-test` overrides
them; at least one `--command` is required, because how a repository runs its tests
cannot be guessed safely. It copies `--plan` with LF endings to `plan.md`, writes
`case.toml` with the keys above and a `NOTES.md` template, and refuses (exit 2, nothing
written) on a plan that leaks a hidden line — `--force-leak` overrides that only for a
plan that must quote a line, and records it in `NOTES.md`.

Then make the case prove it measures something:

```
uv run --script plugins/task-flow/bench/bench.py check-case <id> [--timeout-seconds S]
```

`check-case` calibrates the case in a temporary directory (never `--out`, never the
cases dir, deleted afterwards; the source repository is only read). It prints one
`PASS`/`FAIL` line per check and exits 0 only when all pass, 1 when any fails, 2 for
refusals (unknown case, invalid `case.toml`, missing git):

1. `load` — `load_case` accepts the case.
2. `commits` — `base` and `result` resolve, and `base` is an ancestor of `result`.
3. `hidden-files` — every hidden test path exists at `result`.
4. `no-leak` — no hidden line appears in the plan.
5. `visible-on-result` — the plan's Verification commands run through `gates.py` on an
   export of `result`; every one must be PASS or Not run.
6. `hidden-on-result` — the hidden `commands` all PASS on that same tree.
7. `hidden-on-base` — the hidden files written into an export of `base` make **each**
   hidden command FAIL or ERROR, so every hidden command tells base and result apart.

Checks 5–7 are `SKIP` (counted as failed) when 1–3 fail. A case may be committed only
when `check-case <id>` exits 0.

The plan copy in a case is verbatim except for these recorded adaptations (E3), each
listed in the case's `NOTES.md` with its original line:

- (a) `main` used as the base ref in a Verification command becomes `HEAD` (the scratch
  base commit is `HEAD`).
- (b) a Verification command that depends on files outside the exported tree is removed,
  together with the prose sentence that explains it.
- (c) every `uvx ruff` becomes `uvx ruff@<version>`, pinned to the version with which
  `check-case` passes.
- (d) a command that fails on the result tree only because a tool is newer than the case
  and cannot be pinned is removed; its failure line is quoted in `NOTES.md` and the risk
  is noted.

## Commands

```
uv run --script plugins/task-flow/bench/bench.py list [--cases-dir D]

uv run --script plugins/task-flow/bench/bench.py run \
    --profiles a,b [--cases id,id|all] [--repeat N] [--max-cost-usd X] [--review] \
    [--review-model M] [--claude-command JSON] [--run-id ID] [--extend ID] [--out D] \
    [--cases-dir D] [--project D] [--timeout-seconds S] [--keep-code]

uv run --script plugins/task-flow/bench/bench.py report <run-id> [--out D] [--project D]

uv run --script plugins/task-flow/bench/bench.py save <run-id> [--replace] [--out D] [--results-dir D] [--project D]

uv run --script plugins/task-flow/bench/bench.py check-case <id> [--cases-dir D] [--timeout-seconds S]

uv run --script plugins/task-flow/bench/bench.py add-case --repo P --base B --result R --plan F --size S --id ID --command C [--command ...] [--hidden-test ...] [--cases-dir D] [--force-leak]

uv run --script plugins/task-flow/bench/bench.py log [--results-dir D]

uv run --script plugins/task-flow/bench/bench.py publish [--run ID] [--results-dir D] [--docs-dir D] [--readme F] [--project D]
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
  `<results-dir>/<run-id>/`, refuses (exit 2) when the target exists, and refreshes
  `<results-dir>/log.md`. `save --replace` overwrites an existing target — needed after
  `run --extend`, which appends rows to a saved run.
- `log` rewrites `<results-dir>/log.md` from the saved runs (deterministic: the same
  inputs give byte-identical output, LF).
- `publish [--run ID]` writes the committed benchmark pages from a saved run (see
  "Re-running, adding a model and publishing"): exit 0 with exactly one stdout line,
  `published <run-id> -> <latest.md path>` plus `; rotated <old-id> -> <history path>`
  and/or `; new baseline` when they apply. Exit 2 with the reason on stderr and no file
  changed is a refusal: no saved runs, an unknown run, a mixed or older run, a missing
  marker pair, a malformed `run.json` / `results.jsonl`, an existing history target.

## Running a first pass

Three cases × 3 or 4 profiles × `--repeat 1` is a first pass:

```
uv run --script plugins/task-flow/bench/bench.py run --profiles p1,p2,p3,p4 --repeat 1 --max-cost-usd 10
```

Cap the whole run with `--max-cost-usd` (runs after the cap are recorded as `skipped`,
never dropped). A first pass should cost a few dollars — measure the real cost in
`report.md` before adding repeats or cases; `--repeat N` is what makes medians and
spread meaningful, so add repeats only after the first pass is affordable.

## Reading the outputs

- `report.md` (per run, `.work/_bench/<run-id>/report.md`): heading with run id,
  fingerprint, harness version, repeat, review on/off and cases; then one table per size
  (`S`, `M`, `L`, then `All`), one row per profile:

  `Profile | Billing | Runs | Accepted | Round 1 | Within 3 | Rounds (median) | Hidden pass
  | Out of scope | Tokens in / cached / out (median per run) | Cost per run median (min–max)
  | Cost per accepted | Seconds median (min–max)`

  Cost per accepted = (sum of `cost_usd` + `review_cost_usd`) / accepted runs, `n/a
  (0 accepted)` when none. Skipped runs count in Runs but not in the medians. `## Notes`
  says when review cost is 0 because `--review` was off, marks a subscription profile
  (cost per token unknown; compared on tokens, rounds, time and acceptance) and marks a
  profile `(partial)` with the reason. `## Failures` lists every non-accepted row and
  every accepted row with hidden `fail`: n, case, profile, repeat, outcome, reason.
- `bench/results/log.md`: one row per saved run × profile — Run, Date, Harness,
  Fingerprint (`sha256:` + first 12 hex), Cases, Profile, Model, Runs, Accepted,
  Within 3, Hidden pass, Tokens in / cached / out (median per run), Cost per accepted —
  sorted by run id, then profile order in `run.json`. It is generated by `bench.py log`
  (and refreshed by every `save`), never hand-edited. Rows are comparable only when
  their fingerprint and harness version are equal; with no saved runs the table reads
  `No saved runs yet.`

## When to re-run

Re-run when something measured changes: a new model or a new price, a new profile, or a
raised `HARNESS_VERSION` in `bench.py` (a new fingerprint — save the old runs first, so
`log.md` keeps both). Otherwise there is no need to re-run on a schedule.

## Adding a profile

1. add the profile to `.claude/task-flow.models.toml` (`models.toml`);
2. `run --extend <run-id> --profiles <new>` appends its rows to the existing run (the
   fingerprint must match — same cases, repeat and review flag);
3. `save --replace <run-id>` re-saves the extended run into `bench/results/<run-id>/`;
4. `log` rewrites `bench/results/log.md` with the new profile's rows.

## Re-running, adding a model and publishing

Re-run when something measured changes — a new model, a new price or a new profile, or
a raised `HARNESS_VERSION` in `bench.py` (a new fingerprint; see "When to re-run"). The
workflow is:

1. `run --profiles a,b [--repeat N]` — measure;
2. `save <run-id>` — keep the run in `bench/results/<run-id>/` (committed data, so the
   published pages are reproducible byte for byte from it);
3. `publish` — write the pages (below).

Adding a model to an existing run:

1. add the profile to `.claude/task-flow.models.toml`;
2. `run --extend <run-id> --profiles <new>` (the fingerprint must match);
3. `save --replace <run-id>`;
4. `publish` — the extended run appears with the new profile; no history page is
   created, because the run id is the same.

`publish` reads only `<results-dir>/<run-id>/{run.json,results.jsonl}` — with no `--run`
it takes the newest saved run (by `run.json` `created`, then run id) — and writes:

- `docs/task-flow/benchmark/latest.md` and `latest.svg` — the run's setup (cases with
  sizes, repeats, review, models with billing and prices, harness version, fingerprint,
  task-flow version), one table per size plus `All`, and the chart: tokens per accepted
  task (input / cached / output stacked) and cost per accepted task;
- `docs/task-flow/benchmark/history/<date>-<run-id>.md` + `.svg` — the previously
  published latest, copied when a new latest is written (only its chart link and index
  link are rewritten to the history paths);
- `docs/task-flow/benchmark.md` — the history index between the markers
  `<!-- bench:history:start -->` and `<!-- bench:history:end -->` (the file is created
  with `# Benchmark`, a link to this README and the empty block when missing);
- the block between the two marker lines in the root `README.md`:

  ```
  <!-- bench:latest:start -->
  <!-- bench:latest:end -->
  ```

  Each marker must occur exactly once, on its own line, start before end. `publish`
  refuses (exit 2, and nothing is written — not even the docs pages) when the README
  lacks them, and names the file and the two marker lines to add; an existing
  `docs/task-flow/benchmark.md` without the history markers is refused the same way.

A **new baseline** is a run whose fingerprint differs from the published latest's. It is
not refused — it is labelled: `latest.md` and the README block carry `New baseline: the
suite changed since run <prev-id>; results are not comparable with earlier runs.`, the
history index marks the row `new baseline`, and stdout ends with `; new baseline`. A run
*older* than the published latest, or one whose rows mix fingerprints or harness
versions, is refused (exit 2): start a new baseline with `bench.py run` and `save` it.
Results that cannot be compared are never mixed into one table.

The honesty rules, in the tables, the chart and the README block: repeats come with their
spread (median with min–max); a profile with a `skipped` / `unavailable` / `cost_cap`
row, or with missing (case, repeat) runs, is marked `(partial)` and the reason is named;
accepted is shown as `k/n (p%)` with the percentage rounded down (199 of 200 shows
`99%`, never `100%`); a subscription profile shows `subscription` in the cost columns
and no cost bar; a profile with 0 accepted shows `n/a (0 accepted)` and no bars.

The generated files are committed and must never be edited by hand — run `publish`
again instead; the same saved run gives byte-identical files, and publishing the same
run twice in a row changes no file. `publish` spends nothing and never runs the
benchmark; `run` does — that is the difference between the two.

## Run layout and outputs

`<out>/<run-id>/` holds `run.json` (run id, creation time, `harness_version`,
`repeat`, `review`, `cases`, the per-case fingerprint entries, `profiles`,
`fingerprint`, and `task_flow_version` when the run was created — not part of the
fingerprint, so a page can name the task-flow version that ran), `results.jsonl` (one
JSON object per line, keys sorted) and `report.md`. Each run `runs/<n>/` (n = 1, 2, …
across the whole run, continued by `--extend`) contains:

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
  output goes to `--out`. `check-case` works the same way in a temporary directory.
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

## Repeats, medians and spread

`--repeat N` runs each case × profile N times (order: repeat outer, then case, then
profile). The report shows medians and min–max for cost per run, tokens per run and
seconds, and cost per accepted over all repeats — the spread is the point of repeating.

## Re-running over time and the suite fingerprint

A run is identified by its run id and a **suite fingerprint**: `run.json` records the
inputs, and the fingerprint is `sha256:` + SHA-256 of the canonical JSON of the harness
version, `repeat`, the review flag and, per case, `{case_toml_sha256, plan_sha256,
base, result}` (commits resolved to full ids; file hashes over LF-normalized UTF-8
text; `NOTES.md` is not hashed, so it does not change what is measured). Hashing the
whole `case.toml` means a changed hidden test list or command changes the fingerprint.

```
uv run --script plugins/task-flow/bench/bench.py run --extend <run-id> --profiles <new>
```

recomputes the fingerprint from the current case files with the stored repeat and review
flag; on mismatch it exits 2 naming the first differing component (case id and field,
or repeat / harness version). It refuses a profile already in the run and `--cases` /
`--repeat` / `--review` values that differ from the run; otherwise it appends rows with n
continuing and regenerates `report.md`. Raise `HARNESS_VERSION` (currently `1`, a
constant in `bench.py`) whenever a change alters what is measured.

## Durability

Cases need their `base` and `result` commits, so they **work only as long as the git
history is kept**: they break if the history is rewritten, squashed away or
garbage-collected (`check-case` then fails `commits` or `hidden-files`). The plans are
copied into the case folders, so cleaning `.work/` does not matter. In a future case
whose base postdates `bench/`, the exported tree contains the other cases' folders —
their plans and hidden paths, never hidden code from the same case.

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
- The first three cases lean towards Python tooling (see Cases); a FastAPI-service or
  configuration case is the open slot for a case of your own.
- bench adds a tool; it changes no command, stop, round cap or file format of the
  workflow, and it is never run by the gates — only its test
  (`tests/test_bench.py`) is.
