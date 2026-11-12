"""请求列表的可视化检查（点击失败徽章 → 详情弹窗、四个过滤下拉、截图留档）。

为什么单独一个脚本、而不进 tests/run_all.py：
    run_all 里的套件只依赖标准库 + node，Playwright 是可选依赖（见 README 对
    tests/_mobile_check.py 的说明）。这个脚本需要 Playwright 和一个浏览器，
    所以和 _mobile_check.py 一样按需手动跑：

        python tests/_visual_recent.py            # 跑全部检查
        python tests/_visual_recent.py modal      # 只跑名字含 modal 的检查

数据全部是合成 fixture：假账号、假 usage、假 API Key，不碰 accounts/ 与真实
日志；网关由 http.server 直接托管 dashboard.html，不启动真实反代。

截图写到 %TEMP%\\wb-visual-recent（或 $TMPDIR），供人工复核；检查本身断言 DOM
属性与请求 URL，不依赖看图。
"""
import functools
import http.server
import json
import os
import socket
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(tempfile.gettempdir(), "wb-visual-recent")

PASS = 0
FAIL = 0
ONLY = sys.argv[1] if len(sys.argv) > 1 else ""


def check(name, cond, extra=""):
    global PASS, FAIL
    if ONLY and ONLY not in name:
        return
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + ("  " + str(extra) if extra else ""))


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- fixtures

def row(iso, model, outcome, account, key, key_name, **kw):
    r = {"iso": iso, "model": model, "stream": False, "outcome": outcome,
         "elapsed_ms": kw.get("elapsed_ms", 12), "ttft_ms": None,
         "prompt_tokens": kw.get("prompt_tokens", 0), "completion_tokens": 0,
         "total_tokens": kw.get("prompt_tokens", 0), "credit": 0,
         "account": account, "key": key, "key_name": key_name, "realm": "cn"}
    if outcome != "completed":
        r["error"] = True
        r["status"] = kw.get("status", 500)
        r["message"] = kw.get("message", "")
    return r


ROWS = [
    row("2026-11-12T17:48:41", "deepseek-v4.1-flash", "failed", "u-1001", "kA", "ops-key",
        status=429, elapsed_ms=3150,
        message="HTTP 429 Too Many Requests: quota exhausted for account 159****3813 (code 6004)"),
    row("2026-11-12T17:48:35", "step-5-preview", "failed", "u-1001", "kA", "ops-key",
        status=504, elapsed_ms=12, message="upstream gateway timeout after 12ms"),
    row("2026-11-12T17:48:33", "deepseek-v4.1-flash", "upstream_aborted", "u-1002", "kB", "laptop",
        status=502, elapsed_ms=67, message="stream reset by peer"),
    row("2026-11-12T17:48:30", "step-5-preview", "completed", "u-1002", "kB", "laptop",
        prompt_tokens=120, elapsed_ms=1800),
    row("2026-11-12T17:48:24", "deepseek-v4.1-flash", "client_aborted", "u-1001", "kA", "ops-key",
        elapsed_ms=621),
    row("2026-11-12T17:47:58", "glm-5.3", "completed", "u-1003", "kC", "ci-runner",
        prompt_tokens=42, elapsed_ms=900),
]

SEEN = []


def payload_for(url):
    path = url.split("?")[0]
    q = url.split("?", 1)[1] if "?" in url else ""
    if path.endswith("/usage/recent"):
        SEEN.append(url)
        rows = ROWS
        if "account=u-1001" in q:
            rows = [r for r in rows if r["account"] == "u-1001"]
        if "model=deepseek-v4.1-flash" in q:
            rows = [r for r in rows if r["model"] == "deepseek-v4.1-flash"]
        if "key=kA" in q:
            rows = [r for r in rows if r["key"] == "kA"]
        if "outcome=failed" in q:
            rows = [r for r in rows if r["outcome"] != "completed"]
        if "outcome=completed" in q:
            rows = [r for r in rows if r["outcome"] == "completed"]
        return {"rows": rows, "total": len(rows), "page": 1, "total_pages": 1, "limit": 100,
                "usd_cny": 7.2, "scanned": len(ROWS), "total_exact": "?" not in url,
                "filters": None,
                "accounts_map": {"u-1001": {"nickname": "159****3813"},
                                 "u-1002": {"nickname": "166****8472"},
                                 "u-1003": {"nickname": "131****0099"}}}
    if path.endswith("/usage/perf"):
        return {"success_rate_pct": 50.0, "errors": 3, "p50_ms": 900}
    if path.endswith("/usage"):
        # accounts_map 跟着 /usage 走（refreshInner 用它把 uid 翻成昵称），
        # 少了它弹窗里的账号列只能显示 uid 前 8 位。
        return {"requests": 6, "credit": 3.5, "log_file": "usage.jsonl", "realm": "cn",
                "usd_cny": 7.2,
                "accounts_map": {"u-1001": {"nickname": "159****3813"},
                                 "u-1002": {"nickname": "166****8472"},
                                 "u-1003": {"nickname": "131****0099"}}}
    return {"accounts": [], "rows": [], "slots": [], "data": [], "results": [], "byAccount": []}


def launch(p):
    for kwargs in ({"channel": "msedge"}, {}, {"channel": "chrome"}):
        try:
            return p.chromium.launch(**kwargs)
        except Exception:
            continue
    try:
        return p.firefox.launch()
    except Exception:
        raise SystemExit("no browser available for Playwright (chromium/edge/firefox)")


