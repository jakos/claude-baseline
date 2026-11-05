# What enforces what

Paths such as `scripts/…`, `skills/…`, `agents/…`, `templates/…` and `bench/…` are inside `plugins/task-flow/`.

Prompts are advice; a hook is a rule. The rules live in `scripts/rules.py`
(`check_command`, `check_write`) — one implementation used by the hook
`scripts/guard.py`, which runs before every edit and shell command and applies only to
task-flow's own agents (it reads `agent_type` from the hook input), and by
`scripts/implement.py` for the external implementer (role `external-implementer`),
`scripts/role_run.py` for the external planner (role `external-planner` — its tools are
not text rules but a hard surface: only `read_file`, `list_files`, `grep` and an
argv-list `run_command` checked by `rules.check_argv` against `rules.READ_ONLY_PROGRAMS`,
with paths and symlinks contained to the selected repository roots, and no write/edit
tool at all; the trusted runner publishes the validated artifact), and
`scripts/gates.py` for the script checker (role `checker`):

- planner, backlog, checker and reviewer can write only under `.work/`;
- no task-flow agent can push, open a PR, `kubectl`/`oc apply`, `helm install`, `docker
  push` or `terraform apply`;
- no task-flow agent can commit — changes stay staged until `/task-flow:task-finish`;
- planner, backlog and reviewer cannot run git commands that change the tree or branches;
- no task-flow agent, and not the external implementer either, can snooze a habit-hooks
  finding — writing a path that ends in `.habit-hooks/snooze.json` or running
  `habit-snooze --snooze` / `habit-snooze --prune` is denied (habit-hooks' own guidance:
  never snooze without the user's explicit approval).

Commands are matched as raw text, split into `;`/`&`/`|`/newline segments, without
regard to quotes. A `git` segment with whitespace followed by a mutating verb (`add`,
`checkout`, `reset`, `worktree add`, ...) is denied for planner and reviewer; one with
whitespace followed by a commit or push verb is denied for every task-flow agent.

Run the rules' test with
`uv run --script plugins/task-flow/tests/test_rules.py`, the guard's with
`uv run --script plugins/task-flow/tests/test_guard.py`, the task list's with
`uv run --script plugins/task-flow/tests/test_task_list.py`, the prune's with
`uv run --script plugins/task-flow/tests/test_prune_task.py`, the autopilot's with
`uv run --script plugins/task-flow/tests/test_autopilot.py`, the benchmark harness' with
`uv run --script plugins/task-flow/tests/test_bench.py`, the deterministic gates' with
`uv run --script plugins/task-flow/tests/test_gates.py`, the model config's with
`uv run --script plugins/task-flow/tests/test_models.py`, the external implementer's with
`uv run --script plugins/task-flow/tests/test_implement.py`, the external planner runner's
with `uv run --script plugins/task-flow/tests/test_role_run.py`, its artifact contract's
with `uv run --script plugins/task-flow/tests/test_role_contracts.py`, the CLI driver's with
`uv run --script plugins/task-flow/tests/test_cli_driver.py`, the launcher's with
`uv run --script plugins/task-flow/tests/test_launch.py`, the release script's with
`uv run --script plugins/task-flow/tests/test_release.py` and the doctor's with
`uv run --script plugins/task-flow/tests/test_doctor.py`, the docs' with
`uv run --script plugins/task-flow/tests/test_docs.py`.

Limits of the guard, stated plainly (they apply to `implement.py`'s `run_command` too,
which uses the same rules from `scripts/rules.py` with the `external-implementer` role):

- It sees edit tools and shell command text. A shell redirect such as `echo x > file`
  from the checker or reviewer is not caught; their prompts forbid it, nothing enforces it.
  The snooze denial matches the write-tool path and the command text only: a shell
  redirect or a `cp` into `.habit-hooks/snooze.json` is not caught.
- **Known false positive:** planner and reviewer are denied when a mutating git phrase
  appears with whitespace before the verb anywhere in the command text, including inside
  quotes, e.g. `git grep -e "worktree add"`. Quotes are not parsed. Quoted single words
  such as `git log --grep 'commit'` pass. Use the Grep tool for such searches. The test
  pins this.
- The approval gate, the 3-round cap, the no-progress stop and the list of stops are
  enforced by the orchestration procedure and recorded in `status.md`; they are
  instructions, not hooks. The autopilot's circuit breaker is the same: `autopilot.py`
  computes the decision, the procedure obeys it.
- The external planner's read-only operation is enforced structurally, not by deny
  regexes: `scripts/role_run.py` offers no write/edit tool, holds `run_command` to the
  `rules.READ_ONLY_PROGRAMS` argv allowlist (for `git` only a read-only subcommand list
  behind harmless global flags, no path-taking/config/writing/pager flags; for `find`
  no `-delete`/`-exec*`/`-ok*`/`-fprint*`/`-fls`; for `rg` no `--pre*`) and contains
  resolved paths and symlinks — in the file tools and in every path-looking argv word
  alike — to the selected repository and the supplied plugin/artifact roots. A
  `driver = "cli"` producer command must be the verified read-only Codex form
  (`--sandbox read-only`, `-o {last_message_file}`, no bypass flags); the CLI's own
  sandbox is an external boundary, not a task-flow guarantee of isolation.
  `/task-flow:task-doctor` prints the method, model and billing of all five stages (its
  `## Stages` table) by inspecting the configuration only — it never launches a CLI and
  spends no model capacity.
- A `driver = "cli"` implementer (`scripts/cli_driver.py`) is a process, not a tool-call
  stream: `rules.py` and the `guard.py` hook never see its commands, so they cannot police
  its commits, pushes, network activity or child processes — its confinement is the CLI's
  own sandbox flag (e.g. `--sandbox workspace-write`) and the runner's environment
  (allowlist + `pass_env`), working directory, deadline and result capture. A credential
  passed through `pass_env` (e.g. `CODEX_API_KEY`) may reach the CLI's child commands.
  What the runner does stage is the ordinary result: tracked changes and new files, with
  a same-branch descendant commit recovered via `git reset --soft` before staging.
