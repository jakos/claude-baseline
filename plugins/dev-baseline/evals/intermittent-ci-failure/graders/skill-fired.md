---
# Passes when debug-systematically was invoked at least once. Matches the namespaced
# dev-baseline:debug-systematically form too. Free and deterministic; excluded from the
# score in a two-arm run and reported as a plugin-fired indicator.
type: tool_used
tool: Skill
input_match: '"skill"\s*:\s*"(?:[\w-]+:)?debug-systematically"'
---
