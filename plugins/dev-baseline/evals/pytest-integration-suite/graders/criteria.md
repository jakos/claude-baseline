---
type: llm
---

PASS if the answer separates fast tests that avoid I/O from tests that run against a real
database, AND recommends a real Postgres (Testcontainers, Docker, or equivalent) rather
than mocking the database driver.

FAIL if it recommends mocking the database or the ORM for the database-level tests, if it
treats one undifferentiated `tests/` directory as the answer, or if it never distinguishes
the two kinds of test at all.
