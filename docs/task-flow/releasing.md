# Releasing

Paths such as `scripts/…`, `skills/…`, `agents/…`, `templates/…` and `bench/…` are inside `plugins/task-flow/`.

`/task-flow:task-release` releases one target — a plugin or a project — in one controlled
procedure, and `scripts/release.py` computes every decision, so the command is a thin
wrapper around it. The procedure is **Release** in
[skills/workflow/orchestration.md](../../plugins/task-flow/skills/workflow/orchestration.md): list the targets,
`check` (every precondition, one JSON report), `verify` (the target's checks, run in the
background), optionally `apply --dry-run`, then **one approval gate — always asked, there
is no `--auto` for it** — then `apply` (the version bump in every version file, the
changelog entry, the release commit `chore(release): <target> <version>` and the annotated
tag), and finally a separate push question, default no. Nothing changes before the
approval. `--dry-run` runs check, verify and `apply --dry-run` and asks nothing.

The targets live in `.claude/task-flow.release.toml` (override with `--config <path>`);
[`templates/release.toml`](../../plugins/task-flow/templates/release.toml) is a commented example. The file is
parsed strictly with `tomllib` — unknown keys anywhere are a config error (exit 2). Schema:

```toml
[targets.<name>]
tag = "<name>-{version}"          # required; must contain {version} exactly once
changelog = "path/CHANGELOG.md"   # required, repo-relative
scope = ["path", ...]             # required, non-empty; only commits touching these count
branches = ["main"]               # optional, default ["main"]
checks = [["cmd", "arg"], ...]    # optional, default []; each an argument list, no shell quoting
[[targets.<name>.version_files]]  # required, at least one
path = "package.json"             # repo-relative
format = "json"                   # "json" | "toml"
selector = "version"              # dot-separated; a segment is key or key[field=value]
```

The selector picks the version value: `version` (plugin.json, package.json),
`plugins[name=task-flow].version` (a marketplace-style list, exactly one element must
match), `project.version` (pyproject.toml), `package.version` (Cargo.toml). TOML files
allow only plain `key` segments, because the write is a single-line replacement.

Version rule: strict `MAJOR.MINOR.PATCH`. With no explicit version and no flag, a breaking
change (`!` after the type or scope, or `BREAKING CHANGE:` in the body) gives major —
except below 1.0.0, where it gives minor and never an automatic 1.0.0 (`--major` on 0.x
does give 1.0.0); any `feat` gives minor; anything else gives patch. An explicit version is
taken as given and must be higher than the current one. The commit range starts at the
highest tag matching the target's tag pattern; `--since <ref>` overrides it. **The first
release has no tag: `check` refuses with `no-base` and asks for `--since <ref>`**, so the
first changelog entry states its starting point once, explicitly.

Two rollbacks: pin an earlier tag (check out `<target>-<old version>`, or pin an
installed plugin's marketplace to it as described in [Rolling back](external-implementer.md#rolling-back)), or
simply not push — the release commit and the tag stay local, and you may remove them
yourself; the command never deletes or moves a tag, never force-pushes, and never pushes
without its own question.

The checks list has no shell glob: each `plugins/task-flow/tests/test_*.py` is one
`["uv", "run", "--script", <file>]` entry in `.claude/task-flow.release.toml`, so a new test
file must be added there by hand.
