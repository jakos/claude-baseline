---
type: llm
---

This rubric tests what `pytest-suite` argues. "Use Testcontainers, separate unit from
integration" is consensus and is no longer sufficient.

PASS only if BOTH hold:

1. The container is started **once for the suite or module**, with state reset between
   tests by transaction rollback or truncation — not a fresh container per test.
2. The schema under test comes from **the project's real migrations**, not
   `metadata.create_all` or an equivalent auto-generated schema, or the answer explicitly
   argues why migrations must be exercised.

FAIL if it starts a container per test, if it builds the schema with `create_all`, or if
it never says where the schema comes from at all.
