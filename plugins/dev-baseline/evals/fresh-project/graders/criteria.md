---
type: llm
---

This rubric tests what `project-bootstrap` argues for an empty project. "Use uv, add
tests, write a CLAUDE.md" is consensus and is not sufficient.

PASS only if BOTH hold:

1. Before presenting a scaffold or CLAUDE.md content as done, the answer **asks the user
   questions** whose answers would change what gets built — at least one about what must
   never happen (for example deleting or overwriting photos) or about what the tool talks
   to (the NAS, how it is reached).
2. The answer **lets the user delegate** a question they cannot answer: it offers to
   research it, look it up, or recommend an option with a reason, rather than requiring an
   answer to every question.

FAIL if it writes out a complete CLAUDE.md or file tree with invented constraints before
asking anything; if it asks nothing; or if its questions are only generic preferences
(project name, licence) that change nothing about the build.

Judge on substance, not on grammatical mood. A question or offer phrased conditionally
counts as present. Only its absence is a FAIL.
