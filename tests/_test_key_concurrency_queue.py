#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""key 级并发上限的「排队」语义（而不是立刻 429）的单元测试。

背景：`concurrency_limit` 是**单个 API Key** 的并发上限。原实现占满时立刻返回
False，调用方直接回 429 —— 而多进程客户端（Claude Code、DSH）在工具调用密集时
天然会瞬时超并发，用户看到的是「网关抽风」。本改动把「拒绝」换成**有界等待**：
最多等 `WB_CHAT_SLOT_WAIT` 秒（默认 30，与全局对话槽位同一个预算），等到才 429。

本套件钉五件事：
  1. limit=1 时第二个请求会**等**到第一个释放再成功，等待时长确实发生；
  2. 等待超预算时仍然返回 False（不会无限挂住），且 current/limit 如实返回；
  3. 归还与占用成对：没占用也能安全 release（不抛异常、不把计数压成负数）；
  4. limit ≤ 0 表示不限：永远放行，且不建信号量；
  5. 面板回显带 `concurrency_limit`（上游只存不显，导致面板保存时把真实值写回 0）。

运行：python3 tests/_test_key_concurrency_queue.py
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_proxy


def entry(limit, key_id="k-1"):
    return {"id": key_id, "concurrency_limit": limit, "key": "sk-test"}


class _Base(unittest.TestCase):
    def setUp(self):
        # 每个用例都要干净的表：信号量与计数器都是模块级全局
        wb_proxy._key_active_concurrency.clear()
        wb_proxy._key_semaphores.clear()
        self._old_wait = wb_proxy.CHAT_SLOT_WAIT_SECONDS
        wb_proxy.CHAT_SLOT_WAIT_SECONDS = 5.0

    def tearDown(self):
        wb_proxy._key_active_concurrency.clear()
        wb_proxy._key_semaphores.clear()
        wb_proxy.CHAT_SLOT_WAIT_SECONDS = self._old_wait


class TestQueueInsteadOfReject(_Base):
    def test_second_request_waits_then_succeeds(self):
        e = entry(1)
        ok, current, limit = wb_proxy._acquire_key_slot(e)
        self.assertTrue(ok)
        self.assertEqual((current, limit), (1, 1))

        result = {}

        def worker():
            t0 = time.time()
            result["ret"] = wb_proxy._acquire_key_slot(e)
            result["waited"] = time.time() - t0

        th = threading.Thread(target=worker)
        th.start()
        time.sleep(1.0)
        self.assertNotIn("ret", result, "占用未释放前第二个请求应该还在等")
        wb_proxy._release_key_slot(e)
        th.join(10)

        self.assertTrue(result["ret"][0], "释放后第二个请求必须拿到名额")
        self.assertGreaterEqual(result["waited"], 0.9, "应该真的等过，而不是瞬间拒绝")
        wb_proxy._release_key_slot(e)

    def test_timeout_still_rejects(self):
        wb_proxy.CHAT_SLOT_WAIT_SECONDS = 0.3
        e = entry(1)
        self.assertTrue(wb_proxy._acquire_key_slot(e)[0])
        t0 = time.time()
        ok, current, limit = wb_proxy._acquire_key_slot(e)
        waited = time.time() - t0
        self.assertFalse(ok, "等不到名额时仍要如实拒绝")
        self.assertEqual((current, limit), (1, 1))
        self.assertGreaterEqual(waited, 0.25)
        self.assertLess(waited, 3.0, "超预算后要立刻拒绝，不能挂住")
        wb_proxy._release_key_slot(e)

    def test_two_slots_queue_both(self):
        e = entry(2)
        self.assertTrue(wb_proxy._acquire_key_slot(e)[0])
        self.assertTrue(wb_proxy._acquire_key_slot(e)[0])
        wb_proxy._release_key_slot(e)
        ok, current, limit = wb_proxy._acquire_key_slot(e)
        self.assertTrue(ok)
        self.assertEqual((current, limit), (2, 2))


class TestReleaseSafety(_Base):
    def test_release_without_acquire_is_safe(self):
        e = entry(1)
        wb_proxy._acquire_key_slot(e)
        for _ in range(3):
            wb_proxy._release_key_slot(e)      # 多还几次不能抛异常
        self.assertNotIn("k-1", wb_proxy._key_active_concurrency)
        # 多还之后仍然应该能重新拿到限额内的名额
        self.assertTrue(wb_proxy._acquire_key_slot(e)[0])
        wb_proxy._release_key_slot(e)

    def test_no_state_left_behind_when_unlimited(self):
        e = entry(0)
        for _ in range(5):
            self.assertTrue(wb_proxy._acquire_key_slot(e)[0])
        wb_proxy._release_key_slot(e)
        self.assertEqual(wb_proxy._key_active_concurrency, {})
        self.assertEqual(wb_proxy._key_semaphores, {}, "limit=0 不该建信号量")


class TestLimitChange(_Base):
    def test_limit_change_rebuilds_semaphore(self):
        e = entry(1)
        self.assertTrue(wb_proxy._acquire_key_slot(e)[0])
        e["concurrency_limit"] = 3            # 面板上调
        ok, current, limit = wb_proxy._acquire_key_slot(e)
        self.assertTrue(ok, "上调后应该立刻多出名额")
        self.assertEqual(limit, 3)
        self.assertLessEqual(current, 3)

    def test_shrink_after_acquire_does_not_overflow(self):
        e = entry(3)
        self.assertTrue(wb_proxy._acquire_key_slot(e)[0])
        self.assertTrue(wb_proxy._acquire_key_slot(e)[0])
        e["concurrency_limit"] = 1            # 面板下调到比当前占用还小
        wb_proxy.CHAT_SLOT_WAIT_SECONDS = 0.2
        ok, current, limit = wb_proxy._acquire_key_slot(e)
        self.assertFalse(ok, "已超占时新请求应该等不到名额")
        self.assertEqual(limit, 1)
        wb_proxy._release_key_slot(e)
        wb_proxy._release_key_slot(e)


class TestPanelEcho(unittest.TestCase):
    def test_runtime_settings_view_echoes_concurrency_limit(self):
        import wb_settings
        view = wb_proxy.runtime_settings_view()
        self.assertIn("api_keys", view)
        for item in view.get("api_keys") or []:
            self.assertIn("concurrency_limit", item,
                          "面板要能读到并发上限，否则保存时会把真实值写回 0")
            self.assertIsInstance(item["concurrency_limit"], int)


if __name__ == "__main__":
    unittest.main(verbosity=2)
