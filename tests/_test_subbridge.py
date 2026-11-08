"""Core conversion checks for the optional subscription bridge.

The original PR only asserted that the file shipped. These tests exercise the
parts that decide whether the generated mihomo config can actually work:
direct-proxy translation, HTTP subscription detection, duplicate naming, port
stickiness, and graceful missing-PyYAML behaviour.

Run with: python tests/_test_subbridge.py
"""

import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PASS = 0
FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  ok   %s" % label)
    else:
        FAIL += 1
        print("  FAIL %s%s" % (label, (" -> " + detail) if detail else ""))


def load_module():
    spec = importlib.util.spec_from_file_location(
        "wb_subbridge_test", os.path.join(ROOT, "wb_subbridge.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load_module()
work = tempfile.mkdtemp(prefix="wb-subbridge-test-")
bridge.BASE_DIR = work
bridge.SUBS_PATH = os.path.join(work, "subs.json")
bridge.SELECTION_PATH = os.path.join(work, "selection.json")
bridge.PORTMAP_PATH = os.path.join(work, "portmap.json")

try:
    # HTTP(S) subscription URLs must go to download(), not direct-proxy mode.
    check("https subscription is not a direct proxy",
          not bridge.is_direct_proxy_source("https://example.com/sub?token=x"))
    check("http subscription path is not a direct proxy",
          not bridge.is_direct_proxy_source("http://example.com/sub"))
    check("bare http endpoint is a direct proxy",
          bridge.is_direct_proxy_source("http://user:pass@127.0.0.1:8080"))
    check("socks5 URL is a direct proxy",
          bridge.is_direct_proxy_source("socks5://user:pass@127.0.0.1:1080"))
    check("unsupported socks4 URL is not silently treated as socks5",
          not bridge.is_direct_proxy_source("socks4://user:pass@127.0.0.1:1080"))

    direct = bridge.direct_proxy_to_mihomo(
        "socks5://alice:s%40cret@127.0.0.1:1080", "p:test")
    check("direct socks5 becomes a named mihomo proxy",
          direct["name"] == "p:test" and direct["type"] == "socks5"
          and direct["server"] == "127.0.0.1" and direct["port"] == 1080)
    check("direct proxy credentials are decoded",
          direct.get("username") == "alice" and direct.get("password") == "s@cret")

    selection = ["socks5://alice:s%40cret@127.0.0.1:1080"]
    config = bridge.build_config(selection, [], selection)
    check("listener references the proxy name, not the URL",
          config["listeners"][0]["proxy"] == config["proxies"][0]["name"])
    check("direct proxy is present in proxies",
          len(config["proxies"]) == 1 and config["proxies"][0]["type"] == "socks5")
    check("generated listener and proxy are different concepts",
          config["listeners"][0]["proxy"] != selection[0])

    # Re-render must keep the same port for the same selector.
    port1 = config["listeners"][0]["port"]
    config2 = bridge.build_config(selection, [], selection)
    check("listener port is sticky across renders",
          config2["listeners"][0]["port"] == port1)

    # Duplicate display names get deterministic suffixes without changing ids.
    proxies = [
        {"name": "same", "type": "ss", "server": "a.example", "port": 1,
         "cipher": "aes-128-gcm", "password": "x"},
        {"name": "same", "type": "ss", "server": "b.example", "port": 2,
         "cipher": "aes-128-gcm", "password": "y"},
    ]
    entries = bridge.selectable_nodes(proxies, [])
    check("duplicate names remain independently selectable",
          len(entries) == 2 and entries[0]["id"] != entries[1]["id"])
    cfg = bridge.build_config([entries[0]["id"], entries[1]["id"]], proxies, [])
    check("duplicate listener names are disambiguated",
          len({x["name"] for x in cfg["listeners"]}) == 2)

    # If yaml is unavailable, the failure is a JSON error rather than a crash.
    saved_yaml = bridge.yaml
    bridge.yaml = None
    old_import = __import__
    def blocked(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("blocked for test")
        return old_import(name, *args, **kwargs)
    import builtins
    builtins.__import__ = blocked
    try:
        try:
            bridge.load_yaml()
            check("missing PyYAML is reported", False)
        except ValueError as exc:
            check("missing PyYAML is reported", "PyYAML is required" in str(exc))
    finally:
        builtins.__import__ = old_import
        bridge.yaml = saved_yaml
finally:
    import shutil
    shutil.rmtree(work, ignore_errors=True)

print()
print("SUMMARY: PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
