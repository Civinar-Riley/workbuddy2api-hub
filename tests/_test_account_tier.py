"""账号档位：付费 / 免费来自计费摘要（#505）。

计费摘要（`/billing/meter/get-user-resource-summary`）是唯一可信的档位来源：
`modelTiers` 的 Pro Only 名单实测会误判（gpt-5.6-terra / -luna 在名单里却可用），
11158 只能证明「这个号不能用这个模型」，推不出「这个号是免费档」。

要钉住的行为：

  1. `IsPaidUser` / `SubscriptionPackageName` 解析成 `tier` 记录；没有
     `IsPaidUser` 的响应返回 None，绝不写成「免费」；
  2. 失败（401 / 非 0 code / 网络）保留旧值与时间戳，不写结论；
  3. 企业账号与风控限制中的账号跳过；
  4. 一天最多读一次；国内版复用详细积分路径已经打过的同一份摘要；
  5. 结论落盘，并且 `is_paid_user` 与 `credits.is_paid_user` 同步。

Run with the current interpreter (python tests/run_all.py tier).
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_accounts


def make_account(uid="t-1", realm="intl", **extra):
    data = {"uid": uid, "accessToken": "token-%s" % uid, "realm": realm}
    data.update(extra)
    return wb_accounts.Account(data)


class TierFromSummaryTests(unittest.TestCase):
    def test_full_payload(self):
        tier = wb_accounts.tier_from_summary({
            "IsPaidUser": True,
            "SubscriptionPackageName": "Pro",
            "SubscriptionPackageCode": "pkg_pro",
            "IsProtectedPriceUser": True,
            "ProTrialStatus": 2,
        })
        self.assertTrue(tier["paid"])
        self.assertEqual(tier["package"], "Pro")
        self.assertEqual(tier["package_code"], "pkg_pro")
        self.assertTrue(tier["protected_price"])
        self.assertEqual(tier["trial"], 2)
        self.assertEqual(tier["source"], "billing")
        self.assertGreater(tier["checked_at"], 0)

    def test_intl_omits_pro_trial(self):
        # 国际版的摘要没有 ProTrialStatus，不能因此判成失败。
        tier = wb_accounts.tier_from_summary({
            "IsPaidUser": False, "SubscriptionPackageName": "Free"})
        self.assertFalse(tier["paid"])
        self.assertEqual(tier["trial"], 0)
        self.assertEqual(tier["package"], "Free")

    def test_missing_is_paid_user_is_not_free(self):
        # 没有结论 ≠ 免费：失败或旧版上游都不能把付费号降成免费号。
        self.assertIsNone(wb_accounts.tier_from_summary(
            {"SubscriptionPackageName": "Free"}))
        self.assertIsNone(wb_accounts.tier_from_summary({}))
        self.assertIsNone(wb_accounts.tier_from_summary(None))

    def test_junk_trial_falls_back_to_zero(self):
        tier = wb_accounts.tier_from_summary(
            {"IsPaidUser": False, "ProTrialStatus": "x"})
        self.assertEqual(tier["trial"], 0)


class TierStateTests(unittest.TestCase):
    def test_unknown_tier_is_not_paid(self):
        account = make_account()
        self.assertIsNone(account.tier_paid())
        self.assertFalse(account.is_paid_user)

    def test_loaded_tier_wins_over_credits(self):
        account = make_account(
            tier={"paid": True, "checked_at": time.time()},
            credits={"is_paid_user": False, "remain": 10})
        self.assertTrue(account.is_paid_user)
        self.assertTrue(account.tier_paid())

    def test_credits_fallback_when_tier_absent(self):
        account = make_account(credits={"is_paid_user": True, "remain": 10})
        self.assertTrue(account.is_paid_user)

    def test_apply_tier_mirrors_into_credits(self):
        account = make_account(credits={"remain": 10})
        self.assertTrue(account.apply_tier(
            {"paid": True, "package": "Pro", "checked_at": time.time()}))
        self.assertTrue(account.is_paid_user)
        self.assertTrue(account.credits["is_paid_user"])
        self.assertEqual(account.public()["tier"]["package"], "Pro")

    def test_partial_answer_is_ignored(self):
        account = make_account(tier={"paid": True, "checked_at": time.time()})
        self.assertFalse(account.apply_tier({"package": "Pro"}))
        self.assertTrue(account.is_paid_user)

    def test_freshness_window(self):
        now = 10_000_000.0
        account = make_account(tier={"paid": True, "checked_at": now - 60})
        self.assertTrue(account.tier_is_fresh(now))
        account.tier["checked_at"] = now - wb_accounts.TIER_TTL_SECONDS - 1
        self.assertFalse(account.tier_is_fresh(now))
        account.tier = {"package": "Free"}
        self.assertFalse(account.tier_is_fresh(now))


class TierFetchTests(unittest.TestCase):
    def test_summary_is_parsed(self):
        account = make_account()
        reply = {"code": 0, "data": {"IsPaidUser": True,
                                      "SubscriptionPackageName": "Pro"}}
        with mock.patch.object(wb_accounts, "http_json", return_value=reply):
            tier = account._fetch_tier_summary()
        self.assertTrue(tier["paid"])
        self.assertEqual(tier["package"], "Pro")

    def test_non_zero_code_writes_nothing(self):
        account = make_account()
        with mock.patch.object(wb_accounts, "http_json",
                               return_value={"code": 11140, "msg": "illegal"}):
            self.assertIsNone(account._fetch_tier_summary())

    def test_network_error_writes_nothing(self):
        account = make_account()
        with mock.patch.object(wb_accounts, "http_json",
                               side_effect=OSError("boom")):
            self.assertIsNone(account._fetch_tier_summary())

    def test_enterprise_and_restricted_are_skipped(self):
        enterprise = make_account("t-ent", enterpriseId="e-1")
        restricted = make_account("t-res")
        restricted.account_banned = True
        boom = mock.patch.object(wb_accounts, "http_json",
                                 side_effect=AssertionError("must not call"))
        with boom:
            self.assertIsNone(enterprise._fetch_tier_summary())
            self.assertIsNone(restricted._fetch_tier_summary())

    def test_refresh_is_once_per_ttl(self):
        account = make_account()
        calls = []

        def fake(*args, **kwargs):
            calls.append(1)
            return {"code": 0, "data": {"IsPaidUser": True,
                                          "SubscriptionPackageName": "Pro"}}

        with mock.patch.object(wb_accounts, "http_json", side_effect=fake):
            self.assertIsNotNone(account._refresh_tier_if_due())
            self.assertIsNone(account._refresh_tier_if_due())
        self.assertEqual(len(calls), 1)

    def test_intl_balance_uses_the_tier(self):
        account = make_account("t-intl")
        account.apply_tier({"paid": True, "package": "Pro",
                            "checked_at": time.time()})
        empty = {"code": 0, "data": {"Response": {"Data": {"Accounts": []}}}}
        with mock.patch.object(wb_accounts, "http_json", return_value=empty):
            res = account._fetch_credits_fallback()
        self.assertTrue(res["ok"])
        self.assertTrue(res["credits"]["is_paid_user"])

    def test_fetch_credits_attaches_the_tier(self):
        account = make_account("t-fetch")
        with mock.patch.object(account, "_fetch_credits_raw",
                               return_value={"ok": True, "credits": {}}), \
                mock.patch.object(account, "_fetch_tier_summary",
                                  return_value={"paid": False, "package": "Free",
                                                "checked_at": time.time()}):
            res = account.fetch_credits()
        self.assertEqual(res["tier"]["package"], "Free")
        self.assertFalse(account.is_paid_user)

    def test_tier_round_trips_through_the_credential_file(self):
        with tempfile.TemporaryDirectory(prefix="tier-persist-") as directory:
            account = make_account("t-disk")
            account.apply_tier({"paid": True, "package": "Pro",
                                "checked_at": time.time()})
            account.save(directory)
            pool = wb_accounts.AccountPool(directory, log=lambda _m: None)
            loaded = {a.uid: a for a in pool.load()}
            self.assertEqual(loaded["t-disk"].tier["package"], "Pro")
            self.assertTrue(loaded["t-disk"].is_paid_user)

    def test_import_does_not_carry_the_tier(self):
        # 导入到另一台机器时丢掉上一台机器的结论，避免「看起来还是付费」。
        self.assertIn("tier", wb_accounts.VOLATILE_FIELDS)


if __name__ == "__main__":
    unittest.main()
