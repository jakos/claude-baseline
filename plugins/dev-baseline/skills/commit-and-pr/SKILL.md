---
name: commit-and-pr
description: Write commits and pull request descriptions that stay useful months later — conventional commit format, sensible slicing, PR bodies that explain why. Use when committing, splitting work into commits, or opening a pull request.
---

# Commits and pull requests

The reader is you in six months, running `git blame` on a line that just caused an
incident. Write for that person.

## Conventional commits

```
<type>(<scope>): <subject>

<body — why, not what>

<footer — refs, breaking changes>
```

Types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, `ci`, `chore`.

The subject: imperative mood, lowercase, no trailing period, under ~72 characters.
"add retry to payment client", not "Added retry" or "adding retry".

**The body carries the why.** The diff already shows what changed; it cannot show what
you knew when you changed it.

```
fix(orders): use SELECT FOR UPDATE when claiming an order

Two workers could claim the same order when the poll interval
overlapped with a slow handler, producing duplicate fulfilment.
Row-level locking is enough here because claims are always
single-row; an advisory lock would serialise the whole table.

Fixes #412
```

Skip the body for genuinely trivial changes. A typo fix does not need a paragraph.

Breaking changes get `!` after the type and a `BREAKING CHANGE:` footer explaining the
migration. Anything that changes a stable identifier, an API contract, a database schema
in a non-additive way, or a config key is breaking, however small the diff.

## Slicing

One commit, one reason to change. The test for whether a commit is too big: can you write
its subject line without using "and"?

Separate, always:

- A refactor from a behaviour change. Reviewing a 400-line diff where two lines actually
  changed behaviour is how bugs get merged. Move the code in one commit, change it in the
  next.
- A formatting sweep from anything else.
- A dependency bump from the code that uses the new version.

Each commit should leave the tests passing. A branch where commit 3 of 7 is broken cannot
be bisected, and bisection is the main thing good history buys you.

## Before committing

Run the project's gates. Read the diff yourself — `git diff --staged` — rather than
committing what you assume you changed. Watch for debug prints, commented-out code, a
`.only` on a test, and anything that looks like a credential.

Never commit a secret. If one is already committed, rotate it: removing it in a later
commit does not remove it from history, and a rewrite does not help anyone who already
cloned.

## Pull requests

The title is a conventional-commit subject for the whole change.

The body answers three questions, in this order:

1. **Why does this exist?** The problem, the bug, the requirement. Link the issue.
2. **What approach did you take, and what did you reject?** This is the part reviewers
   actually need and the part most PRs omit. If you considered doing it another way, one
   sentence on why not saves a whole review round trip.
3. **How was it verified?** Which tests, which manual check, what you could not test and
   why.

Then call out anything that needs attention: a risky migration, a deliberate trade-off, a
follow-up you are not doing in this PR, a place you are unsure about. Flagging your own
uncertainty gets you a better review than presenting everything as settled.

Keep PRs small enough to review properly. Beyond roughly 400 lines, review quality drops
off sharply and approval starts meaning "looks plausible" rather than "I checked this".
If a change is genuinely large, say in the body where a reviewer should concentrate.

## What not to write

No "various fixes", no "update code", no "WIP" on a merged commit. No PR body that
restates the diff in prose. No summary of your own process — that the change took three
attempts is not information the repository needs.