def main():
    os.makedirs(SHOTS, exist_ok=True)
    port = free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=ROOT)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/dashboard.html" % port

    from playwright.sync_api import sync_playwright

    errors = []
    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)[:200]))

        def route(r):
            url = r.request.url
            if url.endswith((".html", ".js", ".css", ".png", ".svg", ".ico")):
                r.continue_()
                return
            r.fulfill(status=200, content_type="application/json", body=json.dumps(payload_for(url)))

        page.route("**/*", route)
        page.goto(base)
        page.wait_for_timeout(600)
        page.evaluate("() => localStorage.setItem('wb-lang', 'zh')")
        page.reload()
        page.wait_for_timeout(1200)
        page.evaluate("() => { const g = document.getElementById('panelGate'); if (g) g.style.display='none'; }")
        page.evaluate("() => { window.VIEW_REALM = 'cn'; refresh(); }")
        page.wait_for_timeout(2000)

        headers = page.eval_on_selector_all("#recent table th", "els => els.map(e => e.textContent)")
        check("请求列表已改名", page.locator("h2:has-text('请求列表')").count() >= 1)
        # 筛选条要在表格上方（挨着标题），不能沉到表格和分页之间。
        order = page.evaluate("""() => {
            const bar = document.querySelector('.recent-filterbar');
            const tbl = document.querySelector('#recent');
            if (!bar || !tbl) return 'missing';
            return (bar.compareDocumentPosition(tbl) & Node.DOCUMENT_POSITION_FOLLOWING) ? 'above' : 'below';
        }""")
        check("筛选条在表格上方", order == "above", order)
        check("结果列后面紧跟耗时（没有多出来的失败原因列）",
              headers.index("结果") + 1 == headers.index("耗时")
              and "失败原因" not in headers, headers)
        check("失败徽章是可点按钮",
              page.eval_on_selector_all("#recent tbody button.badge-btn", "els => els.length") >= 3,
              headers)

        shot = os.path.join(SHOTS, "recent-list.png")
        page.screenshot(path=shot, clip=dict(zip(("x", "y", "width", "height"), (
            max(0, page.locator("#recent").bounding_box()["x"] - 10),
            max(0, page.locator("#recent").bounding_box()["y"] - 90),
            1480, 430))))

        # 点开失败详情
        page.locator("#recent tbody button.badge-btn").first.click()
        page.wait_for_timeout(400)
        modal = page.locator("#recentFailModal")
        meta = page.inner_text("#recentFailMeta")
        msg = page.inner_text("#recentFailMsg")
        check("弹窗打开", modal.is_visible())
        check("弹窗标题是「请求失败详情」", "请求失败详情" in page.inner_text("#recentFailTitle"))
        check("弹窗带 HTTP 状态码", "429" in meta, meta[:120])
        check("弹窗带上游原话", "quota exhausted" in msg, msg[:120])
        check("弹窗带模型 / 账号 / Key",
              "deepseek-v4.1-flash" in meta and "159****3813" in meta and "ops-key" in meta, meta[:200])
        page.screenshot(path=os.path.join(SHOTS, "recent-fail-modal.png"))
        page.click("#recentFailModal .modal-x")
        page.wait_for_timeout(200)
        check("弹窗关闭", not modal.is_visible())

        # 四个过滤下拉：服务端过滤（URL 带参数）+ 结果变少
        total_rows = page.locator("#recent tbody tr").count()
        SEEN.clear()
        page.select_option("#recentAccountFilter", "u-1001")
        page.wait_for_timeout(1200)
        acct_rows = page.locator("#recent tbody tr").count()
        check("按账号过滤后 URL 带 account=u-1001",
              any("account=u-1001" in u for u in SEEN), SEEN[-1] if SEEN else None)
        check("按账号过滤后行数变少", acct_rows < total_rows, (acct_rows, total_rows))

        page.select_option("#recentOutcomeFilter", "failed")
        page.wait_for_timeout(1200)
        check("结果 + 账号组合过滤（outcome=failed&account=u-1001）",
              any("outcome=failed" in u and "account=u-1001" in u for u in SEEN),
              SEEN[-1] if SEEN else None)
        page.select_option("#recentModelSelect", "deepseek-v4.1-flash")
        page.wait_for_timeout(1200)
        check("按模型过滤后 URL 带 model 参数",
              any("model=deepseek-v4.1-flash" in u for u in SEEN), SEEN[-1] if SEEN else None)
        page.screenshot(path=os.path.join(SHOTS, "recent-filtered.png"))

        sel = page.locator(".recent-filterbar").bounding_box()
        page.screenshot(path=os.path.join(SHOTS, "recent-filterbar.png"),
                        clip={"x": sel["x"] - 6, "y": sel["y"] - 6,
                              "width": sel["width"] + 12, "height": sel["height"] + 12})

        page.click(".recent-filterbar button:has-text('清除筛选')")
        page.wait_for_timeout(1200)
        check("清除筛选后行数还原", page.locator("#recent tbody tr").count() == total_rows,
              page.locator("#recent tbody tr").count())

        check("没有 JS 报错", not errors, errors[:2])
        browser.close()
    srv.shutdown()

    print("\n  %d passed, %d failed · 截图: %s" % (PASS, FAIL, SHOTS))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
