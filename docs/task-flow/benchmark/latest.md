<!-- bench:page run=combined-100 date=2026-11-02 created=2026-11-02T14:03:45Z fingerprint=sha256:dbd92482d92322582311aadf791120363e2f61aa46aaa6ed9cae3850543aa91e profiles=mistral-large,mistral-small,glm-flash,deepseek-flash -->

# Benchmark: latest result

[All benchmark results](../benchmark.md)

## Setup

- run: combined-100, 2026-11-02
- cases: autopilot-plan-and-run-tasks (L), finish-prunes-task-folder (M), task-list (S)
- repeats: each case x profile ran 1 times; medians with min–max
- review: off
- profile `mistral-large`: model mistral-large-4-0, billing api, prices $0.7000/M input, $0.0700/M cached, $2.1000/M output
- profile `mistral-small`: model mistral-small-2603, billing api, prices $0.1500/M input, $0.0150/M cached, $0.6000/M output
- profile `glm-flash`: model zai-glm-5-3-flash, billing api, prices $0.1500/M input, $0.0300/M cached, $0.5000/M output
- profile `deepseek-flash`: model deepseek-v4-1-flash-1017, billing api, prices $0.3000/M input, $0.0070/M cached, $1.2000/M output
- harness version: 1
- fingerprint: `sha256:dbd92482d92322582311aadf791120363e2f61aa46aaa6ed9cae3850543aa91e`
- task-flow version: 0.12.0

![Benchmark run combined-100, all sizes: mistral-large: tokens in 1,433,672, cached 1,367,637, out 32,968, cost $0.2112; mistral-small: tokens in 11,666,879, cached 11,073,632, out 131,571, cost $0.3340; glm-flash: tokens in 6,202,722, cached 5,051,136, out 80,816, cost $0.3647; deepseek-flash: tokens in 1,225,506, cached 1,182,549, out 13,514, cost $0.0374.](latest.svg)

## S

| Profile | Model | Runs | Accepted | Rounds (median) | Hidden pass | Tokens per accepted (in / cached / out) | Cost per run median (min–max) | Cost per accepted |
|  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- | --- |
| mistral-large | mistral-large-4-0 | 1 | 1/1 (100%) | 1 | 1/1 | 518,138 / 478,208 / 18,325 | $0.0999 ($0.0999–$0.0999) | $0.0999 |
| mistral-small | mistral-small-2603 | 1 | 0/1 (0%) | 0 | 0/1 | n/a (0 accepted) | $0.1089 ($0.1089–$0.1089) | n/a (0 accepted) |
| glm-flash | zai-glm-5-3-flash | 1 | 1/1 (100%) | 1 | 1/1 | 1,716,338 / 1,066,752 / 36,766 | $0.1478 ($0.1478–$0.1478) | $0.1478 |
| deepseek-flash | deepseek-v4-1-flash-1017 | 1 | 1/1 (100%) | 1 | 1/1 | 584,038 / 558,464 / 7,890 | $0.0210 ($0.0210–$0.0210) | $0.0210 |

## M

| Profile | Model | Runs | Accepted | Rounds (median) | Hidden pass | Tokens per accepted (in / cached / out) | Cost per run median (min–max) | Cost per accepted |
|  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- | --- |
| mistral-large | mistral-large-4-0 | 1 | 1/1 (100%) | 1 | 1/1 | 669,263 / 621,824 / 21,209 | $0.1213 ($0.1213–$0.1213) | $0.1213 |
| mistral-small | mistral-small-2603 | 1 | 1/1 (100%) | 3 | 1/1 | 3,479,045 / 3,272,640 / 55,132 | $0.1131 ($0.1131–$0.1131) | $0.1131 |
| glm-flash | zai-glm-5-3-flash | 1 | 1/1 (100%) | 1 | 1/1 | 2,005,164 / 1,410,048 / 40,084 | $0.1516 ($0.1516–$0.1516) | $0.1516 |
| deepseek-flash | deepseek-v4-1-flash-1017 | 1 | 1/1 (100%) | 1 | 1/1 | 600,710 / 568,448 / 7,707 | $0.0229 ($0.0229–$0.0229) | $0.0229 |

