#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更稳的会话粘滞键（affinity_stable_key_enabled）的单元测试。

背景：会话键原本只用 [system, 首条 user]。两个开场白相同的会话算出同一个键、
共享同一个 SessionAffinity 绑定槽，于是每轮互相覆盖、轮流换账号，缓存每轮归零。
开关默认打开，键里多算一条「首条 assistant 回复」，两个会话从第 2 轮起分开。

这个套件钉三件事：
  1. 显式关着时与旧逻辑一字不差（键只由前两条消息决定）；
  2. 默认打开后同开场白的两个会话在第 2 轮之后算出不同的键；
  3. 同一段对话每一轮都算出同一个键（否则粘滞本身就不成立）。

运行：python3 tests/_test_affinity_stable_key.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_proxy
import wb_settings

SYS = {"role": "system", "content": "You are a helpful assistant."}
OPENING = {"role": "user", "content": "帮我看看这个项目"}


def turn(first_reply, follow_up="继续", n_extra=0):
    """一段对话：system + 首问 + 首条 assistant 回复 + 追问（+ 若干历史）。"""
    msgs = [SYS, OPENING, {"role": "assistant", "content": first_reply},
            {"role": "user", "content": follow_up}]
    for i in range(n_extra):
        msgs.append({"role": "assistant" if i % 2 == 0 else "user",
                     "content": "历史 %d" % i})
    return msgs


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "accounts"), exist_ok=True)
        self._old_dir = wb_proxy.ACCOUNTS_DIR
        wb_proxy.ACCOUNTS_DIR = self.dir

    def tearDown(self):
        wb_proxy.ACCOUNTS_DIR = self._old_dir


class TestSwitch(_Base):
    def test_default_is_on(self):
        self.assertTrue(wb_settings.affinity_stable_key_enabled(self.dir))

    def test_roundtrip(self):
        for value in (True, False, True):
            wb_settings.set_affinity_stable_key_enabled(self.dir, value)
            self.assertEqual(wb_settings.affinity_stable_key_enabled(self.dir), value)

    def test_only_json_true_enables(self):
        # 与 egress_backoff 同口径：字符串 "true" 之类不能算打开，且直接拒收
        # （bool("false") 是真值，静默转 bool 会把开关悄悄打开）。
        for bad in ("true", 1, "1", [1], {"a": 1}, 0, None):
            with self.assertRaises(ValueError):
                wb_settings.set_affinity_stable_key_enabled(self.dir, bad)

    def test_corrupt_value_reads_as_off(self):
        import json
        path = wb_settings.settings_path(self.dir)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"affinity_stable_key_enabled": "yes"}, fh)
        wb_settings._load_cache.update({"key": None, "value": {}})
        self.assertFalse(wb_settings.affinity_stable_key_enabled(self.dir))


class TestKeyDerivation(_Base):
    def test_on_separates_same_opening(self):
        a = wb_proxy.affinity_route(turn("好的，先看目录结构"))
        b = wb_proxy.affinity_route(turn("请问具体要看哪部分"))
        self.assertNotEqual(a[0], b[0], "首条回复不同就该是两个会话")

    def test_on_keeps_one_conversation_stable(self):
        first = wb_proxy.affinity_route(turn("好的，先看目录结构"))
        later = wb_proxy.affinity_route(turn("好的，先看目录结构", n_extra=40))
        self.assertEqual(first[0], later[0], "同一段对话每一轮必须算出同一个键")

    def test_on_differs_from_off(self):
        on = wb_proxy.affinity_route(turn("好的"))[0]
        wb_settings.set_affinity_stable_key_enabled(self.dir, False)
        off = wb_proxy.affinity_route(turn("好的"))[0]
        self.assertNotEqual(off, on)

    def test_on_falls_back_to_two_messages_on_first_turn(self):
        # 第 1 轮还没有 assistant 回复：只能退回前两条消息。两个同开场白的会话
        # 在这一轮仍会撞，这是已知且可接受的（从第 2 轮起分开）。
        a = wb_proxy.affinity_route([SYS, OPENING])[0]
        self.assertEqual(a, wb_proxy.affinity_route([SYS, OPENING])[0])
        self.assertIsNotNone(a)

    def test_on_still_distinguishes_different_openings(self):
        other = [SYS, {"role": "user", "content": "写一个快速排序"},
                 {"role": "assistant", "content": "好"}, {"role": "user", "content": "加注释"}]
        self.assertNotEqual(wb_proxy.affinity_route(turn("好的"))[0],
                            wb_proxy.affinity_route(other)[0])

    def test_switch_can_be_turned_back_off(self):
        on = wb_proxy.affinity_route(turn("好的"))[0]
        wb_settings.set_affinity_stable_key_enabled(self.dir, False)
        off = wb_proxy.affinity_route(turn("好的"))[0]
        self.assertNotEqual(on, off)
        self.assertEqual(off, wb_proxy.affinity_route([SYS, OPENING])[0])

    def test_empty_and_none_are_safe(self):
        self.assertEqual(wb_proxy.affinity_route(None), (None, None))
        self.assertEqual(wb_proxy.affinity_route([]), (None, None))


class TestPoolBinding(_Base):
    """开关打开后，两个同开场白的会话在池子里不再互相挤号。"""

    def _pool(self, count=8):
        import wb_accounts as A
        accounts = [A.Account({"uid": "u%02d" % i, "accessToken": "t",
                               "realm": "intl"},
                              os.path.join(self.dir, "u%02d.json" % i))
                    for i in range(count)]
        pool = A.AccountPool(self.dir)
        pool.accounts = accounts
        return pool

    def test_off_makes_two_conversations_swap_accounts(self):
        """共享键 + 两轮同时在飞：每轮互相把对方的账号抢走（这正是问题）。"""
        wb_settings.set_affinity_stable_key_enabled(self.dir, False)
        pool = self._pool()
        key = wb_proxy.affinity_route(turn("回复 A"))[0]
        self.assertEqual(key, wb_proxy.affinity_route(turn("回复 B"))[0])
        seq_a, seq_b = [], []
        for _ in range(4):
            # 两个会话这一轮同时在飞：第二个必须避开第一个刚占的账号。
            first = pool.pick_for_session(realm="intl", session_key=key)
            second = pool.pick_for_session(realm="intl", session_key=key,
                                           exclude={first.uid})
            seq_a.append(first.uid)
            seq_b.append(second.uid)
        # 每一轮都互换：A 上一轮的账号成了 B 这一轮的账号。
        self.assertEqual(seq_a[1:], seq_b[:-1], "共享键时每轮互换账号")
        self.assertGreater(len(set(seq_a)), 1, "共享键时每轮都会换号（这正是问题）")

    def test_on_keeps_each_conversation_on_its_own_account(self):
        pool = self._pool()
        key_a = wb_proxy.affinity_route(turn("回复 A"))[0]
        key_b = wb_proxy.affinity_route(turn("回复 B"))[0]
        self.assertNotEqual(key_a, key_b)
        seen_a, seen_b = [], []
        for _ in range(4):
            seen_a.append(pool.pick_for_session(realm="intl", session_key=key_a).uid)
            seen_b.append(pool.pick_for_session(realm="intl", session_key=key_b).uid)
        self.assertEqual(len(set(seen_a)), 1, "同一段对话必须钉住一个账号")
        self.assertEqual(len(set(seen_b)), 1, "同一段对话必须钉住一个账号")
        self.assertNotEqual(seen_a[0], seen_b[0], "两个会话应落在不同账号")


if __name__ == "__main__":
    unittest.main(verbosity=2)
