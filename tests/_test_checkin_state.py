"""签到判定：业务失败不许记成签到成功。

旧实现 `ok: (code == 0 or code == 10001)` 把两类回执判成成功：

- 200 + code 10001 + 「请求处理中，请勿重复操作」——明确没签成；
- 200 + code 0 + 「签到失败，已签到天数不足 7 天」——code 是 0，文案说失败。

并且它拿到 HTTP 200 就先写 `last_checkin`，于是当天 `can_checkin()` 恒为
False，9 点与 21 点两轮都不会重试，这次失败被永久放弃。
"""
import io
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_accounts


def _account(directory):
    account = wb_accounts.Account(
        {"uid": "checkinstate", "accessToken": "token", "realm": "cn"},
        path=os.path.join(directory, "checkinstate.json"))
    account.headers = lambda purpose=None: {}
    return account


def _http_error(code, body):
    return urllib.error.HTTPError("https://example.invalid/", code, "err", {},
                                  io.BytesIO(body.encode("utf-8")))


class CheckinStateTests(unittest.TestCase):
    """四类归因：checked / already / pending / failed。"""

    def test_state_table(self):
        cases = [
            (0, "success", "checked"),
            (0, "OK", "checked"),
            (0, "签到成功，距离 7 天奖励还不足", "checked"),   # 「不足」不能判失败
            (0, "签到失败，已签到天数不足 7 天", "failed"),     # code=0 但文案说失败
            (10001, "今天已签到", "already"),
            # 生产实测：重复签到时上游回的是 code=0 + 这句，旧实现判成 checked，
            # 面板于是弹「签到成功 (+100积分)」，其实一个积分都没加。
            (0, "今天已签到，请明天再来", "already"),
            (10001, "请求处理中，请勿重复操作", "pending"),
            (10001, "每人限领一次，您已领取过", "failed"),      # 补偿码，不是签到
            (11140, "restricted", "failed"),
        ]
        for code, msg, expected in cases:
            with self.subTest(code=code, msg=msg):
                self.assertEqual(wb_accounts.checkin_state(code, msg), expected)


class CheckinWriteGateTests(unittest.TestCase):
    """只有 checked / already 才写 last_checkin。"""

    def _run(self, payload=None, error=None):
        with tempfile.TemporaryDirectory(prefix="checkin-") as directory:
            account = _account(directory)
            account.last_checkin = ""
            patcher = mock.patch.object(wb_accounts, "http_json",
                                        return_value=payload if error is None else None)
            if error is not None:
                patcher = mock.patch.object(wb_accounts, "http_json", side_effect=error)
            with patcher:
                result = account._checkin_upstream()
            return result, account.last_checkin

    def test_a_real_success_is_recorded(self):
        result, last = self._run(payload={"code": 0, "msg": "success"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "checked")
        self.assertNotEqual(last, "")

    def test_an_already_checked_account_counts_as_done(self):
        result, last = self._run(payload={"code": 10001, "msg": "今天已签到"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "already")
        self.assertNotEqual(last, "")

    def test_a_pending_receipt_is_not_a_success(self):
        result, last = self._run(payload={"code": 10001, "msg": "请求处理中，请勿重复操作"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "pending")
        self.assertEqual(last, "")       # 不写盘，当天 9 点 / 21 点还能重试

    def test_already_keeps_todays_real_checkin_time(self):
        """今天早上定时器签的 09:00:31 不能被晚上手点那次覆盖成点击时刻。"""
        with tempfile.TemporaryDirectory(prefix="checkin-") as directory:
            account = _account(directory)
            account.last_checkin = time.strftime("%Y-%m-%d") + " 09:00:31"
            with mock.patch.object(wb_accounts, "http_json",
                                   return_value={"code": 0, "msg": "今天已签到，请明天再来"}):
                result = account._checkin_upstream()
            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "already")
            self.assertTrue(account.last_checkin.endswith("09:00:31"))

    def test_already_still_fills_a_missing_record(self):
        """本机没有当天记录时仍要补写，否则定时器当天还会再问一次上游。"""
        result, last = self._run(payload={"code": 0, "msg": "今天已签到，请明天再来"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "already")
        self.assertNotEqual(last, "")

    def test_code_zero_with_failure_text_is_not_a_success(self):
        result, last = self._run(payload={"code": 0, "msg": "签到失败，已签到天数不足 7 天"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "failed")
        self.assertEqual(last, "")

    def test_http_400_already_checked_is_done(self):
        result, last = self._run(error=_http_error(400, '{"code": 10001, "msg": "今天已签到"}'))
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "already")
        self.assertNotEqual(last, "")

    def test_http_400_pending_is_not_a_success(self):
        result, last = self._run(error=_http_error(400, '{"code": 10001, "msg": "请求处理中，请勿重复操作"}'))
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "pending")
        self.assertEqual(last, "")


if __name__ == "__main__":
    unittest.main()
