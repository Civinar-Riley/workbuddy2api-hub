"""Kimi historical reasoning mirror regression tests."""
import json
import os
import sys
import tempfile

_startup_dir = tempfile.TemporaryDirectory(prefix="kimi-reasoning-mirror-")
os.environ["ACCOUNTS_DIR"] = _startup_dir.name
os.environ["WB_PROXY_USAGE_DIR"] = _startup_dir.name
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_proxy as P

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, extra))


def assistant_messages(body):
    return [message for message in body.get("messages", [])
            if isinstance(message, dict) and message.get("role") == "assistant"]


print("[1] Kimi mirrors non-empty reasoning_content")
trace = " Kimi thought with leading and trailing spaces "
body = P.build_upstream_body({
    "model": "kimi-k2",
    "messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello", "reasoning_content": trace},
    ],
})
out_assistant = assistant_messages(body)[0]
check("reasoning is the original reasoning_content",
      out_assistant.get("reasoning") == trace, json.dumps(out_assistant, ensure_ascii=False))
check("reasoning is not a placeholder space", out_assistant.get("reasoning") != " ",
      repr(out_assistant.get("reasoning")))

print()
print("[1b] Prefixes only match the model family, not lookalikes")
for model in ("kimi-k2-sg", "space-bunny-sg"):
    body = P.build_upstream_body({
        "model": model,
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "reasoning_content": trace},
        ],
    })
    check("%s mirrors the family prefix" % model,
          assistant_messages(body)[0].get("reasoning") == trace,
          json.dumps(body, ensure_ascii=False))
for model in ("kimono", "glmish", "stepfather"):
    body = P.build_upstream_body({
        "model": model,
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "reasoning_content": trace},
        ],
    })
    check("%s is not a family prefix" % model,
          "reasoning" not in assistant_messages(body)[0],
          json.dumps(body, ensure_ascii=False))

print()
print("[2] Kimi mirrors after adjacent assistant repair")
tool_trace = "real reasoning after tool call"
body = P.build_upstream_body({
    "model": "kimi-k2",
    "messages": [
        {"role": "user", "content": "look"},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "look", "arguments": "{}"},
        }]},
        {"role": "assistant", "content": "done", "reasoning_content": tool_trace},
        {"role": "tool", "tool_call_id": "call_1", "name": "look", "content": "result"},
        {"role": "user", "content": "next"},
    ],
})
merged_assistants = assistant_messages(body)
check("adjacent assistants are merged", len(merged_assistants) == 1,
      json.dumps(body["messages"], ensure_ascii=False))
check("merged reasoning remains the original trace",
      merged_assistants[0].get("reasoning") == tool_trace,
      json.dumps(merged_assistants[0], ensure_ascii=False))

print()
print("[3] Kimi does not invent reasoning")
body = P.build_upstream_body({
    "model": "kimi-k2",
    "messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "one"},
        {"role": "assistant", "content": "two", "reasoning_content": "  "},
    ],
})
silent_assistants = assistant_messages(body)
check("all assistants stay without reasoning",
      all("reasoning" not in message for message in silent_assistants),
      json.dumps(silent_assistants, ensure_ascii=False))
check("whitespace reasoning_content is not mirrored",
      all(message.get("reasoning") != "  " for message in silent_assistants),
      json.dumps(silent_assistants, ensure_ascii=False))

print()
print("[4] DeepSeek zero-trace placeholder behavior remains")
body = P.build_upstream_body({
    "model": "deepseek-v4.1-flash",
    "thinking": {"type": "enabled"},
    "messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ],
})
deepseek_assistant = assistant_messages(body)[0]
check("DeepSeek keeps the single-space placeholder",
      deepseek_assistant.get("reasoning") == " ",
      json.dumps(deepseek_assistant, ensure_ascii=False))

print()
print("[5] Other models do not gain reasoning")
for model in ("hy4-preview", "gpt-5"):
    body = P.build_upstream_body({
        "model": model,
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "reasoning_content": trace},
        ],
    })
    check("%s does not gain reasoning" % model,
          "reasoning" not in assistant_messages(body)[0],
          json.dumps(body, ensure_ascii=False))

print()
print("[6] Kimi body does not gain DeepSeek thinking")
body = P.build_upstream_body({
    "model": "kimi-k2",
    "messages": [{"role": "user", "content": "hi"}],
})
check("top-level thinking is absent", "thinking" not in body, json.dumps(body))

print()
print("[7] Responses reasoning summary survives the chat path")
summary = " Responses summary with original spacing "
responses_payload = {
    "model": "kimi-k2",
    "input": [
        {"type": "reasoning", "summary": [
            {"type": "summary_text", "text": summary},
        ]},
        {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "answer"},
        ]},
        {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "next"},
        ]},
    ],
}
body = P.build_upstream_body(P.responses_to_chat(responses_payload))
responses_assistant = assistant_messages(body)[0]
check("Responses summary becomes the original Kimi reasoning",
      responses_assistant.get("reasoning") == summary,
      json.dumps(responses_assistant, ensure_ascii=False))

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
