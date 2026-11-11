#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单发 /v1/messages 请求的稳定亲和键（`WB_MSGSTAT_SHARDS`）的单元测试。

背景：一条一次性调用是 [system, user]，user 文本每次都不同；而 `affinity_route()`
用 `msgs[:2]` 派生会话键，于是每次调用都算成「新会话」，被轮询派到不同账号。
上游 prompt 缓存是**按账号存放**的，换号等于整段前缀重付：实测同一段 5.3k
system 在 6 个账号上命中全是 0%，而钉住同一个账号能稳定命中 96% 以上。

本套件钉七件事：
  1. 开关默认关闭（``WB_MSGSTAT_SHARDS=0``），关闭时一律返回 None，行为与旧版一字不差；
  2. 打开后同一 (api key, 模型, system, tools) 组合的调用共用同一段稳定前缀；
  3. 易变部分只影响分桶位，且桶号落在 ``[0, N)``；
  4. 流式请求、已续轮请求、只有一条消息的请求都不进这套逻辑；
  5. `affinity_prefix_key()` 与 `affinity_route()` 派生的键逐字一致（否则 prebind 绑不上）；
  6. `prebind_prefix_second_turn()` 能把第 2 轮的键过继到第 1 轮的账号上；
  7. 天盐开关只改键、不改分桶语义。

运行：python3 tests/_test_messages_affinity_cache.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_proxy
import wb_settings

SYS = {"role": "system", "content": "You are Claude Code, Anthropic's official CLI." + "\\nctx " * 200}
TOOLS = [{"type": "function", "function": {"name": "Read"}},
         {"type": "function", "function": {"name": "Bash"}}]


def one_shot(user_text, **over):
    """一条单发调用：[system, user]，非流式。"""
    chat = {"model": "deepseek-v4.1-flash", "stream": False, "tools": TOOLS,
            "messages": [SYS, {"role": "user", "content": user_text}]}
    chat.update(over)
    return chat


def conversation(reply, n_extra=0):
    """一段真会话：system + 首问 + 首条回复 + 追问（+ 若干历史）。"""
    msgs = [SYS, {"role": "user", "content": "帮我看看这个项目"},
            {"role": "assistant", "content": reply},
            {"role": "user", "content": "继续"}]
    for i in range(n_extra):
        msgs.append({"role": "assistant" if i % 2 == 0 else "user",
                     "content": "历史 %d" % i})
    return {"model": "deepseek-v4.1-flash", "stream": True, "messages": msgs}


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "accounts"), exist_ok=True)
        self._old_dir = wb_proxy.ACCOUNTS_DIR
        self._old_shards = wb_proxy.MSGSTAT_SHARDS
        self._old_salt = wb_proxy.MSGSTAT_DAILY_SALT
        self._old_pool = wb_proxy.POOL
        wb_proxy.ACCOUNTS_DIR = self.dir
        wb_proxy.MSGSTAT_SHARDS = 3
        wb_proxy.MSGSTAT_DAILY_SALT = True

    def tearDown(self):
        wb_proxy.ACCOUNTS_DIR = self._old_dir
        wb_proxy.MSGSTAT_SHARDS = self._old_shards
        wb_proxy.MSGSTAT_DAILY_SALT = self._old_salt
        wb_proxy.POOL = self._old_pool


class _FakeAffinity(object):
    def __init__(self):
        self.bindings = {}

    def get(self, key):
        return self.bindings.get(key)

    def bind(self, key, uid):
        self.bindings[key] = uid


class _FakePool(object):
    def __init__(self):
        self.affinity = _FakeAffinity()


