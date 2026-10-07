---
name: profile-python
description: task-flow profile for Python changes — detection, conventions, verification commands and review checklist. Read by task-flow agents; not for general use.
disable-model-invocation: true
user-invocable: false
---

# Profile: python

This profile is thin on purpose. The detail lives in skills that also serve ordinary
sessions, so there is one source of truth:

- **`python-tooling`** (dev-baseline, always installed with task-flow) — uv, pyproject,
  ruff, prek, the `just check` gate, newest stable Python.
- **`python-service`** and **`pytest-suite`** (claude-python-service, when installed) —
  layering, FastAPI at the edge, Testcontainers. Load them with the Skill tool when the
  repo is a service; if they are not available, the short rules below stand in.

## Detect

`*.py`, `pyproject.toml`, `setup.cfg`, `setup.py`, `requirements*.txt`, `uv.lock`,
`noxfile.py`, `tox.ini`.

## Conventions

- Follow the repo's existing structure first. Enforce DDD layering only when
  `plan.md → Project rules` declares it: use cases do not import infrastructure,
  repositories hide persistence, mappers translate between domain, DTOs and persistence
  models.
- Type hints on new and changed functions. Small functions. Dependencies injected, not
  read from module globals.
- FastAPI: thin routers, logic in use cases, Pydantic models only at the boundary.
- Async: no blocking call (`requests`, `time.sleep`, sync DB drivers, file I/O in a loop)
  inside `async def`.
- Tests with pytest. New behaviour needs a test that fails without the change. Unit tests
  for domain and use cases with fakes; integration tests against real dependencies via
  Testcontainers for repositories and external services. Fixtures over duplicated setup.

## Verify

Discover before defaulting, in this order: `.claude/task-flow.md`, a `justfile` or
`Makefile` target (`just check`, `make check`), `[tool.*]` sections in `pyproject.toml`,
`noxfile.py` / `tox.ini`, the CI config. Prefer `uv run` when `uv.lock` exists.

Defaults when nothing is configured:

```
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy <package>        # only if mypy is configured in the repo
```

## Review checklist

- New branches and error paths have tests; a bug fix has a test that would have failed.
- No swallowed exceptions (`except Exception: pass`, log-and-continue on bad data);
  `raise … from err` keeps the cause.
- Logs and error messages carry no secrets or personal data.
- Outbound calls have timeouts; retries only on idempotent operations.
- Declared layering respected; no ORM row or Pydantic request model leaking inward.
- Public API changes reflected in docs, schemas and type hints.
