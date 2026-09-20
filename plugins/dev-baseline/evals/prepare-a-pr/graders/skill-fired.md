---
# Passes when commit-and-pr was invoked at least once. Matches the namespaced
# dev-baseline:commit-and-pr form too. Free and deterministic; excluded from the
# score in a two-arm run and reported as a plugin-fired indicator.
type: tool_used
tool: Skill
input_match: '"skill"\s*:\s*"(?:[\w-]+:)?commit-and-pr"'
---