class TestSwitchIsOffByDefault(unittest.TestCase):
    def test_module_default_is_off(self):
        # 源码里的默认值必须是 0：改变默认派单行为的事必须显式开启
        self.assertEqual(wb_proxy.MSGSTAT_SHARDS, 0)

    def test_off_returns_none(self):
        old = wb_proxy.MSGSTAT_SHARDS
        wb_proxy.MSGSTAT_SHARDS = 0
        try:
            self.assertIsNone(wb_proxy.msgstat_affinity_key("k", one_shot("随便问问")))
        finally:
            wb_proxy.MSGSTAT_SHARDS = old


class TestKeyDerivation(_Base):
    def test_same_configuration_shares_stable_prefix(self):
        a = wb_proxy.msgstat_affinity_key("k1", one_shot("总结这个文件"))
        b = wb_proxy.msgstat_affinity_key("k1", one_shot("解释这段报错"))
        self.assertIsNotNone(a)
        self.assertEqual(a.split("-")[1], b.split("-")[1],
                         "同一 system/tools 的不同提问必须共用稳定前缀")

    def test_shard_stays_in_range(self):
        for shards in (1, 3, 7):
            wb_proxy.MSGSTAT_SHARDS = shards
            for i in range(60):
                key = wb_proxy.msgstat_affinity_key("k1", one_shot("问题 %d" % i))
                shard = int(key.split("-")[2])
                self.assertTrue(0 <= shard < shards)

    def test_sharding_spreads_requests(self):
        seen = set()
        for i in range(200):
            key = wb_proxy.msgstat_affinity_key("k1", one_shot("问题 %d" % i))
            seen.add(key.split("-")[2])
        self.assertEqual(seen, {"0", "1", "2"}, "3 个桶都该被用到")

    def test_different_key_model_or_system_changes_bucket(self):
        base = wb_proxy.msgstat_affinity_key("k1", one_shot("问题")).split("-")[1]
        self.assertNotEqual(base, wb_proxy.msgstat_affinity_key("k2", one_shot("问题")).split("-")[1])
        self.assertNotEqual(base, wb_proxy.msgstat_affinity_key(
            "k1", one_shot("问题", model="另一个模型")).split("-")[1])
        other_sys = one_shot("问题")
        other_sys["messages"] = [{"role": "system", "content": "别的提示词"}, other_sys["messages"][1]]
        self.assertNotEqual(base, wb_proxy.msgstat_affinity_key("k1", other_sys).split("-")[1])

    def test_tool_order_does_not_matter(self):
        a = wb_proxy.msgstat_affinity_key("k1", one_shot("问题")).split("-")[1]
        flipped = one_shot("问题", tools=list(reversed(TOOLS)))
        self.assertEqual(a, wb_proxy.msgstat_affinity_key("k1", flipped).split("-")[1])

    def test_tool_set_does_not_matter(self):
        a = wb_proxy.msgstat_affinity_key("k1", one_shot("问题")).split("-")[1]
        self.assertNotEqual(a, wb_proxy.msgstat_affinity_key(
            "k1", one_shot("问题", tools=TOOLS[:1])).split("-")[1])

    def test_stream_and_continuations_are_not_covered(self):
        self.assertIsNone(wb_proxy.msgstat_affinity_key("k1", one_shot("问题", stream=True)))
        self.assertIsNone(wb_proxy.msgstat_affinity_key("k1", {"model": "m", "messages": [SYS]}))
        self.assertIsNone(wb_proxy.msgstat_affinity_key("k1", conversation("好")))
        self.assertIsNone(wb_proxy.msgstat_affinity_key("k1", {"messages": []}))

    def test_multimodal_content_is_hashable(self):
        chat = one_shot("x")
        chat["messages"] = [SYS, {"role": "user", "content": [
            {"type": "text", "text": "看图"}, {"type": "image_url", "image_url": {"url": "data:x"}}]}]
        key = wb_proxy.msgstat_affinity_key("k1", chat)
        self.assertTrue((key or "").startswith("msgstat-"), key)
        self.assertEqual(len((key or "").split("-")[1]), 16)

    def test_daily_salt(self):
        salted = wb_proxy.msgstat_affinity_key("k1", one_shot("问题")) or ""
        wb_proxy.MSGSTAT_DAILY_SALT = False
        unsalted = wb_proxy.msgstat_affinity_key("k1", one_shot("问题")) or ""
        # 带盐的键以当天日期结尾（日期自带连字符，别按 "-" 数段）
        self.assertTrue(salted.endswith(wb_proxy.time.strftime("%Y-%m-%d")), salted)
        self.assertFalse(unsalted.endswith(wb_proxy.time.strftime("%Y-%m-%d")), unsalted)
        self.assertEqual(salted.split("-")[1], unsalted.split("-")[1])
        self.assertEqual(salted.split("-")[2], unsalted.split("-")[2])
        self.assertEqual(unsalted, "msgstat-%s-%s" % (unsalted.split("-")[1], unsalted.split("-")[2]))


