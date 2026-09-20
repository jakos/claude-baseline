---
name: debug-systematically
description: Find the root cause of a bug, test failure or production incident by forming and killing hypotheses rather than changing code until symptoms stop. Use when something is broken, intermittent, or when a first fix did not hold.
---

# Debugging

The failure mode this exists to prevent: changing things until the symptom disappears,
then declaring victory. That produces code with an unexplained fix in it and a bug that
comes back in a month wearing different clothes.

## Reproduce before anything else

A bug you cannot reproduce is a bug you cannot prove you fixed.

Narrow the reproduction until it is fast and deterministic: one test, one request, one
input. If it only reproduces sometimes, find what varies — time, ordering, concurrency,
data, cache state, which machine. **"Intermittent" is a description of your knowledge,
not of the system.**

If it reproduces only in production, the next move is usually better observability, not a
cleverer guess.

## Then form hypotheses, plural

Write down two or three candidate causes before testing any of them. One hypothesis is
how you spend an hour confirming your first guess while the real cause sits unexamined.

For each, ask: **what observation would rule this out?** Then go make that observation.
A hypothesis you cannot falsify is not useful yet — sharpen it until it predicts
something specific.

Kill hypotheses; do not try to confirm them. Confirmation is where bias lives.

## Bisect the distance between working and broken

Find any two points where one works and one does not, then halve the gap:

- **In time:** `git bisect`, with a script that exits non-zero on the bug.
- **In data:** which input triggers it, which does not.
- **In the stack:** is the value already wrong when it enters this function, or does this
  function corrupt it? Check at the boundary, not at the symptom.
- **In the environment:** works locally, fails in CI — the difference is the clue, so
  enumerate the differences rather than re-reading the code.

Each halving is one measurement that eliminates half the search space. This beats
reading code, reliably, and it is the technique people skip because it feels less clever.

## Read the actual error

Read the whole stack trace, including the frames you believe are irrelevant, and
including the `during handling of the above exception` chain, which usually contains the
real cause. Read the error text literally: `NoneType has no attribute 'id'` means
something returned `None` — the interesting question is which call and why, not how to
add a guard.

An exception swallowed in a `try/except` two layers up will surface as nonsense
somewhere else. Grep for bare `except` early.

## For intermittent failures

Suspect, in this order:

1. **Shared mutable state** — a module-level cache, a class attribute, a fixture with the
   wrong scope, a connection reused across contexts.
2. **Ordering** — tests that pass alone and fail in a suite, or vice versa. Run with
   `-p no:randomly` and with random ordering to see which way it breaks.
3. **Time** — timeouts, TTLs, midnight, month ends, DST, leap days, timezone assumptions.
4. **Concurrency** — a read-then-write without a lock, a race between a retry and the
   original request.
5. **External state** — a dependency's data changed under you.

## When you find it

State the causal chain out loud: input → what the code did → why that was wrong →
observed symptom. If there is a gap in that chain, you have found *a* problem, not
necessarily *the* problem. Say so rather than papering over it.

Then:

- Write a test that fails before the fix and passes after. If you cannot, explain why —
  that is itself a finding about the code's testability.
- Fix the cause, not the symptom. A null check that stops the crash while leaving the
  value wrongly `None` is a deferred bug with worse diagnostics.
- Ask where else this pattern occurs. Bugs of a kind rarely appear once.

## When you are stuck

After an hour with no progress, the hypothesis set is probably wrong, not
under-tested. Go back and question an assumption you have not examined — that the
input is what you think, that the deployed code is the code you are reading, that the
config is what the file says, that the dependency version is what the lockfile claims.

Say plainly that you are stuck and what you have ruled out. A clear account of eliminated
hypotheses is real progress and lets someone else start from where you got to. Quietly
continuing to guess is not.
