"""Regression tests for payload compatibility additions."""
import copy
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _isolated_dirs import isolated_data_dirs  # noqa: E402

_TMP = isolated_data_dirs("wb-payload-compat-")

import wb_proxy as P  # noqa: E402


TOOL_FINGERPRINTS = (
    ("claude", "You are Claude Code, Anthropic's official CLI for Claude. "
               "Use this tool carefully."),
    ("codex", "You are a coding agent running in the Codex CLI tool. "
              "Inspect the repository before editing."),
    ("dsh", "You are an AI agent, powered by DeepSeek Harness. "
            "Use the available tools."),
)


def tool(name, description, parameters=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters or {"type": "object", "properties": {}},
        },
    }


class ToolDescriptionSanitizationTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(
            os.environ, {"WB_PRESERVE_REQUEST_CONTRACT": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_normalize_tools_cleans_client_descriptions(self):
        for name, description in TOOL_FINGERPRINTS:
            with self.subTest(client=name):
                obj = {"tools": [tool("lookup", description)]}
                P.normalize_tools(obj)
                clean = obj["tools"][0]["function"]["description"]
                self.assertEqual(
                    clean, P.sanitize_text(description, identity=True))
                self.assertNotEqual(clean, description)
                self.assertFalse(P.has_fingerprint(clean, identity=True))

    def test_build_upstream_body_cleans_client_descriptions(self):
        tools = [tool("lookup_%d" % index, description)
                 for index, (_name, description) in enumerate(TOOL_FINGERPRINTS)]
        body = P.build_upstream_body({
            "model": "deepseek-v4.1-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": tools,
        })
        for index, (_name, description) in enumerate(TOOL_FINGERPRINTS):
            clean = body["tools"][index]["function"]["description"]
            self.assertEqual(
                clean, P.sanitize_text(description, identity=True))
            self.assertFalse(P.has_fingerprint(clean, identity=True))

    def test_preserve_contract_keeps_descriptions_exact(self):
        description = TOOL_FINGERPRINTS[0][1]
        with mock.patch.dict(
                os.environ, {"WB_PRESERVE_REQUEST_CONTRACT": "1"}):
            obj = {"tools": [tool("lookup", description)]}
            P.normalize_tools(obj)
            self.assertEqual(
                obj["tools"][0]["function"]["description"], description)

            body = P.build_upstream_body({
                "model": "deepseek-v4.1-flash",
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [tool("lookup", description)],
            })
            self.assertEqual(
                body["tools"][0]["function"]["description"], description)


class ReasoningSanitizationTests(unittest.TestCase):
    FP = "Sisyphus-Junior - Focused executor from OhMyOpenCode"
    CLEAN = "Sisyphus-Junior - Focused executor"

    def test_sanitize_messages_cleans_reasoning_and_reasoning_content(self):
        messages = [{
            "role": "assistant",
            "content": "answer",
            "reasoning": "legacy " + self.FP,
            "reasoning_content": "current " + self.FP,
        }]
        out = P.sanitize_messages(messages)
        self.assertEqual(out[0]["reasoning"], "legacy " + self.CLEAN)
        self.assertEqual(
            out[0]["reasoning_content"], "current " + self.CLEAN)

    def test_backfill_uses_cleaned_reasoning_on_wire(self):
        body = P.build_upstream_body({
            "model": "deepseek-v4.1-flash",
            "messages": [
                {"role": "user", "content": "question"},
                {
                    "role": "assistant",
                    "content": "answer",
                    "reasoning": "legacy " + self.FP,
                },
                {"role": "user", "content": "follow-up"},
            ],
        })
        assistant = next(
            message for message in body["messages"]
            if message.get("role") == "assistant")
        self.assertEqual(assistant["reasoning"], "legacy " + self.CLEAN)
        self.assertEqual(
            assistant["reasoning_content"], "legacy " + self.CLEAN)
        self.assertNotIn("from OhMyOpenCode", assistant["reasoning_content"])


class KimiToolSchemaTests(unittest.TestCase):
    def build_tool(self, model, parameters):
        body = P.build_upstream_body({
            "model": model,
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [tool("lookup", "Lookup data.", copy.deepcopy(parameters))],
        })
        return P.inline_kimi_tool_schemas(body)

    def test_literal_data_is_not_treated_as_schema(self):
        parameters = {"type": "object", "properties": {"arg": {
            "type": "object", "default": {"$ref": "literal-data"}}}}
        schema = self.build_tool("kimi-k3", parameters)["tools"][0]["function"]["parameters"]
        self.assertEqual(schema["properties"]["arg"]["default"], {"$ref": "literal-data"})

    def test_nested_defs_refs_and_schema_metadata_are_inlined(self):
        parameters = {
            "type": "object",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {
                "Address": {
                    "type": "object",
                    "properties": {"zip": {"type": "string"}},
                },
                "User": {
                    "type": "object",
                    "properties": {
                        "address": {"$ref": "#/$defs/Address"},
                    },
                },
            },
            "properties": {
                "user": {"$ref": "#/$defs/User"},
            },
            "required": ["user"],
        }
        body = self.build_tool("cn:kimi-k2.5", parameters)
        schema = body["tools"][0]["function"]["parameters"]
        self.assertNotIn("$defs", schema)
        self.assertNotIn("$schema", schema)
        self.assertNotIn("$ref", schema["properties"]["user"])
        address = schema["properties"]["user"]["properties"]["address"]
        self.assertEqual(
            address["properties"]["zip"]["type"], "string")

    def test_definitions_pointer_escapes_and_kimi_k3(self):
        parameters = {
            "type": "object",
            "definitions": {
                "A~B/C": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                },
            },
            "properties": {
                "escaped": {"$ref": "#/definitions/A~0B~1C"},
            },
        }
        body = self.build_tool("kimi-k3", parameters)
        schema = body["tools"][0]["function"]["parameters"]
        self.assertNotIn("definitions", schema)
        self.assertEqual(
            schema["properties"]["escaped"]["properties"]["ok"]["type"],
            "boolean")

    def test_cycle_returns_empty_object_instead_of_looping(self):
        parameters = {
            "type": "object",
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "next": {"$ref": "#/$defs/Node"},
                    },
                },
            },
            "properties": {
                "root": {"$ref": "#/$defs/Node"},
            },
        }
        body = self.build_tool("kimi-k2.5", parameters)
        schema = body["tools"][0]["function"]["parameters"]
        node = schema["properties"]["root"]
        self.assertEqual(node["type"], "object")
        self.assertEqual(node["properties"]["next"], {})

    def test_missing_definition_becomes_object(self):
        parameters = {
            "type": "object",
            "properties": {
                "missing": {"$ref": "#/$defs/NotThere"},
            },
        }
        body = self.build_tool("kimi-k2.5", parameters)
        missing = body["tools"][0]["function"]["parameters"]["properties"]["missing"]
        self.assertEqual(missing, {"type": "object", "properties": {}})

    def test_non_kimi_schema_is_untouched(self):
        parameters = {
            "type": "object",
            "$defs": {
                "Value": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            },
            "properties": {
                "value": {"$ref": "#/$defs/Value"},
            },
        }
        body = self.build_tool("deepseek-v4.1-flash", parameters)
        schema = body["tools"][0]["function"]["parameters"]
        self.assertIn("$defs", schema)
        self.assertEqual(
            schema["properties"]["value"], {"$ref": "#/$defs/Value"})

    def test_node_budget_exhaustion_is_observable(self):
        budget = [1]
        result = P._inline_kimi_schema_node(
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
            {},
            budget=budget,
        )
        self.assertEqual(result, {})
        self.assertLess(budget[0], 0)


class OutputBudgetTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(
            os.environ, {"WB_MIN_OUTPUT_BUDGET": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_floor_by_effort_and_model_cap(self):
        body = {
            "max_tokens": 128,
            "reasoning_effort": "high",
            "thinking": {"type": "enabled"},
        }
        with mock.patch.object(
                P, "model_default_max_output_tokens", return_value=32000):
            P.ensure_min_output_budget(body, "deepseek-v4.1-flash")
        self.assertEqual(body["max_tokens"], P.MIN_BUDGET_BY_EFFORT["high"])

        capped = {
            "max_tokens": 128,
            "reasoning_effort": "xhigh",
            "thinking": {"type": "enabled"},
        }
        with mock.patch.object(
                P, "model_default_max_output_tokens", return_value=4096):
            P.ensure_min_output_budget(capped, "fixture")
        self.assertEqual(capped["max_tokens"], 4096)

    def test_probe_disabled_and_none_are_unchanged(self):
        cases = (
            {
                "max_tokens": 1,
                "reasoning_effort": "high",
                "thinking": {"type": "enabled"},
            },
            {
                "max_tokens": 128,
                "reasoning_effort": "high",
                "thinking": {"type": "disabled"},
            },
            {
                "max_tokens": 128,
                "reasoning_effort": "none",
                "thinking": {"type": "enabled"},
            },
        )
        for body in cases:
            with self.subTest(body=body), mock.patch.object(
                    P, "model_default_max_output_tokens", return_value=32000):
                before = dict(body)
                P.ensure_min_output_budget(body, "fixture")
                self.assertEqual(body, before)

    def test_build_upstream_body_applies_floor_only_when_enabled(self):
        payload = {
            "model": "deepseek-v4.1-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 128,
            "reasoning_effort": "high",
        }
        with mock.patch.object(
                P, "model_default_max_output_tokens", return_value=32000):
            disabled = P.build_upstream_body(copy.deepcopy(payload))
            self.assertEqual(disabled["max_tokens"], 128)
            with mock.patch.dict(
                    os.environ, {"WB_MIN_OUTPUT_BUDGET": "1"}):
                enabled = P.build_upstream_body(copy.deepcopy(payload))
        self.assertEqual(
            enabled["max_tokens"], P.MIN_BUDGET_BY_EFFORT["high"])


class StarvedCompletionTests(unittest.TestCase):
    def test_is_starved_completion(self):
        self.assertTrue(P.is_starved_completion("", [], "length"))
        self.assertTrue(P.is_starved_completion(" \n\t", None, " LENGTH "))
        self.assertFalse(P.is_starved_completion("text", [], "length"))
        self.assertFalse(
            P.is_starved_completion("", [{"id": "call_1"}], "length"))
        self.assertFalse(P.is_starved_completion("", [], "stop"))

    def test_build_starved_retry_body_disables_thinking(self):
        body = {
            "model": "deepseek-v4.1-flash",
            "max_tokens": 128,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "reasoningEffort": "high",
            "messages": [{"role": "user", "content": "hello"}],
        }
        before = copy.deepcopy(body)
        out, ok = P.build_starved_retry_body(body, floor=8192)
        self.assertTrue(ok)
        self.assertEqual(out["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", out)
        self.assertNotIn("reasoningEffort", out)
        self.assertEqual(out["max_tokens"], 8192)
        self.assertEqual(body, before)

    def test_build_starved_retry_body_rejects_probes_and_disabled(self):
        for body in (
                {"max_tokens": 1, "messages": []},
                {"max_tokens": 15, "messages": []},
                {"max_tokens": 128, "thinking": {"type": "disabled"},
                 "messages": []},
        ):
            with self.subTest(body=body):
                self.assertEqual(P.build_starved_retry_body(body), (None, False))

    def test_build_starved_retry_body_without_floor_keeps_budget(self):
        out, ok = P.build_starved_retry_body(
            {"max_tokens": 128, "messages": []})
        self.assertTrue(ok)
        self.assertEqual(out["max_tokens"], 128)
        self.assertEqual(out["thinking"], {"type": "disabled"})


if __name__ == "__main__":
    unittest.main()
