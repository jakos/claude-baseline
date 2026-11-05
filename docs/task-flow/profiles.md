# Profiles

Paths such as `scripts/…`, `skills/…`, `agents/…`, `templates/…` and `bench/…` are inside `plugins/task-flow/`.

| Profile | Detects |
| --- | --- |
| `profile-python` | `*.py`, `pyproject.toml`, `requirements*.txt` — thin: defers to `python-tooling`, and to `python-service` / `pytest-suite` when claude-python-service is installed |
| `profile-docs` | `*.md`, `*.rst`, `docs/`, `mkdocs.yml`, ADRs |
| `profile-config` | YAML manifests, Helm, kustomize, compose, `Jenkinsfile`, `Dockerfile`, `.env*`, CI files |
| `profile-generic` | everything else — discovers conventions and commands from the repo |
| `workflow` | not a profile: the shared contract — layout, formats, round limit |

Profiles are skills marked `disable-model-invocation` and `user-invocable: false`: they
never load in an ordinary session, never compete with `python-tooling` for triggers, and
cost no context. Agents read them by path.

## Adding a profile

1. Create `skills/profile-<name>/SKILL.md` with the same frontmatter as the others and
   the four sections: **Detect**, **Conventions**, **Verify**, **Review checklist**.
2. Add its path to the Profiles list in `skills/workflow/SKILL.md`, and its name to the
   allowed values under `plan.md → Profiles`.
3. Run one `task-run` on a scratch repo with a file it should detect, and check that
   `plan.md → Profiles` names it.

Separate from these detection profiles, a workflow *stage* can also be delegated to an
external model profile from `models.toml` — today the planner, via
`[roles] planner = "<profile>"` or a one-run `--planner <profile>` — see
[External planner](external-implementer.md#external-planner-a-producer-role) and
[Configuration](configuration.md#external-planner).

## Implementer profiles by plan size

Detection profiles above remain separate from model profiles. The optional
`roles.implementer` routing table selects enabled external profiles using four plan
counts and inclusive S/M bounds, with L for overflow and `default` for unmapped bands.
See [Configuration](configuration.md#deterministic-implementer-routing) for both TOML
alternatives and strict validation. No thresholds are enabled or recommended by default.
Profile-specific limits and fallback chains remain unchanged; routing never promotes
a model because a round failed.
