---
name: profile-generic
description: task-flow fallback profile for any language or file type without a dedicated profile. Read by task-flow agents; not for general use.
disable-model-invocation: true
user-invocable: false
---

# Profile: generic

## Detect

Anything no other profile claims.

## Conventions

Discover them from the repository rather than assuming: README, CONTRIBUTING, editor and
lint configs, two or three neighbouring files. Mirror the existing style, naming, error
handling and test layout exactly. Do not introduce a tool or dependency the plan did not
name.

## Verify

Find the project's own commands, in this order: `.claude/task-flow.md`; `justfile`,
`Makefile`, `Taskfile.yml`; `package.json` scripts (`test`, `lint`, `build`);
`Cargo.toml` (`cargo test`, `cargo clippy`); `go.mod` (`go test ./...`, `go vet ./...`);
`pom.xml` or `build.gradle`; the CI config. Use the CI's commands verbatim when they
exist — they are what will judge the change later anyway.

## Review checklist

- Correct for the inputs the plan names, and for empty, missing and malformed ones.
- Tests added where the ecosystem has them and the repo already tests similar code.
- Errors handled the way the surrounding code handles them.
- No unrelated changes: formatting sweeps, renames, dependency bumps the plan did not ask
  for.
