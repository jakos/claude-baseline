# Eval suite

Every case here checks the same two things: **did the right skill fire**, and **was the
answer the one that skill is supposed to produce**. Five positive cases, one per skill, and
two negative cases that must not fire anything — because a description that triggers too
eagerly costs context in every session, which is the criticism this repo's README makes of
large skill collections.

Each case is a `prompt.md` (the message Claude receives, phrased the way a user would type
it, never naming the skill) and a `graders/` directory:

- `skill-fired.md` — `tool_used` on the `Skill` tool. Free, deterministic.
- `criteria.md` — an `llm` rubric on the answer, written as concrete PASS/FAIL conditions.

Run from the plugin root:

```bash
claude plugin eval .                    # both arms: with the plugin, and without
claude plugin eval . --ablation none    # with-arm only; half the cost, for iterating
claude plugin eval . --case service-layering
claude plugin eval . --tag negative
```

Every case runs in an empty working directory with read-only tools, so the prompts are
self-contained and nothing here touches a real repo.

**Write prompts that do not invite the model to look around.** A prompt implying there is
code to inspect ("I've inherited an existing repo…") sends it hunting through an empty
directory; it then opens its answer by asking where the code is, and the judge reads that
as a refusal and fails a case whose advice was actually correct. Either state the
situation without implying an inspectable checkout, or give the case a
`context.scaffold_script` that builds one. The first failing run of this suite was exactly
this mistake, not a skill defect.

Reading the result: `Δ` is the with-plugin score minus the no-plugin score, and it is the
number that matters — it is what the plugin *adds*. A case where the model already answers
well without the skill has a near-zero `Δ` and is telling you that skill is not earning its
context. `tool_used: Skill` graders are excluded from the score in two-arm runs (they can
never pass without the plugin) and are reported as pass/fail indicators instead; the
negative cases set `arm: both` so their must-not-fire check is scored in both arms.

Not covered yet: the `code-reviewer` agent and the `format_python.sh` hook. Both need a
scaffolded workspace (`context.scaffold_script`, which only runs under `--scaffold`).
