---
# Passes when python-service was invoked at least once. Matches the namespaced
# dev-baseline:python-service form too. Free and deterministic; excluded from the
# score in a two-arm run and reported as a plugin-fired indicator.
type: tool_used
tool: Skill
input_match: '"skill"\s*:\s*"(?:[\w-]+:)?python-service"'
---
