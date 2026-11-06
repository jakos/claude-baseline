# Changelog

## 0.13.0 - 2026-11-06

### Features

- feat(bench): publish run time next to cost and quality (d0916b6)

- feat(bench): run agent command-line tools in the benchmark (2d16ab6)

- feat(bench): add a repeatable benchmark protocol with per-round hidden probes (e2d9d5e)

- feat(task-flow): run the backlog and reviewer roles through an external profile (0a685ad)

- feat(task-flow): choose the implementer per task by plan size (059a186)

- feat(task-flow): run the planner role through an external profile (4bd4156)

- feat(task-flow): add a generic cli driver to implement.py (cf66db3)

- feat(task-flow): add task epics to group, run and inspect related tasks (b483de9)

- feat(task-flow): add an opt-in finish record and commit trailers (003877c)

- feat(task-flow): publish notes for merged runs and mixed turn limits (4ee9185)

- feat(task-flow): add bench.py merge and fill the All table in report.md (36691a1)

### Fixes

- fix(task-flow): make two tests independent of the host (8672eda)

- fix(task-flow): make the doctor and cli driver tests independent of the host (89f9787)

- fix(task-flow): make the test suite portable across Windows, Linux and drives (5df5700)

### Other

- docs: state the AI-native SDLC purpose and generate the stage status view (341dada)

- ci: run the task-flow gates on GitHub Actions (bb99681)

- docs(task-flow): save the first benchmark runs (4881913)

## 0.12.0 - 2026-11-02

### Features

- feat(task-flow): add bench.py publish for the results page, chart and history (e4c2466)

- feat(task-flow): add the first benchmark cases, check-case and a results log (4c06ef3)

- feat(task-flow): add bench.py to compare implementer profiles on frozen cases (21d9d3d)

- feat(task-flow): add task-doctor, a read-only environment and configuration check (629f6f3)

- feat(task-flow): post-edit feedback and prompt extras for the external implementer (66b67ed)

- feat(task-flow): add an Anthropic Messages driver for the external implementer (15d77de)

- feat(task-flow): add gates.py to run plan verification commands without a model (c384f9a)

- feat(task-flow): run Windows .cmd and .bat tools from the external implementer (5f4ccb3)

- feat(task-flow): run existing tasks as a stack with task-autopilot --tasks (cab100f)

### Fixes

- fix(task-flow): accept scientific-notation costs in autopilot and tighten its docs and tests (e1dae6f)

### Other

- docs: rewrite the README as a short task-flow front page (221eb9d)

- docs: add Mermaid diagrams, a full setup guide and diagram checks (ade17a2)

- docs: move the detailed task-flow documentation into docs/ subpages (ded7c0a)

## 0.11.0 - 2026-11-01

### Features

- feat(task-flow): add /task-flow:task-release with a script-computed release (427c4e3)

- feat(task-flow): account Claude tokens and external USD separately in the autopilot (e710c0f)

- feat(task-flow): choose each Claude role's model from models.toml (4f76e6f)

- feat(task-flow): implement.py reports per-turn timing, a heartbeat, retries and a progress trail (ddf3387)

- feat(task-flow): route the implementer role to implement.py when models.toml names a profile (ea298c5)

- feat(task-flow): implement.py runs one implementer round on a non-Claude model (27c921e)

### Fixes

- fix(task-flow): implement.py stages only what the round produced (06d3269)

### Other

- test(task-flow): make test_child_env_is_allowlisted deterministic (b076e74)

- docs: refresh task-flow, root and getting-started docs for the release (bc1258e)

- refactor(task-flow): one set of denial rules in rules.py for guard and implement.py (7dc3087)
