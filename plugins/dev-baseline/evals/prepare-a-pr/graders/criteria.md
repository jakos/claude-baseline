---
type: llm
---

PASS if the answer both (a) tells the user to reorganise the commits so that the refactor
is separate from the behaviour change, and (b) says the PR description should explain why
the change exists, not merely restate what changed.

FAIL if it accepts the 11 commits as they are, or if its PR-description advice is only a
summary of the diff.
