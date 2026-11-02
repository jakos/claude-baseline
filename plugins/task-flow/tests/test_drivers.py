# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline unit tests for scripts/drivers.py: the per-provider wire formats.

No network, no subprocess: the drivers are pure data mapping. Run:
uv run --script plugins/task-flow/tests/test_drivers.py
"""

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import drivers

TOOL_NAMES = [
    "read_file",
    "write_file",
    "edit_file",
    "list_files",
    "grep",
    "run_command",
    "blocked",
]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": f"The {name} tool.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    for name in TOOL_NAMES
]


def profile(**overrides):
    """A resolved profile dict with the keys the drivers read."""
    settings = {
        "driver": "openai-loop",
        "base_url": "https://api.example.com/v1",
        "model": "m",
        "temperature": None,
        "max_output_tokens": None,
        "max_tokens_param": "max_tokens",
        "extra_body": None,
    }
    settings.update(overrides)
    return settings


def markers(obj):
    """Every cache_control value in a nested body/conversation, in document order."""
    found = []

    def walk(value):
        if isinstance(value, dict):
            if "cache_control" in value:
                found.append(value["cache_control"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(obj)
    return found


class OpenAILoopTest(unittest.TestCase):
    def test_openai_body_matches_previous_shape(self):
        driver = drivers.get("openai-loop")
        p = profile(
            temperature=0.7,
            max_output_tokens=4096,
            extra_body={"reasoning_effort": "high"},
        )
        conversation = driver.start("SYS", "USER")
        body = driver.body(p, "SYS", conversation, TOOLS)
        self.assertEqual(
            body,
            {
                "model": "m",
                "messages": [
                    {"role": "system", "content": "SYS"},
                    {"role": "user", "content": "USER"},
                ],
                "tools": TOOLS,
                "tool_choice": "auto",
                "temperature": 0.7,
                "max_tokens": 4096,
                "reasoning_effort": "high",
            },
        )

    def test_openai_max_tokens_param_completion(self):
        driver = drivers.get("openai-loop")
        p = profile(max_tokens_param="max_completion_tokens", max_output_tokens=777)
        body = driver.body(p, "S", [], TOOLS)
        self.assertEqual(body["max_completion_tokens"], 777)
        self.assertNotIn("max_tokens", body)

    def test_openai_url_headers_read_usage_append(self):
        driver = drivers.get("openai-loop")
        p = profile()
        self.assertEqual(driver.url(p), "https://api.example.com/v1/chat/completions")
        self.assertEqual(
            driver.headers("k"),
            {"Content-Type": "application/json", "Authorization": "Bearer k"},
        )
        self.assertEqual(driver.headers(None), {"Content-Type": "application/json"})
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "hi",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "a"}',
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 40},
            },
        }
        reply = driver.read(payload)
        self.assertEqual(reply.text, "hi")
        self.assertEqual(reply.calls, [("c1", "read_file", '{"path": "a"}')])
        self.assertFalse(reply.refused)
        conversation = driver.start("S", "U")
        driver.append(conversation, reply, [("c1", "content")])
        self.assertEqual(
            conversation[2],
            {
                "role": "assistant",
                "content": "hi",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "a"}',
                        },
                    }
                ],
            },
        )
        self.assertEqual(
            conversation[3],
            {"role": "tool", "tool_call_id": "c1", "content": "content"},
        )
        self.assertEqual(
            driver.usage(payload),
            {"input": 100, "cached": 40, "cache_write": 0, "output": 5},
        )
        # cached tokens are capped at the prompt
        capped = driver.usage(
            {
                "usage": {
                    "prompt_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 50},
                    "completion_tokens": 1,
                }
            }
        )
        self.assertEqual(capped["cached"], 10)
        self.assertEqual(
            driver.usage("not a dict"),
            {"input": 0, "cached": 0, "cache_write": 0, "output": 0},
        )
        with self.assertRaises(drivers.Malformed):
            driver.read({"choices": []})
        with self.assertRaises(drivers.Malformed):
            driver.read(
                {
                    "choices": [
                        {"message": {"tool_calls": [{"function": {"name": "x"}}]}}
                    ]
                }
            )


class AnthropicLoopTest(unittest.TestCase):
    def anthropic(self, **overrides):
        overrides.setdefault("driver", "anthropic-loop")
        overrides.setdefault("base_url", "https://api.anthropic.com/v1")
        overrides.setdefault("model", "claude-sonnet-5-5")
        return profile(**overrides)

    def test_anthropic_body_shape(self):
        driver = drivers.get("anthropic-loop")
        p = self.anthropic(
            max_output_tokens=8000, extra_body={"output_config": {"effort": "medium"}}
        )
        conversation = driver.start("SYS", "USER")
        body = driver.body(p, "SYS", conversation, TOOLS)
        self.assertEqual(body["model"], "claude-sonnet-5-5")
        self.assertEqual(body["max_tokens"], 8000)
        self.assertEqual(
            body["system"],
            [
                {
                    "type": "text",
                    "text": "SYS",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        )
        self.assertEqual([tool["name"] for tool in body["tools"]], TOOL_NAMES)
        for tool, source in zip(body["tools"], TOOLS):
            self.assertEqual(tool["description"], source["function"]["description"])
            self.assertEqual(tool["input_schema"], source["function"]["parameters"])
        self.assertEqual(body["tool_choice"], {"type": "auto"})
        self.assertEqual(body["output_config"], {"effort": "medium"})
        for key in ("temperature", "top_p", "top_k"):
            self.assertNotIn(key, body)
        # the first user message is a one-element text block that carries the marker
        self.assertEqual(
            body["messages"],
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "USER",
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            ],
        )

    def test_anthropic_default_max_tokens(self):
        driver = drivers.get("anthropic-loop")
        body = driver.body(self.anthropic(), "S", driver.start("S", "U"), TOOLS)
        self.assertEqual(body["max_tokens"], 16000)
        self.assertEqual(drivers.ANTHROPIC_DEFAULT_MAX_TOKENS, 16000)

    def test_anthropic_headers_and_url(self):
        driver = drivers.get("anthropic-loop")
        p = self.anthropic()
        self.assertEqual(driver.url(p), "https://api.anthropic.com/v1/messages")
        headers = driver.headers("sk-ant")
        self.assertEqual(headers["x-api-key"], "sk-ant")
        self.assertEqual(headers["anthropic-version"], "2026-10-21")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(drivers.ANTHROPIC_VERSION, "2026-10-21")

    def test_anthropic_cache_breakpoints_two_and_moving(self):
        driver = drivers.get("anthropic-loop")
        p = self.anthropic()
        conversation = driver.start("SYS", "USER")
        reply1 = driver.read(
            {
                "content": [
                    {"type": "text", "text": "let me look"},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "read_file",
                        "input": {"path": "a"},
                    },
                ],
                "stop_reason": "tool_use",
            }
        )
        driver.append(conversation, reply1, [("t1", "file content")])
        reply2 = driver.read(
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "grep",
                        "input": {"pattern": "x"},
                    }
                ],
                "stop_reason": "tool_use",
            }
        )
        driver.append(conversation, reply2, [("t2", "matches")])
        body = driver.body(p, "SYS", conversation, TOOLS)
        # exactly two breakpoints: the system block and the last block of the last
        # message (now the newest tool_result user message)
        self.assertEqual(markers(body), [{"type": "ephemeral"}, {"type": "ephemeral"}])
        self.assertIn("cache_control", body["system"][0])
        last_message = body["messages"][-1]
        self.assertEqual(last_message["content"][-1]["type"], "tool_result")
        self.assertIn("cache_control", last_message["content"][-1])
        # the stored conversation never carries markers
        self.assertEqual(markers(conversation), [])

    def test_anthropic_reads_and_appends(self):
        driver = drivers.get("anthropic-loop")
        payload = {
            "content": [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": "I will read."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "read_file",
                    "input": {"path": "a"},
                },
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "grep",
                    "input": {"pattern": "x"},
                },
            ],
            "stop_reason": "tool_use",
        }
        reply = driver.read(payload)
        self.assertEqual(reply.text, "I will read.")
        self.assertEqual(
            reply.calls,
            [("t1", "read_file", {"path": "a"}), ("t2", "grep", {"pattern": "x"})],
        )
        self.assertFalse(reply.refused)
        conversation = driver.start("SYS", "USER")
        driver.append(conversation, reply, [("t1", "res1"), ("t2", "res2")])
        # the assistant content, including the thinking block, goes back unchanged
        self.assertEqual(conversation[1]["role"], "assistant")
        self.assertEqual(conversation[1]["content"], payload["content"])
        # all results of one turn in ONE user message of tool_result blocks, in order
        self.assertEqual(
            conversation[2],
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "res1"},
                    {"type": "tool_result", "tool_use_id": "t2", "content": "res2"},
                ],
            },
        )
        refused = driver.read(
            {"content": [{"type": "text", "text": "no"}], "stop_reason": "refusal"}
        )
        self.assertTrue(refused.refused)
        for bad in (
            "not a dict",
            {"content": "not a list"},
            {"content": [{"type": "tool_use", "name": "x"}]},
            {"content": [{"type": "tool_use", "id": 7}]},
        ):
            with self.subTest(bad=bad), self.assertRaises(drivers.Malformed):
                driver.read(bad)

    def test_anthropic_usage_mapping(self):
        driver = drivers.get("anthropic-loop")
        usage = driver.usage(
            {
                "usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 200,
                    "cache_read_input_tokens": 700,
                    "output_tokens": 50,
                }
            }
        )
        self.assertEqual(
            usage, {"input": 1000, "cached": 700, "cache_write": 200, "output": 50}
        )
        # null or absent cache fields map to 0
        partial = driver.usage(
            {
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": None,
                    "output_tokens": 3,
                }
            }
        )
        self.assertEqual(
            partial, {"input": 10, "cached": 0, "cache_write": 0, "output": 3}
        )
        self.assertEqual(
            driver.usage({}),
            {"input": 0, "cached": 0, "cache_write": 0, "output": 0},
        )
        self.assertEqual(
            driver.usage("not a dict"),
            {"input": 0, "cached": 0, "cache_write": 0, "output": 0},
        )


if __name__ == "__main__":
    unittest.main()
