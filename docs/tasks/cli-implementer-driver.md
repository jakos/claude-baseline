# Task record: cli-implementer-driver

- schema: task-record/1
- task: cli-implementer-driver
- approval: user
- rounds: 3
- final-verdict: APPROVED
- risk: unavailable (no merged risk schema)

## Goal

Add a generic `cli` driver to `implement.py`, so that an implementer round can be done by an agent command-line tool selected in `models.toml`; first target Codex (`codex exec`) with a ChatGPT login or an API key.

## Acceptance criteria

- AC1: CLI subscription and API profiles resolve with driver-specific required fields and project-over-user precedence; invalid keys, placeholders, regexes and incompatible explicit API-loop settings report file/key errors, while existing API configurations retain their behavior. — PASS
- AC2: A fake CLI receives each transport as specified, runs in the worktree with spaced/Unicode paths, sees the inlined round context and applicable conventions, and emits no raw tool output on the runner's one-line stdout. — PASS
- AC3: Only allowlisted variables plus the active profile's pass_env reach the CLI; a sentinel unrelated secret is absent, invocation-scoped CODEX_API_KEY is present only when configured, parent environment is unchanged, and secrets echoed in diagnostics/argv are redacted from persisted evidence. — PASS
- AC4: JSONL usage and cached tokens are summed without double counting, optional reasoning usage is additive, plain-output/unknown usage stays null, subscription cost is null, existing metrics keys remain, and unknown cost does not break stdout or autopilot accounting instructions. — PASS
- AC5: A fake CLI that edits tracked files and creates new files leaves the eligible result staged with accurate files_changed; pre-existing untracked files, ignored/build artifacts and nested repositories stay excluded, including when the fake CLI commits its output; original HEAD is restored only for verified descendant commits on the same branch, and incompatible history changes fail with… [truncated] — PASS
- AC6: A final BLOCKED: message yields exit 3 and the existing BLOCKED Log entry; ordinary success exits 0; timeout/non-limit process errors exit 1 without adding failure Log entries; existing preflight refusals exit 2. — PASS
- AC7: Configured limit matches and a missing executable select the next configured profile, including a fake openai-loop fallback; exhausted/disabled/unkeyed fallbacks preserve existing semantics and never silently use Claude. — PASS
- AC8: A sleeping fake CLI and its child are terminated within the timeout plus bounded cleanup; large concurrent stdout/stderr cannot deadlock the runner; scratch files are removed and structured event or heartbeat progress appears during execution. — PASS
- AC9: Native programs receive argv without shell interpretation; Windows shims use launch.py's guarded path, refuse metacharacters and preserve valid spaced arguments; the stdin/file Codex examples avoid putting prompt text in the shim command line. — PASS
- AC10: Doctor knows the CLI driver, reports executable/key issues without exposing credentials, and performs no /models HTTP request or authenticated CLI invocation for it, with API-driver checks unchanged. — PASS
- AC11: The commented template and provider documentation show Codex subscription/API configuration, verified flags, login/manual smoke procedure, null accounting, Windows launch choices and actual enforcement limits; related pages/diagrams and the root layout describe the delivered behavior. — PASS
- AC12: The complete inherited test list plus test_cli_driver.py appears in both gate registries, all listed checks pass or retain only documented baseline failures, and no version/changelog/default-role/stop-rule change is introduced. — PASS

## Rounds

- round 1: CHANGES_REQUESTED — implementer mistral-large/mistral-large-4-0
- round 2: CHANGES_REQUESTED — implementer mistral-large/mistral-large-4-0
- round 3: APPROVED — implementer mistral-large/mistral-large-4-0

## Out of scope

No heartbeat progress thread for a silent `codex-jsonl` CLI; `bench.py` tolerance for null `tokens` or `cost_usd` with a CLI bench profile; no authenticated Codex run was performed.
