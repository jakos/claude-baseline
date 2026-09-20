---
name: python-service
description: Structure and extend a Python backend service using domain-driven layering — use cases, repositories, mappers, adapters — with FastAPI at the edge. Use when adding a feature, endpoint or integration to a Python service, or when deciding where a piece of logic belongs.
---

# Python service structure

> **Adapt this file.** It encodes a layering style, not universal truth. Where a project
> disagrees, the project's own `CLAUDE.md` wins. Where *you* disagree, edit this skill —
> that is the point of keeping it in your own repo.

## The layering

```
api/          FastAPI routers. HTTP in, HTTP out. No business logic.
use_cases/    One class or function per operation. Orchestration lives here.
domain/       Entities, value objects, domain errors. No I/O, no framework imports.
repositories/ Interfaces in domain terms; implementations talk to a DB or an API.
mappers/      Translate between layers. Never let a DB row reach a router.
adapters/     Outbound integrations: HTTP clients, brokers, caches.
```

The rule that makes this worth the ceremony: **dependencies point inward.** `domain/`
imports nothing from the other layers. If you find yourself importing `sqlalchemy` or
`fastapi` inside `domain/`, the logic is in the wrong place.

## Where does this code go?

Ask in order:

1. Is it a rule that would still be true with a different database and no HTTP?
   → `domain/`
2. Does it coordinate several domain objects or repositories to accomplish one operation?
   → `use_cases/`
3. Does it translate a shape? → `mappers/`
4. Does it speak a protocol — SQL, HTTP, AMQP? → `repositories/` or `adapters/`
5. Is it about request parsing, status codes or auth? → `api/`

A use case that is three lines of delegation is fine. A router with an `if` in it usually
is not.

## FastAPI at the edge

- Routers depend on use cases through `Depends`, never on repositories directly.
- Pydantic models are **API contracts**, not domain objects. Keep them in `api/schemas/`
  and map them. It is tempting to pass one straight through; the first time you need to
  change the API without changing the domain, you will be glad you did not.
- Domain errors map to HTTP in one exception handler, not in every route.
- `async def` for anything doing I/O. A blocking call in an async route stalls the whole
  event loop — if a library is sync-only, push it to a thread pool deliberately.
- Validate at the boundary and trust inward. Re-validating in every layer is noise.

## Repositories

Define the interface in domain terms (`find_active_by_owner`), not storage terms
(`select_where`). The implementation can be SQLAlchemy, an HTTP call to another service,
or an in-memory dict for tests — the use case should not be able to tell.

Return domain objects, not ORM rows. The mapper is the seam that makes the rest of the
codebase testable.

## Distributed-system concerns

Anything crossing a service boundary needs, explicitly:

- **A timeout.** No unbounded outbound call, ever. A missing timeout is how one slow
  dependency takes down four services.
- **Retry policy stated once.** Which errors are retryable, how many attempts, what
  backoff. Retrying a non-idempotent write is a correctness bug, not a resilience
  feature.
- **Idempotency** for anything a retry could duplicate.
- **Correlation propagation.** Every inbound request carries or is assigned a correlation
  id; every outbound call and log line carries it. Without this, a failure spanning three
  services is unreadable.
- **A failure mode you chose.** Degrade, queue, or fail fast — but say which, in a
  comment, not by accident.

## Environment and dependencies

`uv` for everything: `uv sync` to install, `uv add` to declare a dependency, `uv run` to
execute anything that must be reproducible. **Commit `uv.lock`.** A service whose
dependency versions are decided at install time has no reproducible build, and the
failure shows up as "works on my machine" months later.

`uv run` rather than activating a virtualenv: it resolves the project's environment every
time, so a command cannot silently run against the wrong interpreter. There is no
`pip install` in a project that has a lockfile.

Pin the Python version in `pyproject.toml` (`requires-python`) and in `.python-version`.
Two files because they answer different questions — what the package supports, and what
this checkout uses.

## Configuration

Settings come from the environment through one typed settings object, read once at
startup. No `os.getenv` scattered through the code, no config read at import time, and
no secret with a default value in source.

## What to write, and what not to

Add the abstraction when there is a second caller, not in anticipation of one. A
repository interface with exactly one implementation and no test double is a layer of
indirection that costs reading time and buys nothing yet.

The layering above exists to keep I/O out of the domain. It is not a reason to create six
files for a health-check endpoint.