## L

| Profile | Model | Runs | Accepted | Rounds (median) | Hidden pass | Tokens per accepted (in / cached / out) | Cost per run median (min–max) | Cost per accepted |
|  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- | --- |
| mistral-large | mistral-large-4-0 | 1 | 1/1 (100%) | 1 | 0/1 | 3,113,615 / 3,002,880 / 59,370 | $0.4124 ($0.4124–$0.4124) | $0.4124 |
| mistral-small | mistral-small-2603 | 1 | 0/1 (0%) | 0 | 0/1 | n/a (0 accepted) | $0.1120 ($0.1120–$0.1120) | n/a (0 accepted) |
| glm-flash | zai-glm-5-3-flash | 1 | 1/1 (100%) | 1 | 0/1 | 14,886,663 / 12,676,608 / 165,597 | $0.7946 ($0.7946–$0.7946) | $0.7946 |
| deepseek-flash | deepseek-v4-1-flash-1017 | 1 | 1/1 (100%) | 1 | 0/1 | 2,491,769 / 2,420,736 / 24,946 | $0.0682 ($0.0682–$0.0682) | $0.0682 |

## All

| Profile | Model | Runs | Accepted | Rounds (median) | Hidden pass | Tokens per accepted (in / cached / out) | Cost per run median (min–max) | Cost per accepted |
|  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- | --- |
| mistral-large | mistral-large-4-0 | 3 | 3/3 (100%) | 1 | 2/3 | 1,433,672 / 1,367,637 / 32,968 | $0.1213 ($0.0999–$0.4124) | $0.2112 |
| mistral-small | mistral-small-2603 | 3 | 1/3 (33%) | 3 | 1/3 | 11,666,879 / 11,073,632 / 131,571 | $0.1120 ($0.1089–$0.1131) | $0.3340 |
| glm-flash | zai-glm-5-3-flash | 3 | 3/3 (100%) | 1 | 2/3 | 6,202,722 / 5,051,136 / 80,816 | $0.1516 ($0.1478–$0.7946) | $0.3647 |
| deepseek-flash | deepseek-v4-1-flash-1017 | 3 | 3/3 (100%) | 1 | 2/3 | 1,225,506 / 1,182,549 / 13,514 | $0.0229 ($0.0210–$0.0682) | $0.0374 |

## Notes

- no reviewer ran, so review cost is 0 (`--review` was off)
- this run merges rows of the runs `first-pass-2`, `rerun-100-turns`, `rerun-small-task-list`; each row records its `source_run` in the saved results.jsonl
- profile `mistral-small` ran with different turn limits (2 row(s) at 100 turns, 1 row(s) at 60 turns); rows are not strictly like for like
- profile `glm-flash` ran with different turn limits (1 row(s) at 100 turns, 2 row(s) at 60 turns); rows are not strictly like for like
- profile `deepseek-flash` ran with different turn limits (1 row(s) at 100 turns, 2 row(s) at 60 turns); rows are not strictly like for like

## Failures

- n=1 case=autopilot-plan-and-run-tasks profile=mistral-large repeat=1 outcome=accepted hidden=fail — 
- n=2 case=autopilot-plan-and-run-tasks profile=mistral-small repeat=1 outcome=no_progress hidden=not_run — no progress: the failing set of round 2 equals round 1's (['1', '5', '6'])
- n=3 case=autopilot-plan-and-run-tasks profile=glm-flash repeat=1 outcome=accepted hidden=fail — 
- n=4 case=autopilot-plan-and-run-tasks profile=deepseek-flash repeat=1 outcome=accepted hidden=fail — 
- n=10 case=task-list profile=mistral-small repeat=1 outcome=no_progress hidden=not_run — no progress: the failing set of round 2 equals round 1's (['1', '3', '4'])
