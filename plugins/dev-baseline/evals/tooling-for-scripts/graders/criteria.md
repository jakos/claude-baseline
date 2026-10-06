---
type: llm
---

This rubric tests what `python-tooling` argues. "Use uv, commit the lockfile, add ruff and
a pre-commit hook" is consensus and is no longer sufficient.

PASS only if BOTH hold:

1. The git hook runs **the ruff version pinned in the project's lockfile** — a local hook
   invoking it through `uv run` or the project environment — or the answer explicitly
   names the problem of the hook's ruff version drifting from the one the project or CI
   uses.
2. The scripts are set up as a project that is **not built or installed as a package** —
   `package = false`, `uv init --bare`, a virtual or non-package project, or an explicit
   statement that no build backend is needed for scripts that are only run.

FAIL if the hook uses `ruff-pre-commit` with its own `rev` and never addresses version
drift; if it tells people to activate a virtualenv or `pip install` as the normal
workflow; or if it packages the scripts with a build backend without saying why.

Judge on substance, not on grammatical mood. A claim stated conditionally or as a
recommendation counts as present. Only its absence is a FAIL.
