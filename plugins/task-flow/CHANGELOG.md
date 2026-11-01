# Changelog

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
