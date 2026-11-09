"""#851：账号表横向滚动后，账号名仍必须固定可见。

只断言 CSS 字符串不够：这次 bug 正是「右侧操作列 sticky、左侧账号列忘了
sticky」的布局关系错误。这里用真实 Chromium 计算滚动后的几何位置：
  - 1419px 视口、1073px 滚动容器，模拟 issue 里的部署环境；
  - 滚到最右后，版本列贴容器左边，账号列贴版本列右边；
  - 账号名仍在容器内、宽度大于 0；
  - cool-row 是 colspan 告警续行，不能被误钉成第一列；
  - 390px 移动端取消 sticky，且页面不横向溢出。

Playwright/Chromium 不可用时跳过（与 _test_tls_fingerprint 的可选依赖口径
一致）；CI 的测试矩阵仍会跑纯文本契约 _test_ui_layout_refinements.js。

Run with Python: python tests/_test_account_table_sticky.py
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "dashboard.html").read_text(encoding="utf-8")
STYLES = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", HTML, re.S))


def fixture():
    headers = [
        '<th style="text-align:center;width:76px">版本</th>',
        '<th style="text-align:left">账号</th>',
        '<th style="text-align:center;width:84px">状态</th>',
        '<th style="text-align:center;width:118px">今日活动</th>',
        '<th style="text-align:center;width:150px">出口</th>',
        '<th style="text-align:center;width:78px">权重</th>',
        '<th style="text-align:left;width:120px">积分</th>',
        '<th style="text-align:right;width:70px">请求</th>',
        '<th style="text-align:right;width:88px">总 token</th>',
        '<th style="text-align:right;width:80px">缓存</th>',
        '<th style="text-align:center;width:232px">操作</th>',
    ]
    cells = [
        '<td style="text-align:center;white-space:nowrap" data-label="版本">INTL</td>',
        '<td style="text-align:left" data-label="账号"><div class="acct-name-row">'
        '<span class="acct-name" title="test-account">test-account</span></div></td>',
        '<td style="text-align:center" data-label="状态">可用</td>',
        '<td style="text-align:center" data-label="今日活动">已完成</td>',
        '<td style="text-align:center" data-label="出口">默认</td>',
        '<td style="text-align:center;white-space:nowrap" data-label="权重">1</td>',
        '<td style="text-align:left;white-space:normal" data-label="积分">42</td>',
        '<td style="text-align:right" data-label="请求">10</td>',
        '<td style="text-align:right" data-label="总 token">1000</td>',
        '<td style="text-align:right" data-label="缓存">500</td>',
        '<td class="acct-actions" style="text-align:center;white-space:nowrap;vertical-align:middle">'
        '<div class="acct-actions-col"><div class="acct-actions-row">'
        '<button class="sec mini">停用</button><button class="sec mini">测试</button>'
        "</div></div></td>",
    ]
    rows = (
        '<tr class="has-alert">' + "".join(cells) + "</tr>"
        '<tr class="cool-row"><td colspan="11"><div class="cool-list">'
        '<span class="cool-pill">model-a · 撞线 12:00 · 恢复 18:00（时间为本地时间）</span>'
        "</div></td></tr>"
        "<tr>" + "".join(cells) + "</tr>"
    )
    return (
        '<!doctype html><html data-ui="next"><head><meta charset="utf-8">'
        f"<style>{STYLES}main{{max-width:none!important;padding:0!important}}"
        "#wrap{margin-left:0!important}</style></head><body><main>"
        '<div id="wrap" class="table-wrap" style="width:1073px;margin-left:281px">'
        '<table class="data-cards account-cards"><thead><tr>'
        + "".join(headers)
        + "</tr></thead><tbody>"
        + rows
        + "</tbody></table></div></main></body></html>"
    )


class AccountTableStickyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise unittest.SkipTest("playwright is not installed: %s" % exc)
        cls._playwright = sync_playwright().start()
        try:
            cls.browser = cls._playwright.chromium.launch(headless=True)
        except Exception as exc:
            cls._playwright.stop()
            raise unittest.SkipTest("Chromium is unavailable: %s" % exc)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._playwright.stop()

    def page(self, width, height):
        page = self.browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=1.75,
        )
        page.set_content(fixture(), wait_until="load")
        return page

    def test_desktop_scroll_pins_version_and_account_columns(self):
        page = self.page(1419, 729)
        try:
            data = page.evaluate(
                """() => {
                  const wrap = document.querySelector('#wrap');
                  const account = wrap.querySelector('tbody tr:not(.cool-row) td:nth-child(2)');
                  const name = account.querySelector('.acct-name');
                  const version = wrap.querySelector('tbody tr:not(.cool-row) td:nth-child(1)');
                  const cool = wrap.querySelector('tr.cool-row td');
                  wrap.scrollLeft = wrap.scrollWidth;
                  const wr = wrap.getBoundingClientRect();
                  const ar = account.getBoundingClientRect();
                  const nr = name.getBoundingClientRect();
                  const vr = version.getBoundingClientRect();
                  return {
                    scrollLeft: wrap.scrollLeft,
                    maxScroll: wrap.scrollWidth - wrap.clientWidth,
                    wrapLeft: wr.left,
                    wrapRight: wr.right,
                    accountLeft: ar.left,
                    accountRight: ar.right,
                    versionLeft: vr.left,
                    nameLeft: nr.left,
                    nameRight: nr.right,
                    nameWidth: nr.width,
                    accountPosition: getComputedStyle(account).position,
                    coolPosition: getComputedStyle(cool).position,
                  };
                }"""
            )
        finally:
            page.close()
        self.assertGreater(data["maxScroll"], 0)
        self.assertEqual(data["scrollLeft"], data["maxScroll"])
        self.assertEqual(data["accountPosition"], "sticky")
        self.assertAlmostEqual(data["versionLeft"], data["wrapLeft"], delta=1)
        self.assertAlmostEqual(data["accountLeft"], data["wrapLeft"] + 76, delta=1)
        self.assertGreater(data["nameWidth"], 0)
        self.assertGreaterEqual(data["nameLeft"], data["wrapLeft"] - 1)
        self.assertLessEqual(data["nameRight"], data["wrapRight"] + 1)
        self.assertNotEqual(data["coolPosition"], "sticky")

    def test_mobile_cards_cancel_sticky_and_do_not_overflow(self):
        page = self.page(390, 844)
        try:
            data = page.evaluate(
                """() => {
                  document.querySelector('#wrap').style.width = '100%';
                  const td1 = document.querySelector('tbody tr:not(.cool-row) td:nth-child(1)');
                  const td2 = document.querySelector('tbody tr:not(.cool-row) td:nth-child(2)');
                  return {
                    first: getComputedStyle(td1).position,
                    second: getComputedStyle(td2).position,
                    docWidth: document.documentElement.scrollWidth,
                    viewport: innerWidth,
                  };
                }"""
            )
        finally:
            page.close()
        self.assertEqual(data["first"], "static")
        self.assertEqual(data["second"], "static")
        self.assertLessEqual(data["docWidth"], data["viewport"] + 1)


if __name__ == "__main__":
    unittest.main()
