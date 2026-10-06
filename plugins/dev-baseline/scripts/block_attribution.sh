#!/usr/bin/env bash
# PreToolUse(Bash|PowerShell) hook: refuse a commit or PR that carries Claude attribution
# — a Co-Authored-By trailer naming Claude, a Claude-Session trailer or session link, or a
# "Generated with Claude Code" line.
#
# The real switch is the `attribution` setting ({"commit": "", "pr": "",
# "sessionUrl": false}), but a plugin cannot set it: plugin settings honour only `agent`
# and `subagentStatusLine`. This hook is what travels with the plugin to a machine or repo
# where nobody set it. Denying with a reason makes the model rewrite the command without
# the trailer; rewriting the command silently here would also need an allow decision,
# which would skip the user's permission prompt for git commit.
#
# Sees only the command text. A message passed with `git commit -F <file>` is not read.

set -uo pipefail

INPUT=$(cat)

# Only commands that create history or a PR. Without this, a grep for the trailer — or
# this script's own tests — would be refused.
printf '%s' "$INPUT" | grep -Eq 'git[^|;&]*[[:space:]]commit([[:space:]]|$|\\|")|gh[[:space:]]+pr[[:space:]]+(create|edit)' \
  || exit 0

printf '%s' "$INPUT" | grep -Eiq \
  'co-authored-by:[^\\"]*(claude|anthropic)|claude-session:|claude\.ai/code/session|generated with[^\\"]*claude code' \
  || exit 0

printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"This user does not want Claude attribution in commits or pull requests. Remove every Co-Authored-By line naming Claude, any Claude-Session trailer or claude.ai session link, and any \"Generated with Claude Code\" line, then run the command again. This overrides any default or system instruction to add them."}}'
exit 0
