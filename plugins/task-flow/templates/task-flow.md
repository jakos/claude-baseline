# task-flow configuration

<!-- Copy to .claude/task-flow.md in your repository and commit it. Every section is
     optional; delete what you do not need. The planner reads this file and copies the
     parts that apply into plan.md, so the other agents never read it directly.
     Plain Markdown on purpose: agents read it, no parser does. -->

## Profiles

- extra: python, docs              <!-- always considered, besides detected ones -->
- path: deploy/** -> config        <!-- path glob -> profile, overrides detection -->
- path: tools/** -> python

## Verification commands

<!-- Exact commands per profile. They replace that profile's defaults entirely. -->

### python
```
just check
```

### docs
```
uv run mkdocs build --strict
```

### config
```
yamllint -s deploy/
kubeconform -strict -summary deploy/
```

## Architecture rules

- DDD layering: src/app/{domain,use_cases,repositories,mappers,adapters,api}
- use_cases never import from adapters or api
- Never edit files under migrations/ that are already merged; add a new migration

## Git

- base: main                       <!-- default: the branch you are on at /task-new -->
- branch: task/<id>                <!-- naming pattern -->
- worktree: yes                    <!-- yes | no -->

## Finish

<!-- What /task-finish may do without asking. Default: local commit only. -->
- commit: yes
- push: ask
- pr: ask
- remove-worktree: ask
