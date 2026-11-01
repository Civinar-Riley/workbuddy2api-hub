"""Regression tests for tool function-name validation (upstream code 11133).

Reported symptom:
Clients that declare server-side web tools next to ordinary functions, e.g.

    "tools": [ ...7 function tools..., {"type": "web_search"} ]

got HTTP 400 back from the upstream:

    upstream 400: {"code":11133,"msg":"Invalid request parameters",
      extError":{"param":"tools[7].function.name",
                 "message":"the request parameters were rejected by the model provider"}}

Root cause: normalize_tools rewrote every entry whose type was not "function"
into {"type": "function"} -- discarding the original type and never building a
function object. The entry reached the upstream without function.name, and the
validator rejects the request by that exact JSON path.

The same path also forwarded names the upstream cannot accept:
  - characters outside [A-Za-z0-9_-] (dots, slashes, CJK, spaces)
  - empty or missing name
  - names longer than 64 characters

These tests verify:
  1. Server-side declarations without a function payload are dropped.
  2. Tools with missing / empty / illegal / over-long names are dropped.
  3. Legal names survive untouched (no renaming, so tool_call names emitted by
     the model keep matching what the client declared).
  4. Duplicate names collapse to a single entry.
  5. An all-dropped list removes the tools key entirely.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _isolated_dirs import isolated_data_dirs  # noqa: E402
_TMP = isolated_data_dirs("wb-tool-name-validation-")

import wb_proxy as P

PASS = FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + label)
    else:
        FAIL += 1
        print("  [FAIL] " + label + ("  " + str(extra) if extra else ""))


def names(obj):
    return [t["function"]["name"] for t in (obj.get("tools") or [])
            if isinstance(t, dict) and isinstance(t.get("function"), dict)]


def fn_tool(name):
    return {"type": "function",
            "function": {"name": name, "description": "d",
                         "parameters": {"type": "object", "properties": {}}}}


print()
print("[1] Server-side declaration without function payload is dropped")
obj = {"tools": [fn_tool("keep_me"), {"type": "web_search"}]}
P.normalize_tools(obj)
check("web_search declaration dropped", names(obj) == ["keep_me"], obj)
check("no entry left without function", all(
    isinstance(t.get("function"), dict) for t in obj["tools"]), obj)

print()
print("[2] Empty function object is dropped")
obj = {"tools": [fn_tool("keep_me"), {"type": "function"}]}
P.normalize_tools(obj)
check("bare function entry dropped", names(obj) == ["keep_me"], obj)

print()
print("[3] Illegal characters in name are dropped, not rewritten")
for bad in ("a.b.c", "mcp/server", "my tool", "search\uff0cnow"):
    obj = {"tools": [fn_tool("keep_me"), fn_tool(bad)]}
    P.normalize_tools(obj)
    check("name %r dropped" % bad, names(obj) == ["keep_me"], obj)

print()
print("[4] Empty and missing name are dropped")
obj = {"tools": [fn_tool("keep_me"), fn_tool("")]}
P.normalize_tools(obj)
check("empty name dropped", names(obj) == ["keep_me"], obj)

obj = {"tools": [fn_tool("keep_me"),
                 {"type": "function", "function": {"description": "d"}}]}
P.normalize_tools(obj)
check("missing name dropped", names(obj) == ["keep_me"], obj)

print()
print("[5] Over-long name is dropped")
obj = {"tools": [fn_tool("keep_me"), fn_tool("x" * 65)]}
P.normalize_tools(obj)
check("65-char name dropped", names(obj) == ["keep_me"], obj)

obj = {"tools": [fn_tool("x" * 64)]}
P.normalize_tools(obj)
check("64-char name kept", names(obj) == ["x" * 64], names(obj))

print()
print("[6] Legal names survive untouched")
obj = {"tools": [fn_tool("get_weather"), fn_tool("a-b_c9")]}
P.normalize_tools(obj)
check("legal names unchanged", names(obj) == ["get_weather", "a-b_c9"], names(obj))
check("parameters intact",
      obj["tools"][0]["function"]["parameters"]["type"] == "object", obj)

print()
print("[7] Duplicate names collapse to one entry")
obj = {"tools": [fn_tool("dup"), fn_tool("dup"), fn_tool("other")]}
P.normalize_tools(obj)
check("duplicates removed", names(obj) == ["dup", "other"], names(obj))

print()
print("[8] All-dropped list removes the tools key")
obj = {"tools": [{"type": "web_search"}, {"type": "web_fetch"}]}
P.normalize_tools(obj)
check("tools key removed", "tools" not in obj, obj)

obj = {"tools": [fn_tool("")]}
P.normalize_tools(obj)
check("single invalid entry removes tools key", "tools" not in obj, obj)

print()
print("[9] The reported client shape no longer leaks a bad entry")
tools = [fn_tool("tool_%d" % i) for i in range(7)]
tools.append({"type": "web_search"})
obj = {"tools": tools}
P.normalize_tools(obj)
check("7 valid tools kept", len(names(obj)) == 7, names(obj))
check("index 7 no longer present", len(obj["tools"]) == 7, obj)
check("last kept name is tool_6", names(obj)[-1] == "tool_6", names(obj))

print()
print("SUMMARY: PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)