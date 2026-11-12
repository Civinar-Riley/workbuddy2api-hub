"""一键配置的模型目录按出口取，而不是永远跟全局默认出口（无网络单元测试）。

症状：在面板「智能体 → 连接信息 → 默认模型」里选不到任何国际版模型。网关的
默认出口是 `cn` 时，即使把面板切到国际版视图、或把 API Key 绑成「固定国际版
出口」，下拉框里仍然只有国内版目录。

根因：`_agents_models()` 调 `fetch_models()` 时不传 realm，后者退回全局
`CURRENT_REALM`，于是 /agents 永远只描述一个出口。出口选择只在推理请求上生效
（`_request_realm`），一键配置这条链路完全没接。

这个套件锁住修复后的契约：

  - `wb_proxy._agents_models(realm)`  把 realm 透传给 fetch_models
  - `wb_proxy._agents_realm()`        payload > Key 绑定 > 全局出口
  - `GET /agents?realm=`              只认 intl / cn，其余一律回落
  - `POST /agents/apply`              补规格时用同一个出口的目录

    python tests/_test_agents_realm.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


class FakeHandler(P.Handler):
    """只覆盖判定出口所需的两个依赖，不碰 socket / 账号池。"""

    def __init__(self, key_realm=""):
        self._bound = key_realm

    def _key_realm(self):
        return self._bound


real_fetch = P.fetch_models
real_realm = P.CURRENT_REALM


def fake_fetch(realm=None):
    """按 realm 返回不同的目录，并记录调用方传了什么。"""
    seen_realms.append(realm)
    if realm == "intl":
        return [("gpt-6-astra", {"maxInputTokens": 200000})]
    if realm == "cn":
        return [("deepseek-v4-pro", {"maxInputTokens": 100000})]
    return [("both-sides", {"maxInputTokens": 64000})]


seen_realms = []

# ---------------------------------------------------------------------------
print()
print("[1] _agents_models 把 realm 透传给 fetch_models")
# ---------------------------------------------------------------------------

P.fetch_models = fake_fetch
try:
    seen_realms.clear()
    intl = P.Handler._agents_models(None, "intl")
    check("realm=intl 时 fetch_models 收到了 intl",
          seen_realms[-1] == "intl", seen_realms[-1])
    check("realm=intl 拿到的是国际版目录",
          [m["id"] for m in intl] == ["gpt-6-astra"], [m["id"] for m in intl])

    seen_realms.clear()
    cn = P.Handler._agents_models(None, "cn")
    check("realm=cn 时拿到的国内版目录",
          [m["id"] for m in cn] == ["deepseek-v4-pro"], [m["id"] for m in cn])

    seen_realms.clear()
    P.Handler._agents_models(None)
    check("不传 realm 时保持旧行为（交给 fetch_models 自己回退）",
          seen_realms[-1] is None, seen_realms[-1])
finally:
    P.fetch_models = real_fetch

# ---------------------------------------------------------------------------
print()
print("[2] _agents_realm：payload > Key 绑定 > 全局出口")
# ---------------------------------------------------------------------------

P.CURRENT_REALM = "cn"
try:
    h = FakeHandler(key_realm="")
    check("不传 payload、Key 未绑定 -> 全局出口 cn",
          P.Handler._agents_realm(h, None) == "cn",
          P.Handler._agents_realm(h, None))

    check("payload 显式带 intl -> intl",
          P.Handler._agents_realm(h, {"realm": "intl"}) == "intl",
          P.Handler._agents_realm(h, {"realm": "intl"}))

    check("payload 带大写 INTL 也认",
          P.Handler._agents_realm(h, {"realm": "INTL"}) == "intl",
          P.Handler._agents_realm(h, {"realm": "INTL"}))

    h_intl = FakeHandler(key_realm="intl")
    check("payload 没带、Key 绑 intl -> intl",
          P.Handler._agents_realm(h_intl, {}) == "intl",
          P.Handler._agents_realm(h_intl, {}))

    check("payload 优先于 Key 绑定",
          P.Handler._agents_realm(h_intl, {"realm": "cn"}) == "cn",
          P.Handler._agents_realm(h_intl, {"realm": "cn"}))

    check("payload 的未知值被忽略，落到 Key 绑定",
          P.Handler._agents_realm(h_intl, {"realm": "codearts"}) == "intl",
          P.Handler._agents_realm(h_intl, {"realm": "codearts"}))

    check("payload 是 None 时不炸",
          P.Handler._agents_realm(FakeHandler(key_realm="intl"), None) == "intl",
          P.Handler._agents_realm(FakeHandler(key_realm="intl"), None))

    # 全局出口被配成 codearts 这类非 intl/cn 的值：必须收敛到 intl，
    # 否则 fetch_models 会去查一个没有 tier / 目录的 realm。
    P.CURRENT_REALM = "codearts"
    h_none = FakeHandler(key_realm="")
    check("全局出口非 intl/cn 时收敛到 intl",
          P.Handler._agents_realm(h_none, None) == "intl",
          P.Handler._agents_realm(h_none, None))
finally:
    P.CURRENT_REALM = real_realm

# ---------------------------------------------------------------------------
print()
print("[3] 全局出口就是 intl 的老安装：行为不变")
# ---------------------------------------------------------------------------

P.CURRENT_REALM = "intl"
try:
    seen_realms.clear()

    class _H(P.Handler):
        def __init__(self):
            pass

        def _key_realm(self):
            return ""

    realm = P.Handler._agents_realm(_H(), None)
    check("未传 payload 时仍取全局出口 intl", realm == "intl", realm)
finally:
    P.CURRENT_REALM = real_realm

# ---------------------------------------------------------------------------
print()
print("[4] apply 补规格时用同一个出口的目录")
# ---------------------------------------------------------------------------

# _handle_agents_apply 里 specs 来自 self._agents_models(realm)。这里只钉住
# 「realm 是怎么算出来的」：apply 与 /agents 必须走同一个 helper，否则下拉框
# 列的是国际版、补规格却按国内版查，国际版模型会丢 context_window / 档位。
P.CURRENT_REALM = "cn"
try:
    h = FakeHandler(key_realm="")
    payload = {"realm": "intl", "models": [{"id": "gpt-6-astra"}]}
    check("apply 的 realm 与 GET /agents 同源",
          P.Handler._agents_realm(h, payload) == "intl",
          P.Handler._agents_realm(h, payload))
finally:
    P.CURRENT_REALM = real_realm

# ---------------------------------------------------------------------------
print()
print(f"通过 {PASS} / 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
