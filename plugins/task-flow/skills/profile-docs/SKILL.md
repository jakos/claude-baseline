---
name: profile-docs
description: task-flow profile for documentation changes — detection, conventions, verification commands and review checklist. Read by task-flow agents; not for general use.
disable-model-invocation: true
user-invocable: false
---

# Profile: docs

## Detect

`*.md`, `*.rst`, `*.adoc`, `docs/`, `mkdocs.yml`, `conf.py` (Sphinx), `README*`,
`CONTRIBUTING*`, `CHANGELOG*`, ADR directories (`docs/adr/`, `adr/`), OpenAPI
descriptions.

## Conventions

- Match the existing tone, heading style and structure; read two neighbouring pages first.
- Code examples run as written, or are explicitly marked illustrative.
- Adding or renaming a page updates the table of contents and navigation (`mkdocs.yml`
  `nav`, Sphinx `toctree`).
- Decisions go in an ADR when the repo keeps ADRs, in its existing template.
- Every command, flag, path and config key a doc mentions must exist in the code at this
  commit.

## Verify

Use what the repo configures; list the rest under Not run:

```
markdownlint-cli2 "**/*.md"          # if .markdownlint* exists
lychee --offline .                   # link check, if available
mkdocs build --strict                # if mkdocs.yml exists
sphinx-build -W -b html docs _build  # if docs/conf.py exists
```

Plus a check that is always possible: each command, flag, path and config key named in
the changed docs is found in the repository (`git grep -n -- '<name>'`).

## Review checklist

- Accurate against the current code — verified by grep, not by reading the prose.
- No stale references to renamed or removed things.
- Examples consistent with the changed behaviour.
- Readable for the stated audience; no new jargon left undefined.
