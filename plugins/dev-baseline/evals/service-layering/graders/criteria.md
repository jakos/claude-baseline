---
type: llm
---

PASS if the answer keeps the payments call out of the router — placing the outbound call
behind a use case, adapter, client, or equivalently named layer — AND explicitly requires a
timeout on the outbound call.

FAIL if it puts the third-party call and its retry logic directly in the route handler, or
if it discusses retries without ever mentioning a timeout.
