---
description: A "where does this code belong" question should pull in python-service.
tags: [skills, architecture]
max_turns: 8
allowed_tools: [Skill]
---

I'm adding an endpoint to my FastAPI app that charges a customer through a third-party payments API. I'm not sure where the retry logic and the timeout belong — in the router, or somewhere else? How should I lay this out?
