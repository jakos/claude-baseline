---
type: llm
---

This rubric tests what `commit-and-pr` argues. "Tidy the commits, explain why" is
consensus and is no longer sufficient.

PASS only if BOTH hold:

1. It requires **every commit in the rewritten branch to leave the test suite passing**,
   or gives bisectability as the reason for slicing them that way. Separating the refactor
   from the behaviour change counts only when the reason given is reviewability or
   bisection, not tidiness alone.
2. It requires the PR description to say **what alternative approach was considered and
   rejected**, not merely why the change exists.

FAIL if commit slicing is justified only as "cleaner history"; if the PR body advice stops
at problem plus solution with no rejected alternative; or if the 11 commits are left as
they are.

Judge on substance, not on grammatical mood. A claim stated conditionally or as a
recommendation ("if you care about bisectability, …", "if you considered another
approach, say why") counts as present. Only its absence is a FAIL.
