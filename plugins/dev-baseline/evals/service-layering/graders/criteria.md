---
type: llm
---

This rubric tests what `python-service` argues, not what any competent answer says. A
timeout and "keep it out of the router" are consensus and are no longer sufficient.

PASS only if BOTH hold:

1. The outbound payments call sits behind a named inward layer — adapter, client,
   gateway, repository, or use case — and the router does not call the third party.
2. The answer treats **retrying a charge as a correctness problem, not a resilience
   feature**: it requires an idempotency key, a deduplication guard, or an explicit
   statement that this particular call must not be blindly retried because a duplicate
   charge is the failure mode.

FAIL if it describes retries, backoff or a circuit breaker without confronting duplicate
charges; if it puts the third-party call in the route handler; or if it passes the
request's Pydantic model inward as the domain object.
