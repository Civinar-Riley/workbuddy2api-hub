"""积分明细：把「签到用光的包」也拉回来，并用官方的累计口径做汇总。

线上现象（2026-11-12 实测，同一秒两个口径）：

    只查 Status [0]（有效）     → 5 条，求和 总容量 900 / 已用 502
    查 Status [0,1,2,3]        → 89 条（5 有效 + 84 已用完），求和 8863 / 8465
    resource-summary 累计      → 8863 / 8465.21（权威）

签到与成长计划每天各发一个 100 积分包，一被用完就变 `status=3`，只查 `[0]` 等于
把历史记录全部藏起来：「签到 28 天却看不到任何一条」「已用完/过期筛选永远是空的」。
另外对"还看得见的实例"求和会把总容量算小一个数量级（900 vs 8863），所以汇总必须
以 `resource-summary` 的 `Packages[]` 为准。

Run with: python tests/_test_credit_package_scope.py
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_startup_dir = tempfile.TemporaryDirectory(prefix="credit-scope-")
os.environ["ACCOUNTS_DIR"] = _startup_dir.name
os.environ["WB_PROXY_USAGE_DIR"] = _startup_dir.name
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_accounts


SUMMARY_CUMULATIVE = {
    "code": 0,
    "data": {
        "IsPaidUser": True,
        "Packages": [{
            "PackageCode": "P1",
            "CycleTotalCapacity": "8863",
            "CycleRemainCapacity": "397.79",
            "CycleUsedCapacity": "8465.21",
        }],
    },
}


def package_row(account_id, size, used, remain, status, end="2026-12-12 09:00:00"):
    return {
        "AccountId": account_id,
        "PackageCode": "P1",
        "PackageName": "CodeBuddy个人版国内运营裂变包",
        "CycleCapacitySize": size,
        "CycleCapacityUsed": used,
        "CycleCapacityRemain": remain,
        "CycleStartTime": "2026-11-12 09:00:00",
        "CycleEndTime": end,
        "DeductionEndTime": end,
        "Status": status,
    }


class FakeUpstream:
    """按 URL 分派的最小上游假件，同时记录每次请求的 body。"""

    def __init__(self, free_pages, paid_pages=None, summary=None):
        self.free_pages = free_pages
        self.paid_pages = paid_pages or []
        self.summary = summary if summary is not None else SUMMARY_CUMULATIVE
        self.free_bodies = []
        self.paid_bodies = []

    def __call__(self, url, **kwargs):
        raw = kwargs.get("data") or b"{}"
        body = json.loads(raw.decode("utf-8")) if raw else {}
        if url.endswith(wb_accounts.RESOURCE_SUMMARY_PATH):
            return self.summary
        if url.endswith(wb_accounts.RESOURCE_FREE_PACKAGES_PATH):
            self.free_bodies.append(body)
            page = int(body.get("PageNumber") or 1)
            rows = self.free_pages[page - 1] if page <= len(self.free_pages) else []
            return {"code": 0, "data": {"Accounts": rows, "TotalCount": self.total_free()}}
        if url.endswith(wb_accounts.RESOURCE_PAID_PACKAGES_PATH):
            self.paid_bodies.append(body)
            page = int(body.get("PageNumber") or 1)
            rows = self.paid_pages[page - 1] if page <= len(self.paid_pages) else []
            return {"code": 0, "data": {"Accounts": rows, "TotalCount": len(rows)}}
        if url.endswith(wb_accounts.CHECKIN_STATUS_PATH):
            return {"code": 0, "data": {}}
        return {"code": 0, "data": {}}

    def total_free(self):
        return sum(len(page) for page in self.free_pages)


def account():
    return wb_accounts.Account({"uid": "uid-credit", "accessToken": "token", "realm": "cn"})


def fetch(fake):
    acc = account()
    acc.headers = lambda purpose=None: {}
    with mock.patch.object(wb_accounts, "http_json", fake):
        return acc._fetch_credits_cn_detailed()


class PackageScopeTests(unittest.TestCase):
    def test_free_packages_ask_for_every_status(self):
        fake = FakeUpstream([[package_row(1, 100, 100, 0, 3)]])
        fetch(fake)
        self.assertTrue(fake.free_bodies)
        self.assertEqual(fake.free_bodies[0]["Status"], [0, 1, 2, 3])

    def test_used_up_packages_stay_visible(self):
        rows = [package_row(1, 100, 0, 100, 0), package_row(2, 100, 100, 0, 3),
                package_row(3, 1500, 1500, 0, 3)]
        result = fetch(FakeUpstream([rows]))
        self.assertTrue(result["ok"])
        statuses = sorted(p["status"] for p in result["credits"]["packages"])
        self.assertEqual(statuses, [0, 3, 3])

    def test_pages_are_followed_until_total_is_reached(self):
        page_size = wb_accounts.Account.PACKAGE_PAGE_SIZE
        page1 = [package_row(i, 100, 100, 0, 3) for i in range(page_size)]
        page2 = [package_row(1000 + i, 100, 100, 0, 3) for i in range(37)]
        fake = FakeUpstream([page1, page2])
        result = fetch(fake)
        self.assertEqual(len(result["credits"]["packages"]), page_size + 37)
        self.assertEqual([b["PageNumber"] for b in fake.free_bodies], [1, 2])

    def test_page_size_never_exceeds_the_upstream_cap(self):
        # 上游 PageSize 上限 200，传 500 直接 400
        self.assertEqual(wb_accounts.Account.PACKAGE_PAGE_SIZE, 200)
        fake = FakeUpstream([[package_row(1, 100, 0, 100, 0)]])
        fetch(fake)
        self.assertLessEqual(fake.free_bodies[0]["PageSize"], 200)

    def test_totals_come_from_the_summary_not_the_visible_list(self):
        # 列表里只有 900 积分，累计口径是 8863：汇总必须听 summary 的
        fake = FakeUpstream([[package_row(1, 500, 500, 0, 0), package_row(2, 400, 0, 400, 0)]])
        credits = fetch(fake)["credits"]
        self.assertEqual(credits["size"], 8863.0)
        self.assertEqual(credits["used"], 8465.21)
        self.assertEqual(credits["remain"], 397.79)

    def test_without_summary_it_falls_back_to_list_totals(self):
        # summary 只给了包代码、没给容量字段（少数账号如此）→ 退回按列表求和
        fake = FakeUpstream(
            [[package_row(1, 500, 500, 0, 0), package_row(2, 400, 100, 300, 0)]],
            summary={"code": 0, "data": {"IsPaidUser": True, "Packages": [{"PackageCode": "P1"}]}})
        credits = fetch(fake)["credits"]
        self.assertEqual(credits["size"], 900.0)
        self.assertEqual(credits["used"], 600.0)
        self.assertEqual(credits["remain"], 300.0)


class DashboardSourceTests(unittest.TestCase):
    def _source(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "dashboard.html")
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_exhausted_filter_understands_upstream_status(self):
        source = self._source()
        # 过滤条件必须认 status=3，否则「已用完/过期」永远是空的
        self.assertIn("is_exhausted: isExpired || pRemain <= 0 || Number(p.status) === 3", source)
        self.assertIn("if(!p.is_exhausted) return false;", source)
        self.assertIn("已用完", source)

    def test_metric_cards_describe_cumulative_numbers(self):
        source = self._source()
        self.assertIn("累计发放", source)
        self.assertIn("累计消耗", source)
        self.assertIn("'累计发放': 'Total granted'", source)


if __name__ == "__main__":
    unittest.main()
