"""任务进度的账号级缓存与面板下拉数据（wb_proxy / dashboard）。

面板「自动化」页的账号下拉要显示「剩余积分」和「任务 13/18」：
  - 剩余积分取账号本地缓存的 `credits.remain`，不打上游；
  - 任务进度按 uid 缓存，`GET /tasks` 命中缓存就带上，缺的由前端分批
    打 `GET /tasks/progress` 补齐（每批最多 6 个、3 并发）。

Run with: python tests/_test_task_progress.py
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_startup_dir = tempfile.TemporaryDirectory(prefix="task-progress-")
os.environ["ACCOUNTS_DIR"] = _startup_dir.name
os.environ["WB_PROXY_USAGE_DIR"] = _startup_dir.name
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_accounts
import wb_proxy
import wb_tasks


def account(uid, realm="cn", **extra):
    data = {
        "uid": uid,
        "accessToken": "token-" + uid,
        "refreshToken": "",
        "expiresAt": int(time.time()) + 86400,
        "realm": realm,
        "enabled": True,
    }
    data.update(extra)
    return wb_accounts.Account(data)


class FakePool:
    def __init__(self, accounts):
        self.accounts = accounts

    def task_accounts(self, realm=None):
        return [a for a in self.accounts
                if a.task_active() and (not realm or a.realm == realm)]

    def get(self, uid):
        for item in self.accounts:
            if item.uid == uid:
                return item
        return None


def task(status="accepted", current=0, target=1):
    return {"task_code": "c", "status": status,
            "current": current, "target": target}


class Handler(wb_proxy.Handler):
    def __init__(self):
        self.captured = None

    def _authorized(self):
        return True

    def _json_panel(self, code, obj):
        self.captured = (code, obj)

    def _error(self, code, message, err_type="server_error"):
        self.captured = (code, {"error": message})


class CountingTests(unittest.TestCase):
    def test_claimed_completed_and_met_all_count(self):
        tasks = [task("claimed", 1, 1), task("completed", 1, 1),
                 task("accepted", 3, 3), task("accepted", 1, 5)]
        self.assertEqual(wb_proxy.task_progress_counts(tasks), (3, 4))

    def test_missing_numbers_do_not_crash(self):
        tasks = [{"status": "accepted"},
                 {"status": "accepted", "current": "x", "target": 1},
                 {"status": "accepted", "current": 2, "target": 0}]
        self.assertEqual(wb_proxy.task_progress_counts(tasks), (0, 3))
        self.assertEqual(wb_proxy.task_progress_counts([]), (0, 0))
        self.assertEqual(wb_proxy.task_progress_counts(None), (0, 0))


class CacheTests(unittest.TestCase):
    def setUp(self):
        wb_proxy._tasks_progress_cache.clear()
        self.addCleanup(wb_proxy._tasks_progress_cache.clear)

    def test_entry_reads_back_ttl_expires_and_ignores_empty(self):
        wb_proxy.record_task_progress("uid-a", [task("claimed"), task("accepted")])
        entry = wb_proxy.task_progress_entry("uid-a")
        self.assertIsNotNone(entry)
        self.assertEqual((entry["done"], entry["total"]), (1, 2))

        # 上游失败会被 fetch_growth_tasks 吞成 []，不能覆盖已知进度。
        wb_proxy.record_task_progress("uid-a", [])
        self.assertEqual(wb_proxy.task_progress_entry("uid-a")["total"], 2)

        original = wb_proxy.TASK_PROGRESS_TTL
        wb_proxy.TASK_PROGRESS_TTL = 0.05
        self.addCleanup(setattr, wb_proxy, "TASK_PROGRESS_TTL", original)
        time.sleep(0.1)
        self.assertIsNone(wb_proxy.task_progress_entry("uid-a"))

    def test_invalidating_task_cache_clears_progress(self):
        wb_proxy.record_task_progress("uid-b", [task("claimed")])
        wb_proxy.invalidate_tasks_cache()
        self.assertIsNone(wb_proxy.task_progress_entry("uid-b"))


class PersistTests(unittest.TestCase):
    def test_progress_survives_restart_and_ignores_bad_file(self):
        with tempfile.TemporaryDirectory() as directory:
            original_dir = wb_proxy.USAGE_DIR
            self.addCleanup(setattr, wb_proxy, "USAGE_DIR", original_dir)
            self.addCleanup(wb_proxy._tasks_progress_cache.clear)
            self.addCleanup(setattr, wb_proxy, "_tasks_progress_loaded", True)
            self.addCleanup(setattr, wb_proxy, "_tasks_progress_last_payload", None)
            wb_proxy.USAGE_DIR = directory
            wb_proxy._tasks_progress_cache.clear()
            wb_proxy._tasks_progress_loaded = False
            wb_proxy._tasks_progress_last_payload = None

            wb_proxy.record_task_progress("uid-p", [task("claimed"), task("accepted")])
            self.assertTrue(wb_proxy.save_task_progress())
            path = os.path.join(directory, "task-progress.json")
            self.assertTrue(os.path.exists(path))

            # 模拟一次蓝绿发布：内存清空，进度从文件恢复，不再问上游。
            wb_proxy._tasks_progress_cache.clear()
            wb_proxy._tasks_progress_loaded = False
            entry = wb_proxy.task_progress_entry("uid-p")
            self.assertEqual((entry["done"], entry["total"]), (1, 2))

            # 坏文件一律当作没有缓存，不能让面板挂掉。
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            wb_proxy._tasks_progress_cache.clear()
            wb_proxy._tasks_progress_loaded = False
            self.assertIsNone(wb_proxy.task_progress_entry("uid-p"))


class EndpointTests(unittest.TestCase):
    def setUp(self):
        wb_proxy._tasks_progress_cache.clear()
        self.addCleanup(wb_proxy._tasks_progress_cache.clear)

    def test_tasks_lists_credits_and_cached_progress(self):
        acct = account("uid-c", credits={"remain": 1234.5, "size": 2000})
        wb_proxy.record_task_progress("uid-c", [task("claimed"), task("accepted")])
        handler = Handler()
        with mock.patch.object(wb_proxy, "POOL", FakePool([acct])), \
                mock.patch.object(wb_proxy, "growth_snapshot",
                                  lambda a: ([task("claimed"), task("accepted")], {})):
            handler._get_tasks({})
        code, body = handler.captured
        self.assertEqual(code, 200)
        row = body["accounts"][0]
        self.assertEqual(row["uid"], "uid-c")
        self.assertEqual(row["credits_remain"], 1234.5)
        self.assertEqual((row["tasks_done"], row["tasks_total"]), (1, 2))

    def test_progress_dedups_caps_and_skips_unknown(self):
        accounts = [account("uid-%d" % i) for i in range(8)]
        handler = Handler()
        uids = ["uid-0", "uid-0", "uid-1", "ghost"] + ["uid-%d" % i for i in range(2, 8)]
        with mock.patch.object(wb_proxy, "POOL", FakePool(accounts)), \
                mock.patch.object(wb_tasks, "fetch_growth_tasks",
                                  lambda a: [task("claimed")]):
            handler._get_tasks_progress({"uids": [",".join(uids)]})
        code, body = handler.captured
        self.assertEqual(code, 200)
        self.assertEqual(len(body["items"]), 6)
        self.assertNotIn("ghost", [i["uid"] for i in body["items"]])
        self.assertEqual(len({i["uid"] for i in body["items"]}), 6)

    def test_cached_accounts_do_not_hit_upstream(self):
        cached, fresh = account("uid-cached"), account("uid-fresh")
        wb_proxy.record_task_progress("uid-cached", [task("claimed"), task("claimed")])
        called = []

        def fake_fetch(item):
            called.append(item.uid)
            return [task("claimed"), task("accepted")]

        handler = Handler()
        with mock.patch.object(wb_proxy, "POOL", FakePool([cached, fresh])), \
                mock.patch.object(wb_tasks, "fetch_growth_tasks", fake_fetch):
            handler._get_tasks_progress({"uids": ["uid-cached,uid-fresh"]})
        code, body = handler.captured
        states = {i["uid"]: (i["state"], i["done"], i["total"]) for i in body["items"]}
        self.assertEqual(states["uid-cached"], ("cached", 2, 2))
        self.assertEqual(states["uid-fresh"], ("fetched", 1, 2))
        self.assertEqual(called, ["uid-fresh"])

    def test_upstream_failure_only_marks_that_account(self):
        def boom(item):
            raise RuntimeError("upstream said no")

        handler = Handler()
        with mock.patch.object(wb_proxy, "POOL", FakePool([account("uid-x")])), \
                mock.patch.object(wb_tasks, "fetch_growth_tasks", boom):
            handler._get_tasks_progress({"uids": ["uid-x"]})
        code, body = handler.captured
        self.assertEqual(code, 200)
        self.assertEqual(body["items"][0]["state"], "error")
        self.assertIsNone(wb_proxy.task_progress_entry("uid-x"))


class DashboardTests(unittest.TestCase):
    def test_dropdown_builds_credits_and_ratio(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "dashboard.html")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("/tasks/progress?uids=", source)
        # 进度落在面板的第四列（任务 x/y），补齐后只改那一格
        self.assertIn("function growthProgressText(uid)", source)
        self.assertIn('t-progress', source)
        self.assertIn("renderGrowthAccounts(accts)", source)


if __name__ == "__main__":
    unittest.main()
