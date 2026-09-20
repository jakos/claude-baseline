---
type: llm
---

This rubric tests what `debug-systematically` argues. Naming plausible causes and
refusing to add a retry is consensus and is no longer sufficient.

PASS only if BOTH hold:

1. It calls for **more than one competing hypothesis to be written down before any is
   tested**, rather than pursuing the single most likely cause.
2. It frames the next action as an observation that would **rule a hypothesis out** —
   falsification, elimination, bisecting the difference between the passing and failing
   environment — rather than as evidence gathered to confirm a suspicion.

FAIL if it proposes one likely cause and goes to verify it; if the investigation is a
checklist of things to inspect with no elimination logic; or if it recommends a retry,
rerun plugin, longer sleep, or skip mark as the fix.

Judge on substance, not on grammatical mood. A claim stated conditionally or as a
recommendation ("if you care about bisectability, …", "if you considered another
approach, say why") counts as present. Only its absence is a FAIL.
