---
description: Adding Python tooling to a non-Python repo should pull in python-tooling.
tags: [skills, tooling]
max_turns: 8
allowed_tools: [Skill]
---

My infrastructure repo is mostly Terraform, but it's grown a handful of Python helper scripts that share a couple of utility functions and need httpx and boto3. Right now everyone just pip installs whatever they need. I want this set up properly — dependencies, formatting, linting, and a git hook so nobody commits unformatted code. What should the setup look like?
