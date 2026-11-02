# Benchmark report: combined-100

- fingerprint: `sha256:dbd92482d92322582311aadf791120363e2f61aa46aaa6ed9cae3850543aa91e`
- harness version: 1
- repeat: 1
- review: off
- cases: autopilot-plan-and-run-tasks, finish-prunes-task-folder, task-list

## S

| Profile | Billing | Runs | Accepted | Round 1 | Within 3 | Rounds (median) | Hidden pass | Out of scope | Tokens in / cached / out (median per run) | Cost per run median (min–max) | Cost per accepted | Seconds median (min–max) |
|  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- | --- |
| mistral-large | api | 1 | 1 | 1 | 1 | 1 | 1/1 | 0 | 518138 / 478208 / 18325 | $0.0999 ($0.0999–$0.0999) | $0.0999 | 191.4s (191.4s–191.4s) |
| mistral-small | api | 1 | 0 | 0 | 0 | 2 | 0/1 | 0 | 4086935 / 3888112 / 34585 | $0.1089 ($0.1089–$0.1089) | n/a (0 accepted) | 689.5s (689.5s–689.5s) |
| glm-flash | api | 1 | 1 | 1 | 1 | 1 | 1/1 | 0 | 1716338 / 1066752 / 36766 | $0.1478 ($0.1478–$0.1478) | $0.1478 | 1323.4s (1323.4s–1323.4s) |
| deepseek-flash | api | 1 | 1 | 1 | 1 | 1 | 1/1 | 0 | 584038 / 558464 / 7890 | $0.0210 ($0.0210–$0.0210) | $0.0210 | 101.3s (101.3s–101.3s) |

## M

| Profile | Billing | Runs | Accepted | Round 1 | Within 3 | Rounds (median) | Hidden pass | Out of scope | Tokens in / cached / out (median per run) | Cost per run median (min–max) | Cost per accepted | Seconds median (min–max) |
|  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- | --- |
| mistral-large | api | 1 | 1 | 1 | 1 | 1 | 1/1 | 0 | 669263 / 621824 / 21209 | $0.1213 ($0.1213–$0.1213) | $0.1213 | 247.1s (247.1s–247.1s) |
| mistral-small | api | 1 | 1 | 0 | 1 | 3 | 1/1 | 0 | 3479045 / 3272640 / 55132 | $0.1131 ($0.1131–$0.1131) | $0.1131 | 621.3s (621.3s–621.3s) |
| glm-flash | api | 1 | 1 | 1 | 1 | 1 | 1/1 | 0 | 2005164 / 1410048 / 40084 | $0.1516 ($0.1516–$0.1516) | $0.1516 | 693.1s (693.1s–693.1s) |
| deepseek-flash | api | 1 | 1 | 1 | 1 | 1 | 1/1 | 0 | 600710 / 568448 / 7707 | $0.0229 ($0.0229–$0.0229) | $0.0229 | 107.2s (107.2s–107.2s) |

## L

| Profile | Billing | Runs | Accepted | Round 1 | Within 3 | Rounds (median) | Hidden pass | Out of scope | Tokens in / cached / out (median per run) | Cost per run median (min–max) | Cost per accepted | Seconds median (min–max) |
|  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- | --- |
| mistral-large | api | 1 | 1 | 1 | 1 | 1 | 0/1 | 0 | 3113615 / 3002880 / 59370 | $0.4124 ($0.4124–$0.4124) | $0.4124 | 660.7s (660.7s–660.7s) |
| mistral-small | api | 1 | 0 | 0 | 0 | 2 | 0/1 | 0 | 4100899 / 3912880 / 41854 | $0.1120 ($0.1120–$0.1120) | n/a (0 accepted) | 410.9s (410.9s–410.9s) |
| glm-flash | api | 1 | 1 | 1 | 1 | 1 | 0/1 | 0 | 14886663 / 12676608 / 165597 | $0.7946 ($0.7946–$0.7946) | $0.7946 | 2593.6s (2593.6s–2593.6s) |
| deepseek-flash | api | 1 | 1 | 1 | 1 | 1 | 0/1 | 0 | 2491769 / 2420736 / 24946 | $0.0682 ($0.0682–$0.0682) | $0.0682 | 325.3s (325.3s–325.3s) |

## All

| Profile | Billing | Runs | Accepted | Round 1 | Within 3 | Rounds (median) | Hidden pass | Out of scope | Tokens in / cached / out (median per run) | Cost per run median (min–max) | Cost per accepted | Seconds median (min–max) |
|  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- |  --- | --- |
| mistral-large | api | 3 | 3 | 3 | 3 | 1 | 2/3 | 0 | 669263 / 621824 / 21209 | $0.1213 ($0.0999–$0.4124) | $0.2112 | 247.1s (191.4s–660.7s) |
| mistral-small | api | 3 | 1 | 0 | 1 | 2 | 1/3 | 0 | 4086935 / 3888112 / 41854 | $0.1120 ($0.1089–$0.1131) | $0.3340 | 621.3s (410.9s–689.5s) |
| glm-flash | api | 3 | 3 | 3 | 3 | 1 | 2/3 | 0 | 2005164 / 1410048 / 40084 | $0.1516 ($0.1478–$0.7946) | $0.3647 | 1323.4s (693.1s–2593.6s) |
| deepseek-flash | api | 3 | 3 | 3 | 3 | 1 | 2/3 | 0 | 600710 / 568448 / 7890 | $0.0229 ($0.0210–$0.0682) | $0.0374 | 107.2s (101.3s–325.3s) |

## Notes
- review cost is 0 because no reviewer ran (`--review` was off)

## Failures
- n=1 case=autopilot-plan-and-run-tasks profile=mistral-large repeat=1 outcome=accepted hidden=fail — 
- n=2 case=autopilot-plan-and-run-tasks profile=mistral-small repeat=1 outcome=no_progress hidden=not_run — no progress: the failing set of round 2 equals round 1's (['1', '5', '6'])
- n=3 case=autopilot-plan-and-run-tasks profile=glm-flash repeat=1 outcome=accepted hidden=fail — 
- n=4 case=autopilot-plan-and-run-tasks profile=deepseek-flash repeat=1 outcome=accepted hidden=fail — 
- n=10 case=task-list profile=mistral-small repeat=1 outcome=no_progress hidden=not_run — no progress: the failing set of round 2 equals round 1's (['1', '3', '4'])
