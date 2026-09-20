#!/usr/bin/env bash
# PostToolUse(Edit|Write) hook: format the file that was just written, then surface
# anything a linter could not fix automatically.
#
# Findings are returned as JSON on stdout, in hookSpecificOutput.additionalContext.
# That is the only channel a PostToolUse hook has to Claude: plain stdout on exit 0
# goes to the debug log and is never seen, and exit 2 is not honored for this event
# because the tool has already run.
#
# Prefers habit-hooks (https://github.com/habit-hooks/habit-hooks) when installed,
# because it returns actionable coaching text rather than a list of rule codes, which
# agents act on far more reliably. Falls back to plain ruff. Silent no-op when the file
# is not Python or neither tool is present.

set -uo pipefail

INPUT=$(cat)

if command -v jq >/dev/null 2>&1; then
  FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty')
else
  # Windows paths arrive JSON-escaped, so undouble the backslashes after extracting.
  FILE=$(printf '%s' "$INPUT" \
    | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    | sed 's/\\\\/\\/g')
fi

case "$FILE" in
  *.py) ;;
  *) exit 0 ;;
esac

[ -f "$FILE" ] || exit 0

# Emit findings on the one channel Claude actually reads.
emit() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$1" | jq -Rs '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: .}}'
  else
    # Pure-shell JSON string escape: backslash, quote, tab, CR, then newlines to \n.
    ESCAPED=$(printf '%s' "$1" \
      | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g' -e 's/\r//g' \
      | awk 'BEGIN { ORS = "" } { print sep $0; sep = "\\n" }')
    printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"%s"}}\n' "$ESCAPED"
  fi
  exit 0
}

# Resolve ruff. A project that pins a ruff version in its lockfile must be formatted by
# THAT ruff: formatting rules change between releases, so a globally installed 0.13
# reformatting a project pinned to 0.6 produces diff churn nobody asked for, in files the
# author did not touch. Prefer the project's own, fall back to whatever is on PATH.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
RUFF=""
if [ -f "$PROJECT_DIR/uv.lock" ] && command -v uv >/dev/null 2>&1 \
   && grep -q '^name = "ruff"' "$PROJECT_DIR/uv.lock" 2>/dev/null; then
  RUFF="uv run --quiet --project $PROJECT_DIR ruff"
elif command -v ruff >/dev/null 2>&1; then
  RUFF="ruff"
fi

[ -n "$RUFF" ] || exit 0

# Always format first, so the linters see the final shape.
$RUFF format "$FILE" >/dev/null 2>&1
$RUFF check --fix "$FILE" >/dev/null 2>&1

# Preferred: habit-hooks, when the project has opted in with a .habit-hooks/config.toml.
if command -v habit-sensors >/dev/null 2>&1 && command -v habit-mapper >/dev/null 2>&1 \
   && [ -f "${CLAUDE_PROJECT_DIR:-.}/.habit-hooks/config.toml" ]; then
  COACHING=$(habit-sensors --files "$FILE" 2>/dev/null | habit-mapper 2>/dev/null)
  if [ -n "$COACHING" ]; then
    emit "$(printf 'habit-hooks findings in %s — fix these before moving on:\n%s' "$FILE" "$COACHING")"
  fi
fi

# Fallback: whatever ruff could not fix on its own.
REMAINING=$($RUFF check "$FILE" 2>&1)
if [ -n "$REMAINING" ] && ! printf '%s' "$REMAINING" | grep -q "All checks passed"; then
  emit "$(printf 'ruff findings in %s:\n%s' "$FILE" "$REMAINING")"
fi

exit 0
