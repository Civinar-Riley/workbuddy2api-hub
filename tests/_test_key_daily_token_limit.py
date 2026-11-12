"""Key 级日 token 限量：折叠口径、保存保留、403 拦截。

跑一个真实的网关进程（线程内）对着 mock 上游，把整条链路走一遍：
`/settings/save` 写入日限量 → 用量日志按 Key 折叠 → 超限时 403 →
把限量改回 0 立刻恢复。

专门钉住三个容易写错的点：
  1. **按 Key 折叠必须先于 uid 判空**：400 之类的失败行没有 `account` 字段，
     但它一定带着 `key`；先在 uid 上 `continue` 就会漏掉这批 token。
  2. **`/settings/save` 里请求体没带 `daily_token_limit` 时沿用已存值**，否则
     老面板一保存就把用户的日限量清成 0（与 #592 同类的坑）。
  3. **限量判断在并发槽位之前**：已经是超额状态的 Key 不该先排队等
     `WB_CHAT_SLOT_WAIT` 秒再被拒。

Run: python3 tests/_test_key_daily_token_limit.py
"""
import base64
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import _lifecycle as life  # noqa: E402

WORK = tempfile.mkdtemp(prefix="key-daily-limit-")
ACCOUNTS = os.path.join(WORK, "accounts")
os.makedirs(ACCOUNTS, exist_ok=True)
USAGE_DIR = os.path.join(WORK, "usage")
os.makedirs(USAGE_DIR, exist_ok=True)
os.environ["ACCOUNTS_DIR"] = ACCOUNTS
os.environ["WB_PROXY_USAGE_DIR"] = USAGE_DIR
os.environ["HOME"] = WORK
os.environ["USERPROFILE"] = WORK
os.environ["WB_ENABLE_ANTHROPIC_MESSAGES"] = "1"

KEY_ID = "k1"
KEY_NAME = "smoke-key"
KEY_VALUE = "sk-" + "a1b2c3d4" * 4


def jwt(uid="acct-daily-1"):
    def seg(obj):
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return seg({"alg": "none", "typ": "JWT"}) + "." + seg(
        {"uid": uid, "exp": int(time.time()) + 86400 * 30}) + ".x"


with open(os.path.join(ACCOUNTS, "acct1.json"), "w", encoding="utf-8") as fh:
    json.dump({
        "uid": "acct-daily-1",
        "accessToken": jwt(),
        "refreshToken": "",
        "expiresAt": int(time.time()) + 86400 * 30,
        "realm": "intl",
        "domain": "www.workbuddy.ai",
        "platform": "CLI",
        "product": "workbuddy",
        "enabled": True,
    }, fh)

# 用量日志：两行各 60 万 token。第一行故意不带 account（400 失败行的形状），
# 它只能靠 key 维度被算进来。
USAGE_LOG = os.path.join(USAGE_DIR, "usage.jsonl")
now = int(time.time())
with open(USAGE_LOG, "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"at": now - 60, "key": KEY_ID, "key_name": KEY_NAME,
                         "total_tokens": 600000, "model": "deepseek-v4.1-flash",
                         "outcome": "error", "status": 400}) + "\n")
    fh.write(json.dumps({"at": now - 30, "key": KEY_ID, "key_name": KEY_NAME,
                         "account": "acct-daily-1", "total_tokens": 600000,
                         "model": "deepseek-v4.1-flash", "credit": 1.5,
                         "outcome": "ok", "status": 200}) + "\n")

UPSTREAM_PORT = life.free_port()
MOCK = {"requests": []}


class MockUpstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, payload):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        MOCK["requests"].append({"path": self.path, "raw": raw})
        body = {"choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "收到"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
        self._send(json.dumps(body).encode("utf-8"))

    def do_GET(self):
        self._send(b'{"data":{}}')


upstream = ThreadingHTTPServer(("127.0.0.1", UPSTREAM_PORT), MockUpstream)
threading.Thread(target=upstream.serve_forever, daemon=True).start()

import wb_identity  # noqa: E402
import wb_settings  # noqa: E402
import wb_proxy as P  # noqa: E402

for _key, _val in list(wb_identity._ENDPOINTS.items()):
    wb_identity._ENDPOINTS[_key] = ("http://127.0.0.1:%d" % UPSTREAM_PORT, _val[1])

PANEL_PW = "panel-pw-for-tests"
wb_settings.set_api_keys(ACCOUNTS, [{
    "id": KEY_ID,
    "name": KEY_NAME,
    "key": KEY_VALUE,
    "concurrency_limit": 0,
    "daily_token_limit": 1,      # 1M/天，种子日志已经有 1.2M
    "enabled": True,
}])
wb_settings.set_panel_password(ACCOUNTS, PANEL_PW)

GATEWAY_PORT = life.free_port()


class Args(object):
    host = "127.0.0.1"
    port = GATEWAY_PORT
    api_key = None
    system_prompt = "You are a helpful assistant."
    user_agent = None
    usage_dir = USAGE_DIR
    accounts_dir = ACCOUNTS
    import_desktop = False
    lan = False
    panel_password = PANEL_PW
    info = None


P._apply_cli_overrides(Args())
P._bootstrap_runtime(Args())
gateway = ThreadingHTTPServer(("127.0.0.1", GATEWAY_PORT), P.Handler)
threading.Thread(target=gateway.serve_forever, daemon=True).start()

PASS = FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  [PASS] %s" % label)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (label, detail))


def request(method, path, body=None, headers=None, timeout=30):
    url = "http://127.0.0.1:%d%s" % (GATEWAY_PORT, path)
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def wait_ready():
    for _ in range(80):
        try:
            if request("GET", "/health", timeout=2)[0] == 200:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


AUTH = {"Authorization": "Bearer " + KEY_VALUE}


def main():
    check("网关启动并 /health 就绪", wait_ready())

    # ---- 折叠口径：没 account 的失败行也要按 key 计入 ----
    stats = P.daily_usage_stats(ttl=0)
    used = int((stats.get("keytokens") or {}).get(KEY_ID, 0) or 0)
    check("按 Key 折叠含无 account 的失败行（600000+600000）", used == 1200000, "used=%d" % used)
    check("同一批行仍按账号折叠", (stats.get("tokens") or {}).get("acct-daily-1") == 600000,
          "tokens=%r" % (stats.get("tokens"),))

    # ---- 超限：403，且不落到上游 ----
    before = len(MOCK["requests"])
    code, _h, body = request("POST", "/v1/chat/completions",
                             {"model": "deepseek-v4.1-flash",
                              "messages": [{"role": "user", "content": "hi"}]}, AUTH)
    check("超额时 /v1/chat/completions 返回 403", code == 403, "code=%s body=%r" % (code, body[:160]))
    check("403 文案点明日限量", b"Daily token quota exhausted" in body, repr(body[:200]))
    check("超额请求不会打到上游", len(MOCK["requests"]) == before,
          "upstream calls=%d" % (len(MOCK["requests"]) - before))

    code, _h, body = request("POST", "/v1/messages",
                             {"model": "deepseek-v4.1-flash", "max_tokens": 16,
                              "messages": [{"role": "user", "content": "hi"}]},
                             dict(AUTH, **{"anthropic-version": "2023-06-01",
                                           "x-api-key": KEY_VALUE}))
    check("/v1/messages 超额时也用 Anthropic 错误信封", code == 403 and b"permission_error" in body,
          "code=%s body=%r" % (code, body[:200]))

    # ---- 保存保留：请求体不带 daily_token_limit 时沿用已存值 ----
    code, _h, body = request("POST", "/panel/login", {"password": PANEL_PW})
    token = json.loads(body).get("token")
    check("面板登录成功", code == 200 and bool(token), "code=%s" % code)
    panel = {"X-Panel-Token": token}

    code, _h, body = request("GET", "/settings", headers=panel)
    cfg = json.loads(body)
    row = [k for k in (cfg.get("api_keys") or []) if k.get("id") == KEY_ID]
    check("面板回显 daily_token_limit=1", bool(row) and row[0].get("daily_token_limit") == 1,
          "row=%r" % (row[:1],))

    stripped = [{k: v for k, v in item.items() if k != "daily_token_limit"}
                for item in (cfg.get("api_keys") or [])]
    code, _h, body = request("POST", "/settings/save", {"api_keys": stripped}, headers=panel)
    check("不带该字段的保存成功", code == 200, "code=%s body=%r" % (code, body[:160]))
    code, _h, body = request("GET", "/settings", headers=panel)
    row = [k for k in (json.loads(body).get("api_keys") or []) if k.get("id") == KEY_ID]
    check("老面板保存不会把日限量清成 0",
          bool(row) and row[0].get("daily_token_limit") == 1,
          "row=%r" % (row[:1],))

    # ---- 额度放开后立刻恢复 ----
    raised = [dict(item, daily_token_limit=0) if item.get("id") == KEY_ID else item
              for item in (json.loads(body).get("api_keys") or [])]
    code, _h, body = request("POST", "/settings/save", {"api_keys": raised}, headers=panel)
    check("把日限量改回 0 保存成功", code == 200, "code=%s" % code)
    code, _h, body = request("POST", "/v1/chat/completions",
                             {"model": "deepseek-v4.1-flash",
                              "messages": [{"role": "user", "content": "hi"}]}, AUTH)
    check("限量放开后同一 Key 恢复正常", code == 200, "code=%s body=%r" % (code, body[:160]))
    check("正常请求确实打到了上游", len(MOCK["requests"]) > before)

    # ---- 保存侧归一：脏值 / 负数按 0（不限）处理 ----
    dirty = [dict(item, daily_token_limit=-5) if item.get("id") == KEY_ID else item
             for item in raised]
    request("POST", "/settings/save", {"api_keys": dirty}, headers=panel)
    code, _h, body = request("GET", "/settings", headers=panel)
    row = [k for k in (json.loads(body).get("api_keys") or []) if k.get("id") == KEY_ID]
    check("负数会被归一成 0", bool(row) and row[0].get("daily_token_limit") == 0,
          "row=%r" % (row[:1],))


try:
    main()
finally:
    try:
        gateway.shutdown()
        gateway.server_close()
    except Exception:
        pass
    try:
        upstream.shutdown()
        upstream.server_close()
    except Exception:
        pass
    for stop in ("SCHEDULER",):
        try:
            obj = getattr(P, stop, None)
            if obj is not None:
                obj.stop()
        except Exception:
            pass
    try:
        P.PRICING.stop()
    except Exception:
        pass

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
