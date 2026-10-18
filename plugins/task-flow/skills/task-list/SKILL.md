---
name: task-list
description: List open task-flow tasks under .work/ sorted by what to do next; --all includes done tasks.
argument-hint: "[--all]"
disable-model-invocation: true
---

From the repository root, run:

```
uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/task_list.py" $ARGUMENTS
```

Show its output verbatim. Do not read any `status.md` file, do not re-sort, summarise or
add rows, and change nothing. If the script exits non-zero, show its stderr.