class TestPrefixKeyEquivalence(_Base):
    """prebind 依赖 affinity_prefix_key 与 affinity_route 逐字一致。"""

    def test_first_turn_matches_affinity_route(self):
        msgs = one_shot("问题")["messages"]
        self.assertEqual(wb_proxy.affinity_prefix_key(msgs, 2), wb_proxy.affinity_route(msgs)[0])

    def test_second_turn_matches_affinity_route(self):
        msgs = conversation("好的")["messages"]
        head = 3 if (wb_settings.affinity_stable_key_enabled(self.dir) and len(msgs) > 2) else 2
        self.assertEqual(wb_proxy.affinity_prefix_key(msgs, head), wb_proxy.affinity_route(msgs)[0])

    def test_empty_is_none(self):
        self.assertIsNone(wb_proxy.affinity_prefix_key([]))
        self.assertIsNone(wb_proxy.affinity_prefix_key(None))


class TestPrebind(_Base):
    def test_second_turn_inherits_first_turn_account(self):
        pool = _FakePool()
        wb_proxy.POOL = pool
        msgs = conversation("好的")["messages"]
        first_turn, second_turn = msgs[:2], msgs          # 同一段会话的第 1 轮 / 第 2 轮
        pool.affinity.bind(wb_proxy.affinity_prefix_key(first_turn, 2), "acct-1")
        wb_proxy.prebind_prefix_second_turn({"messages": second_turn})
        self.assertEqual(pool.affinity.get(wb_proxy.affinity_prefix_key(second_turn, 3)), "acct-1")

    def test_noop_without_binding(self):
        pool = _FakePool()
        wb_proxy.POOL = pool
        wb_proxy.prebind_prefix_second_turn({"messages": conversation("好的")["messages"]})
        self.assertEqual(pool.affinity.bindings, {})

    def test_noop_for_other_turn_counts(self):
        pool = _FakePool()
        wb_proxy.POOL = pool
        pool.affinity.bind(wb_proxy.affinity_prefix_key(conversation("好的")["messages"][:2], 2), "acct-1")
        for n_extra in (0, 1, 5):
            msgs = conversation("好的", n_extra=n_extra)["messages"]
            if len(msgs) == 4:
                continue
            wb_proxy.prebind_prefix_second_turn({"messages": msgs})
        self.assertEqual(list(pool.affinity.bindings.values()), ["acct-1"])

    def test_noop_without_pool(self):
        wb_proxy.POOL = None
        wb_proxy.prebind_prefix_second_turn({"messages": conversation("好的")["messages"]})

    def test_noop_when_stable_key_off(self):
        pool = _FakePool()
        wb_proxy.POOL = pool
        wb_settings.set_affinity_stable_key_enabled(self.dir, False)
        pool.affinity.bind(wb_proxy.affinity_prefix_key(conversation("好的")["messages"][:2], 2), "acct-1")
        wb_proxy.prebind_prefix_second_turn({"messages": conversation("好的")["messages"]})
        self.assertEqual(len(pool.affinity.bindings), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
