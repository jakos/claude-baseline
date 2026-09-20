---
type: llm
---

This rubric tests what `project-bootstrap` argues. "Write a CLAUDE.md, run the tests" is
consensus and is no longer sufficient.

PASS only if BOTH hold:

1. Establishing the verification gate comes **before** writing any instructions, stated as
   an order of work — not merely mentioned somewhere in the answer.
2. It argues for a **short** CLAUDE.md on the grounds of recurring cost — that it loads
   into every session, and into subagents — or it explicitly says to leave out anything a
   linter or formatter already enforces.

FAIL if CLAUDE.md is written first and verification handled later; if it proposes an
exhaustive document covering the tech stack, style rules or directory listing; or if
length is never treated as a cost.
