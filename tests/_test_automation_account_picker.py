"""自动化页账号选择器：剩余积分与今日签到（wb_proxy + dashboard）。

`/tasks` 的 accounts[] 现在带 `credits_remain`（账号缓存里的剩余积分）与
`checkin_today` / `checkin_credit`（今天有没有领 Buddy 加油站那 100 积分）。
两者都读账号本地缓存，不打上游；面板把它们画成三列自绘下拉，顺带解决
原生 <select> 弹层宽度跟着输入框、长昵称被截断的问题。

Run with: python tests/_test_automation_account_picker.py
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_startup_dir = tempfile.TemporaryDirectory(prefix="acct-picker-")
os.environ["ACCOUNTS_DIR"] = _startup_dir.name
os.environ["WB_PROXY_USAGE_DIR"] = _startup_dir.name
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_accounts
import wb_proxy


def account(uid="uid-picker", **extra):
    data = {
        "uid": uid,
        "accessToken": "token-" + uid,
        "refreshToken": "",
        "expiresAt": int(time.time()) + 86400,
        "realm": "cn",
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


class Handler(wb_proxy.Handler):
    def __init__(self):
        self.captured = None

    def _authorized(self):
        return True

    def _json_panel(self, code, obj):
        self.captured = (code, obj)


def task(status="accepted", current=0, target=1):
    return {"task_code": "c", "status": status, "current": current, "target": target}


class CredentialsTests(unittest.TestCase):
    def test_credits_remain_reads_cached_value(self):
        self.assertEqual(wb_proxy.credits_remain_of(
            account(credits={"remain": 1234.5})), 1234.5)

    def test_credits_remain_is_none_when_absent_or_broken(self):
        self.assertIsNone(wb_proxy.credits_remain_of(account()))
        self.assertIsNone(wb_proxy.credits_remain_of(account(credits={"remain": "x"})))
        self.assertIsNone(wb_proxy.credits_remain_of(object()))


class CheckinTests(unittest.TestCase):
    def test_snapshot_says_checked_today(self):
        acc = account(credits={"checkin": {"today_checked_in": True, "today_credit": 100}})
        self.assertEqual(wb_proxy.checkin_today_of(acc),
                         {"checkin_today": True, "checkin_credit": 100})

    def test_snapshot_says_not_checked_today(self):
        acc = account(credits={"checkin": {"today_checked_in": False, "today_credit": 100}})
        self.assertEqual(wb_proxy.checkin_today_of(acc),
                         {"checkin_today": False, "checkin_credit": 100})

    def test_falls_back_to_last_checkin_date(self):
        today = time.strftime("%Y-%m-%d") + " 09:00:00"
        yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        # 本机记录是今天的 → 已签
        self.assertIs(wb_proxy.checkin_today_of(account(lastCheckin=today))["checkin_today"], True)
        # 只有昨天的记录、没有今天的证据 → 未知（None），不能当成「未签」也不能当成「已签」
        self.assertIsNone(wb_proxy.checkin_today_of(
            account(lastCheckin=yesterday + " 09:00:00"))["checkin_today"])
        self.assertIsNone(wb_proxy.checkin_today_of(account())["checkin_today"])

    def test_enterprise_accounts_show_not_applicable(self):
        acc = account()
        acc.uses_enterprise_billing = lambda: True
        self.assertIsNone(wb_proxy.checkin_today_of(acc)["checkin_today"])


class EndpointTests(unittest.TestCase):
    def test_tasks_accounts_carry_credits_and_checkin(self):
        acc = account(credits={"remain": 88.5,
                               "checkin": {"today_checked_in": True, "today_credit": 100}})
        handler = Handler()
        with mock.patch.object(wb_proxy, "POOL", FakePool([acc])), \
                mock.patch.object(wb_proxy, "growth_snapshot",
                                  lambda a: ([task("claimed")], {})):
            handler._get_tasks({})
        code, body = handler.captured
        self.assertEqual(code, 200)
        row = body["accounts"][0]
        self.assertEqual(row["uid"], "uid-picker")
        self.assertEqual(row["credits_remain"], 88.5)
        self.assertIs(row["checkin_today"], True)
        self.assertEqual(row["checkin_credit"], 100)


class DashboardTests(unittest.TestCase):
    def test_picker_markup_and_sources(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "dashboard.html")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        # 两处账号选择器都换成自绘面板（每日任务 / 一次性任务）
        self.assertIn('id="cnCheckinAcctTrigger"', source)
        self.assertIn('id="growthAcctTrigger"', source)
        self.assertIn('class="acct-menu"', source)
        # 四列：账号 / 剩余积分 / 任务 / 今日签到
        self.assertIn('<span>剩余积分</span>', source)
        self.assertIn('<span>任务</span>', source)
        self.assertIn('<span>今日签到</span>', source)
        self.assertIn(">已签<", source)
        self.assertIn(">未签<", source)
        # 面板打开时挂到 body：留在内容区里会被左侧 sticky 导航的层叠上下文盖住
        self.assertIn("document.body.appendChild(menu)", source)
        # 四边都夹进视口，且不越过左侧导航；纵向挑空间更大的一侧
        self.assertIn("function growthMenuMinLeft()", source)
        self.assertIn("maxRight - width", source)
        self.assertIn("const openUp = below < 240 && above > below;", source)
        # 原生下拉已不存在
        self.assertNotIn('id="growthAcctSelect"', source)
        self.assertNotIn('id="cnCheckinAcctSelect"', source)


if __name__ == "__main__":
    unittest.main()
