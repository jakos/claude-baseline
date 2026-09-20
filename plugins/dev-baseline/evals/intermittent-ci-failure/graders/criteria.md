---
type: llm
---

PASS if the answer treats the intermittency as something to be explained rather than
suppressed: it asks what differs between CI and the laptop, or names specific suspects such
as shared state between tests, test ordering, timing, or concurrency, and works toward a
reproduction.

FAIL if it recommends a retry, a rerun plugin, a longer sleep, or marking the test as flaky
or skipped as the primary fix.
