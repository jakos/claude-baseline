# Benchmark harness (`bench.py`)

`bench.py` answers one question: **what does an implementer profile cost per accepted
task?** It runs the profiles from `models.toml` on frozen task cases — each case ×
profile × repeat — in fully isolated scratch repositories. Each run drives up to 3
rounds of `scripts/implement.py` with `scripts/gates.py` as the judge, probes every
green round with isolated hidden acceptance tests, and records one row in
`.work/_bench/<run-id>/results.jsonl`. `report.md` aggregates the rows into cost per
accepted task, acceptance in round 1 and within 3 rounds, rounds to green, hidden-test
pass rate, out-of-scope changes, tokens, time, median and spread, and the failures with
reasons.

The [version-2 protocol](../../../docs/task-flow/benchmark.md#measurement-protocol-version-2)
fixes settings and sequential execution, keeps normal provider caching, and requires
five completed independent repeats per case/profile for a descriptive ranking.
Smaller or incomplete samples are exploratory; repetitions alone do not control
cache or time effects. Gates-only is the main comparison; explicitly choose a case
subset for a separate `--review` comparison. The protocol also defines the provisional
regression alarm and the optional, manual cache experiment; neither runs here.

Cost per accepted task is `(implementation cost + review cost) / accepted runs`: a
cheap model that never passes the gates is not a bargain.

The harness is stdlib-only and runs on macOS, Linux and Windows:

```
uv run --script plugins/task-flow/bench/bench.py <command>
```

## Cases

### CLI tool/authentication matrix (checked 2026-11-06)

All authenticated tests: **untested**. These are interface and terms checks, not
permission inferred from a login. Recheck the linked terms for the actual account
before confirming a run. Unresolved subscription routes are excluded from the
manual checklist until resolved.

| Tool | Headless command / output | Configuration isolation | Authentication, billing and scripted subscription status |
| --- | --- | --- | --- |
| Codex | [`codex exec`, stdin `-`, `--json`](https://learn.chatgpt.com/docs/non-interactive-mode); Codex JSONL parser | [`CODEX_HOME`](https://learn.chatgpt.com/docs/config-file/environment-variables), file credential store; OS policies and repository instructions still apply | [User runs `codex login` in the isolated home](https://learn.chatgpt.com/docs/auth), using ChatGPT; noninteractive account workflows are documented. [Plan quota/credits](https://learn.chatgpt.com/docs/pricing), not a measured dollar charge. API-key login is a separate API route. Account/admin restrictions still require user review. |
| Gemini CLI | [`gemini -p`, stdin context, text / JSON / stream-json](https://geminicli.com/docs/cli/headless/); preset uses text, usage unknown | [`GEMINI_CLI_HOME`](https://geminicli.com/docs/reference/configuration/); system/project configuration remains possible | [`GEMINI_API_KEY` or cached Google login](https://geminicli.com/docs/get-started/authentication/). [Quotas/prices](https://geminicli.com/docs/resources/quota-and-pricing/) and [terms differ by auth route](https://geminicli.com/docs/resources/tos-privacy/). API key is the proposed test route; subscription Google-login automation remains unresolved here and is excluded. |
| Copilot CLI | [`copilot -p`, `--output-format json` emits JSONL](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference); no usage parser | [`COPILOT_HOME`](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference), auto-update disabled; managed settings remain | User provisions `COPILOT_GITHUB_TOKEN` through GitHub's documented authentication. [Plan allowances/billing](https://docs.github.com/en/copilot/get-started/plans) apply. Programmatic operation is documented, but this benchmark's subscription terms review is unresolved; excluded from checklist pending account-specific review of [GitHub terms](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service). |
| OpenCode | [`opencode run`, text / JSON events](https://opencode.ai/docs/cli/); no usage parser | [XDG configuration](https://opencode.ai/docs/config/), separate data/cache/state directories; project/system configuration remains | [Providers and authentication](https://opencode.ai/docs/providers/) are separate contracts. OpenCode's own Go/Zen service is not an upstream subscription entitlement. Proposed route: user-provided OpenAI API key, API billing. OpenCode Go/Zen and every upstream OAuth/subscription route remain unresolved for this checklist; none is recommended by a working login. |
| Claude | [`claude -p`, stdin, text / JSON / stream-json](https://code.claude.com/docs/en/headless); no usage parser | [`CLAUDE_CONFIG_DIR`](https://code.claude.com/docs/en/settings); managed/project settings still apply | Proposed route: user-provided `ANTHROPIC_API_KEY`, API billing. [Costs](https://code.claude.com/docs/en/costs) distinguish plan and API usage; [legal guidance](https://code.claude.com/docs/en/legal-and-compliance) identifies applicable agreements. Pro/Max automated subscription route remains unresolved here and excluded pending terms review. |

Only official CLI clients are proposed. No third-party adapter receives subscription
credentials. Provider terms, quotas and pricing can change independently of CLI
flags. No numeric subscription fee is assumed: record the user's actual plan fee
as contextual information, never as measured marginal run spending.

Additional checked evidence: [Copilot authentication](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli)
documents user-owned fine-grained PATs with Copilot Requests permission (classic PATs
are unsupported); [additional product terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#github-copilot)
select different agreements by purchaser/account. OpenCode's own [Go pricing](https://opencode.ai/docs/go/),
[Zen model pricing](https://opencode.ai/docs/zen/) and [service terms](https://opencode.ai/legal/terms-of-service)
do not grant upstream provider subscription rights. No Go/Zen or upstream subscription
route is cleared here. Codex's [automation guidance](https://learn.chatgpt.com/docs/non-interactive-mode)
distinguishes invocation-scoped API keys from advanced account-auth CI workflows,
and excludes the latter workflow for public/open-source repositories. This checklist
is for local user-run native CLI sessions, not CI credential seeding; unresolved
account or organizational restrictions still exclude a route from execution.
Codex's applicable [Terms of Use](https://openai.com/policies/terms-of-use/)
and [Service Terms](https://openai.com/policies/service-terms/#4-codex-and-code-generation)
also govern the documented native-client workflow; the former links regional and
business agreements. The interface documentation does not override those agreements
or authorize credential sharing, scraping or quota circumvention. Resolve any
account-specific conflict before including that route in the confirmed run.

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
  nor the hidden files exist in it. Every green round is probed in a disposable copy
  outside the model's repository and task roots, removed before review or another round.

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
    [--infra-retries N] \
    [--cases-dir D] [--project D] [--timeout-seconds S] [--keep-code] [--cli-state-dir D]

uv run --script plugins/task-flow/bench/bench.py report <run-id> [--out D] [--project D]

uv run --script plugins/task-flow/bench/bench.py run --resume <run-id> [--out D] [--project D] [--cases-dir D]

uv run --script plugins/task-flow/bench/bench.py save <run-id> [--replace] [--out D] [--results-dir D] [--project D]

uv run --script plugins/task-flow/bench/bench.py check-case <id> [--cases-dir D] [--timeout-seconds S]

uv run --script plugins/task-flow/bench/bench.py add-case --repo P --base B --result R --plan F --size S --id ID --command C [--command ...] [--hidden-test ...] [--cases-dir D] [--force-leak]

uv run --script plugins/task-flow/bench/bench.py log [--results-dir D]

uv run --script plugins/task-flow/bench/bench.py merge <run-id> <run-id> ... --into <new-id> [--out D] [--project D]

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
- `--resume ID` uses the recorded cases, profiles, repeats, review settings, budget,
  retry policy and scheduling order. Profiles may be omitted. It executes only missing
  `(case, profile, repeat)` cells; failed, skipped and capped rows remain completed data.
  A complete run makes no model calls. Resume refuses conflicting explicit options,
  changed profile/reviewer settings, fingerprints, malformed/truncated rows and duplicate
  identities before mutation. Restore original records when evidence is damaged.
  `--resume` is mutually exclusive with `--extend` and `--run-id`. Interrupted scratch
  numbers are never reused; persisted attempt spend counts, and missing spend is unknown.
- `--infra-retries N` defaults to 2 additional attempts, accepts 0, refuses negatives.
  Request-level profile retries remain independently bounded. Only structured exhausted
  HTTP 408/429/5xx, connection failures and transport/process timeouts qualify. Model
  outcomes, auth/configuration failures, malformed responses, visible test timeouts and
  unknown failures do not. Retries restore code, index, untracked files and task artifacts
  to their pre-attempt state. Attempts are recorded outside the restored task directory.
  Identifiable reviewer process timeouts follow the same bound. All known attempt usage,
  reviewer usage and elapsed time count; unknown billing stays unknown. With a spending
  cap, uncertain billing stops further spending because remaining budget is not safe.
- `report <run-id>` rewrites `report.md` from `results.jsonl` (byte-identical for the
  same rows).
- `save <run-id>` copies `results.jsonl`, `run.json` and `report.md` into
  `<results-dir>/<run-id>/`, refuses (exit 2) when the target exists, and refreshes
  `<results-dir>/log.md`. `save --replace` overwrites an existing target — needed after
  `run --extend`, which appends rows to a saved run.
- `merge a b ... --into m` combines the rows of several runs of one suite into the new
  run `m`. Sources apply in order: a later run's row for a cell (case, profile, repeat)
  replaces the earlier one, cells new to the result are appended, and rows are
  renumbered. Merges reuse source observations and are exploratory, not independent
  repeat evidence. Retain capped outcomes; changed caps require a separately labelled
  experiment. The sources must agree on measurement settings and harness
  version, repeat and review flag, and on the fingerprint entry of every case they share;
  otherwise exit 2 and nothing is written. The merged run's fingerprint is computed from
  the union of its cases, so merging runs that cover a whole suite gives that suite's
  fingerprint. `run.json` records `merged_from`, each row records `source_run`, and
  `publish` notes both, plus any profile whose rows ran with different `max_turns`.
- `log` rewrites `<results-dir>/log.md` from the saved runs (deterministic: the same
  inputs give byte-identical output, LF).
- `publish [--run ID]` writes the committed benchmark pages from a saved run (see
  "Re-running, adding a model and publishing"): exit 0 with exactly one stdout line,
  `published <run-id> -> <latest.md path>` plus `; rotated <old-id> -> <history path>`
  and/or `; new baseline` when they apply. Exit 2 with the reason on stderr and no file
  changed is a refusal: no saved runs, an unknown run, a mixed or older run, a missing
  marker pair, a malformed `run.json` / `results.jsonl`, an existing history target.

## Running a first pass

### Manual CLI run checklist

Do not run authenticated benchmarks without the user's explicit confirmation of
the profiles, models, account routes, quotas and spending limits. No authenticated
measurement was performed when these presets were added.

1. **Codex first, before 2026-11-25.** Review the dated matrix above, pin an installed
   CLI build and enable only `bench-codex` from the commented template. Check the
   selected model is available to the account. Set an explicit run id and a private
   `--cli-state-dir` separate from `--out`. Nothing copies personal configuration.
2. Prepare each cell's private state at
   `<state>/<run-id>/<case>/<profile>/<repeat>/`. The tool home is `tool/`; HOME and
   USERPROFILE are `home/`, XDG roots are `config/`, `data/`, `cache/`, `state/`.
   For Codex, set CODEX_HOME to that cell's `tool/` and run `codex login` yourself
   with file credential storage (`cli_auth_credentials_store = "file"` in its
   `config.toml`). Repeat the documented user login for every scheduled cell;
   never copy an entire personal directory or extract credentials. Retain this
   private state outside saved/published artifacts and restore your shell's env
   after preparation. The harness creates empty state directories if absent, but
   never logs in. API-key routes instead require the named env var in `pass_env`.
   Gemini stores user files under `tool/.gemini/`; the other tool-specific overrides
   point directly at `tool/`. OpenCode also uses the isolated XDG directories.
3. One exploratory pass is 3 cases × 1 profile × 1 repeat = **3 cells**, up to
   **9 implementation rounds**, plus at most two infrastructure attempts per round
   by default. Five repeats mean **15 cells / up to 45 rounds** per profile.
   All five tools at five repeats mean **75 cells / up to 225 rounds**, before
   retries and optional review. Plan subscription quota exposure for the entire
   CLI-owned loop, not one request per round. Record the actual subscription fee
   using optional `subscription_monthly_usd`; it is context, not marginal spend.
4. For API routes, verify current model input/cached/output prices and estimate
   `sum((uncached_input * P_input + cached_input * P_cached + output * P_output)
   / 1_000_000)` across attempts. Add reviewer charges/limits for a separate
   `--review` subset. Set provider/account spending limits and a CLI deadline.
   `--max-cost-usd` limits the API loop and later scheduling, **not a CLI's internal
   spending**. Other CLI presets use `usage_format = "none"`; tokens and cost are
   null and an API budget cannot be enforced inside that invocation. An unknown
   API charge stops subsequent scheduling under a harness cap. Never interpret
   unknown usage as zero or estimate tokens from text length.
5. After user confirmation, this command runs the exploratory Codex pass (all
   paths relative to the current project; provision the matching state first):

   ```
   uv run --script plugins/task-flow/bench/bench.py run --profiles bench-codex --run-id cli-codex-20261106 --repeat 1 --infra-retries 0 --keep-code --cli-state-dir .bench-private --out .work/_bench
   ```

   Review unavailable/failed outcomes and test-change evidence as data. Keep the
   original run; choose a fresh run id for changed settings. Next consider Gemini
   API, then Copilot **only after its unresolved terms review**, OpenCode API,
   then Claude API. Google-login, OpenCode Go/Zen/upstream subscriptions and Claude
   subscription routes stay excluded until their terms decisions are resolved.
6. `--keep-code` retains code for later scoring. Disk scales with cells, attempt
   backups during execution, retained scratch repositories and private CLI state;
   measure local free space before scheduling, since no fixed size is promised.
   `save` keeps only run metadata, rows and report, **not code or credentials**.
   Archive scoring code separately after inspecting it for secrets. After scoring,
   remove only that run's scratch `runs/<n>/code/` directories and its private
   `<state>/<run-id>/` directories; preserve `run.json`, `results.jsonl`, attempts
   and reports. Check resolved paths before manual removal. Revoke/logout credentials
   through each tool's documented user flow before removing private state.

`benchmark_isolation` selects one supported tool policy; unsupported/missing policy,
missing executable/version probe, or omitted `--cli-state-dir` produces an explicit
unavailable cell. `benchmark_version_command` optionally overrides the version-only
argv; it must be an offline version command, never an agent invocation. Neither key
changes ordinary implementer defaults. Only PATH, PATHEXT, Windows system/launcher
variables, locale and temporary-directory variables plus explicit `pass_env` names
reach the tool; HOME, app-data, temp and XDG roots are overridden per cell. Copilot
auto-update is disabled. Do not approve personal config-path env vars.

Provenance records executable name/digest, version, sanitized argv, model, billing,
usage parser, approved env names, isolation limitations and configuration digests.
The configuration digest covers `config.toml`, `config.json`, `settings.json`,
`settings.local.json`, `mcp.json`, `mcp-config.json`, `lsp-config.json`,
`opencode.json`, `opencode.jsonc`, `cli.json`, `AGENTS.md`, `GEMINI.md`, `CLAUDE.md`
and `copilot-instructions.md` in prepared cell state. Only digests are recorded,
including for mixed settings/auth files such as Copilot's `config.json`; separate
auth files, sessions and logs are excluded. Executable argument files are hashed too.
Do not add unrecorded plugins/extensions or managed config between repeats: managed
settings and OS credential stores are residual access, not fully captured isolation.
Suite fingerprints remain extendable; per-profile fingerprints include resolved
settings and CLI provenance. Resume and extension recheck existing profiles and
refuse changed settings/provenance before changing run artifacts. Legacy provenance
is explicitly unknown and cannot establish a compatible CLI resume.

API and CLI loops differ: the CLI owns prompts, tools, repository instructions,
configuration and network access. `rules.py` does not govern its internal loop.
Per-cell state isolation is not a security sandbox. History-free scratch repositories
prevent local history access but cannot prevent network retrieval of public solutions
or tests. Hidden probes remain disposable and outside implementation roots.

Final state is measured against an immutable harness-owned base through a temporary
index, covering committed, staged, unstaged and untracked files without changing the
CLI's index. Commit/reflog evidence includes descendant commits soft-reset by the
ordinary implementer. Visible-test additions, modifications and deletions are reported
alongside scope exceptions; green gates do not imply untouched tests. A malicious
tool can erase its reflog, so commit counts are observed evidence, not an audit proof.

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
Moved to [docs/task-flow/benchmark.md](../../../docs/task-flow/benchmark.md#re-running-adding-a-model-and-publishing).

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
  attempts/                   bounded implementation/reviewer attempt evidence
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
green does its probe supply final `hidden` pass/fail. A final non-green state is
`not_run`, even after an earlier passing probe. Every green round is copied in full
(staged, unstaged, untracked files and index) to a temporary repository outside model
roots. Only there are hidden files installed and commands run; the copy is removed
even on failure. Probe output is captured silently. `hidden_observations` records round
and pass/fail/error solely in harness data; `round1_hidden` and
`earliest_hidden_pass_round` describe initial quality and earliest observed success.
Probe results never affect acceptance, review prompts, feedback or stopping rules.
Legacy rows have unavailable intermediate observations. `attempts` includes role,
logical round, attempt number, elapsed time, known usage and structured failure evidence.
`run.json` additionally records resolved profile settings without keys, reviewer settings,
retry policy, prompt-input digests and `execution_order`.

The per-case repeat table reports median, IQR and min?max for accepted rounds, earliest
observed hidden-pass rounds, implementation plus reviewer tokens and cost, and wall
time. Quantiles interpolate linearly at `(n - 1) * p`; IQR is Q(0.75) minus Q(0.25).
Quality denominators include all scheduled cells, with unavailable legacy values and
not-observed outcomes explicit. Total known work also includes interrupted attempts;
unknown API billing is labelled `unknown`, while subscriptions remain `subscription`.
The size and All tables are cross-case summaries, not independent repeat samples.

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
- **Hidden tests stay hidden.** Hidden files, commands and captured output never exist
  in the implementation checkout, task folder, index or git objects. Probe copies are
  deleted before implementers or reviewers run; only harness-owned observations survive.
- **`--max-cost-usd X` caps the API loop and later scheduling.** Each API run's models file gets
  `max_cost_usd` = the smaller of the profile's own value and the remaining budget, and
  `fallback = []` — a measurement measures one profile, a silent fallback would
  attribute another model's work to it. Runs after a cap (or an `unavailable` profile)
  are recorded as `skipped`, never dropped.
  A CLI owns internal spending; apply provider limits separately (see the checklist).
- Every profile is resolved with `models.load(--project)` before anything runs; an
  unknown, disabled or keyless profile is a refusal.

## Repeats, medians and spread

`--repeat N` runs each case × profile N times sequentially (recorded order: repeat
outer, then case, then profile). Per-case tables show medians, IQR and min–max for
total implementation plus reviewer tokens/cost and wall time, accepted round, initial
and final hidden pass, earliest observed hidden pass, extra attempts and coverage.
Quantiles interpolate linearly at `(n - 1) * p`; IQR is Q(0.75) minus Q(0.25).
Unsuccessful, unavailable and not-observed measurements stay explicit in denominators.
At least five completed independent repeats per case/profile are required for a
descriptive ranking. Cross-case summaries are labelled; never rank across versions,
fingerprints, review modes or differing measurement settings.

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
continuing and regenerates `report.md`. Raise `HARNESS_VERSION` (currently `3`, a
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

## Promoting routing evidence manually

The shipped default is disabled routing with no numeric thresholds. User-supplied bounds
are deterministic policy, not a benchmark-backed recommendation. Do not recommend or
enable default thresholds until saved repeated evidence qualifies:

1. Save run ids and protocol fingerprints, keeping review modes, routing rule digests
   and protocol versions separate.
2. Record per-computed-band repeat counts meeting the established minimum of five
   completed independent repeats per case/profile for a descriptive ranking.
3. Review median and spread, acceptance, hidden-pass and cost, keeping unknown costs
   unknown and checking profile-specific caps and actual fallback evidence.
4. Link the qualifying saved runs and explain the promotion decision before changing
   any recommended defaults. No qualifying saved evidence is claimed here.

Harness results add optional `routing` evidence copied from implementation metrics.
Legacy rows without it remain readable and explicitly unknown. Declared case `size`
is distinct from computed routing `band`; case labels never prove a routing decision.
Reports aggregate computed band/selected profile only within a protocol fingerprint,
review mode, routing schema and rule digest. Costs stay unknown when unavailable.
Fixed case/profile/repeat experiments replace active user routing with their explicit
scalar profile. Harness version 2 changes the protocol fingerprint; version-1 results
remain historical evidence and need a new baseline for the per-round probe. These
reports do not contact paid models to calibrate thresholds.
