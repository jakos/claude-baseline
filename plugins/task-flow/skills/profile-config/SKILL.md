---
name: profile-config
description: task-flow profile for configuration and deployment changes — YAML, Kubernetes/OpenShift, Helm, compose, Jenkinsfile, Dockerfile, env and app config. Read by task-flow agents; not for general use.
disable-model-invocation: true
user-invocable: false
---

# Profile: config

## Detect

`*.yaml` / `*.yml` (Kubernetes and OpenShift manifests, Helm charts, kustomize overlays,
compose files, CI pipelines), `Chart.yaml`, `kustomization.yaml`, `Jenkinsfile`,
`Dockerfile` / `Containerfile`, `.env*`, app config in `*.toml` / `*.ini` / `*.json`,
`.github/workflows/`, `.gitlab-ci.yml`.

## Conventions

- Minimal diffs: change the key, not the formatting around it.
- Keep environments consistent: a change to one overlay (dev, test, prod) is checked
  against the others, and differences are deliberate and stated in the plan.
- No secrets in plain text — reference a Secret, a vault path or an env var.
- Pin image tags and tool versions; never `latest`.
- Workloads have resource requests and limits, and readiness and liveness probes.
- A new or renamed config key comes with the app code that reads it and the docs that
  describe it, in the same change.

## Verify

Use what exists. Nothing here may contact a cluster in a way that changes state:

```
yamllint -s <changed files>                      # if yamllint is available
kubeconform -strict -summary <manifests>         # offline schema check
kubectl apply --dry-run=client -f <manifest>     # client-side only, never server
helm lint <chart> && helm template <chart>       # for charts
kustomize build <overlay>                        # for overlays
hadolint Dockerfile                              # for Dockerfiles
```

Always possible: parse every changed YAML, JSON or TOML file with Python
(`uv run --with pyyaml python -c "import yaml,sys; [list(yaml.safe_load_all(open(f))) for f in sys.argv[1:]]" <files>`)
and check that the keys the app reads still exist.

## Review checklist

- Blast radius: which environments and services does this change reach?
- Backward compatibility: renamed or removed keys still read anywhere?
- Secrets exposure: anything sensitive in a value, a default, a log line or an image
  layer?
- Rollout and rollback: can it be reverted by reverting the commit? Migrations, immutable
  fields (selectors, PVC sizes) or ordering hazards?
- Matching changes in app code and docs for added or renamed keys.
