---
name: pytest-suite
description: Write or restructure a pytest suite for a Python service — unit tests around the domain, integration tests against real dependencies via Testcontainers, fixtures, parametrisation and async. Use when adding tests, setting up a test harness, or when a suite is slow, flaky or hard to read.
---

# pytest suites

> **Adapt this file** to the project's actual conventions; its `CLAUDE.md` wins.

## The shape

```
tests/
    unit/          domain and use cases, no I/O, milliseconds
    integration/   real Postgres/Kafka/Redis via Testcontainers
    contract/      the shapes you promise other services (optional but cheap)
    conftest.py    shared fixtures
```

Most tests should be unit tests, because the layering exists precisely so the interesting
logic can be tested without a database. If the domain can only be tested through
Postgres, the domain has I/O in it — fix the code, not the test.

## Unit tests

Test the use case with fake repositories, not mocks of a database driver.

```python
class InMemoryOrderRepository(OrderRepository):
    def __init__(self, orders: list[Order] | None = None) -> None:
        self._orders = {o.id: o for o in orders or []}

    async def get(self, order_id: OrderId) -> Order | None:
        return self._orders.get(order_id)

    async def save(self, order: Order) -> None:
        self._orders[order.id] = order
```

A fake implementing the real interface catches signature drift; `Mock()` does not. It is
twenty lines once and pays for itself the first time you change the interface.

Reach for `unittest.mock` only at the true edges — the HTTP client, the clock, the random
source — and prefer injecting those as dependencies so you can substitute rather than
patch. `patch()` on an import path breaks whenever the module moves.

Parametrise instead of writing five near-identical tests:

```python
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (OrderStatus.DRAFT, True),
        (OrderStatus.PAID, False),
        (OrderStatus.CANCELLED, False),
    ],
)
def test_can_be_edited(status, expected):
    assert Order(status=status).can_be_edited() is expected
```

## Integration tests with Testcontainers

Real dependency, real driver, real SQL. Mocked database integration tests verify your
mocks.

```python
@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:17-alpine") as container:
        yield container


@pytest.fixture(scope="session")
async def engine(postgres) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(postgres.get_connection_url().replace(
        "postgresql://", "postgresql+asyncpg://"
    ))
    await run_migrations(engine)   # migrations, not metadata.create_all
    yield engine
    await engine.dispose()
```

Two rules that keep this fast and honest:

- **Container per session, clean state per test.** Starting a container per test turns a
  30-second suite into fifteen minutes. Roll back a transaction or truncate between tests
  instead.
- **Run your real migrations**, not `create_all`. Otherwise the suite passes against a
  schema that production will never have, and migration bugs reach staging.

Pin the image tag. `postgres:latest` means your test suite changes behaviour on a day you
did not commit anything.

## Async

Set the mode once in `pyproject.toml` rather than decorating every test:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Never `time.sleep` to wait for something. Poll with a deadline, or expose a hook the test
can await. A sleep is either too short (flaky) or too long (slow), usually both across
different machines.

## Marks and speed

```toml
markers = [
    "integration: needs Docker",
    "slow: over a second",
]
addopts = "-m 'not integration'"
```

Default to the fast suite; run the full one in CI and before pushing. A suite developers
avoid running is a suite that stops catching things.

## What makes a test worth having

Test behaviour, not implementation. A test that breaks when you rename a private method,
while the behaviour is unchanged, is a liability — you will eventually delete it under
deadline pressure, along with the ones that mattered.

Priorities, in order:

1. **Error paths.** Happy paths are easy and rarely broken. Timeouts, partial failures,
   retries, malformed input, the dependency being down.
2. **Boundaries.** Empty, one, many, maximum, just over maximum, negative, zero.
3. **Anything a bug report has already hit.** Every fix gets a test that fails before it
   and passes after. This is the highest-value test you will ever write.
4. Happy paths.

Name tests for the behaviour: `test_cancelling_a_paid_order_is_rejected`, not
`test_cancel_2`. The name is what you read at 2am in a CI log.

## Flakiness

A flaky test is a bug report, not an inconvenience. Do not add a retry and move on —
retries hide real race conditions until they reach production. The usual causes are
shared state between tests, time-of-day dependence, unordered collections compared as
ordered, and real concurrency. Find which one, then fix it.
