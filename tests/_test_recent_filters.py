"""请求列表的四个过滤维度（账号 / 模型 / Key / 结果）在后端的行为。

面板上的下拉只是把 URL 参数拼上去，真正的语义在这里：
  * 带过滤时不走索引，从尾部边读边匹配，读够 page*limit 条命中就停；
  * 「失败」是一个桶：failed / upstream_aborted / client_aborted 都算没成功，
    否则排查时最想看的两类失败反而被筛掉；
  * 停下来时 total 只是下界，响应必须带 total_exact=False 与 scanned，
    面板要如实写成「已扫描 M 行 · 命中 N 条」；
  * 不带过滤时行为与以前逐字一致（索引路径、total、页数都不变）。

No network: 日志、定价快照、设置目录都在临时目录里合成。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="wb-recentfilter-")
USAGE_DIR = os.path.join(_TMP, "usage")
ACCOUNTS_DIR = os.path.join(_TMP, "accounts")
os.makedirs(USAGE_DIR, exist_ok=True)
os.makedirs(ACCOUNTS_DIR, exist_ok=True)
os.environ["WB_PROXY_USAGE_DIR"] = USAGE_DIR
os.environ["ACCOUNTS_DIR"] = ACCOUNTS_DIR
os.environ["WB_USAGE_CACHE"] = "0"
os.environ["WB_COUNT_TTL"] = "0"
os.environ["WB_RECENT_INDEX"] = "0"          # 过滤路径不需要索引；不建就不会干扰

import wb_proxy as P          # noqa: E402
import wb_pricing             # noqa: E402

P.ACCOUNTS_DIR = ACCOUNTS_DIR
wb_pricing.set_data_dir(USAGE_DIR)
wb_pricing.set_settings_dir(ACCOUNTS_DIR)

PASS = FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + label)
    else:
        FAIL += 1
        print("  [FAIL] " + label + ("  " + str(extra) if extra else ""))


def write_rows(rows):
    with open(P.USAGE_LOG, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def row(i, account, model, key, outcome, realm="intl", message=None, status=None):
    r = {
        "at": 1700000000 + i,
        "iso": "2026-11-12T10:%02d:%02d" % (i // 60 % 60, i % 60),
        "model": model,
        "stream": False,
        "outcome": outcome,
        "elapsed_ms": 10 + i,
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
        "account": account,
        "key": key,
        "realm": realm,
    }
    if outcome != "completed":
        r["error"] = True
        if message:
            r["message"] = message
        if status is not None:
            r["status"] = status
    return r


ROWS = [
    row(1, "acct-a", "model-1", "key-1", "completed"),
    row(2, "acct-a", "model-1", "key-1", "failed", message="HTTP 429 too many requests", status=429),
    row(3, "acct-b", "model-2", "key-2", "completed"),
    row(4, "acct-b", "model-2", "key-2", "upstream_aborted", message="stream reset", status=502),
    row(5, "acct-a", "model-2", "key-1", "client_aborted"),
    row(6, "acct-b", "model-1", "key-2", "completed"),
    row(7, "acct-a", "model-1", "key-1", "failed", message="no credits", status=402),
]
write_rows(ROWS)


def outcomes(payload):
    return [P.row_outcome(r) for r in payload["rows"]]


# --------------------------------------------------------------------------
# 1. 不带过滤：行为保持不变
base = P.recent_usage(limit=10, realm="intl", page=1)
check("无过滤时返回全部行（新→旧）", len(base["rows"]) == len(ROWS), len(base["rows"]))
check("无过滤时 total 是全量、total_exact=True",
      base["total"] == len(ROWS) and base["total_exact"] is True, base["total"])
check("无过滤时不给 filters / scanned",
      base["filters"] is None and base["scanned"] is None, (base["filters"], base["scanned"]))

# --------------------------------------------------------------------------
# 2. 按账号
by_acct = P.recent_usage(limit=10, realm="intl", account="acct-a")
check("按账号过滤只回该账号的行",
      {r["account"] for r in by_acct["rows"]} == {"acct-a"}, by_acct["rows"])
check("按账号过滤的命中数正确（4 条）", by_acct["total"] == 4, by_acct["total"])
check("按账号过滤回显 filters", by_acct["filters"] == {"account": "acct-a"}, by_acct["filters"])
check("读到文件头才停 → total_exact=True", by_acct["total_exact"] is True, by_acct["total_exact"])

# --------------------------------------------------------------------------
# 3. 「失败」是桶：failed + upstream_aborted + client_aborted
by_failed = P.recent_usage(limit=10, realm="intl", outcome="failed")
check("失败桶包含 failed / upstream_aborted / client_aborted",
      sorted(outcomes(by_failed)) == ["client_aborted", "failed", "failed", "upstream_aborted"],
      outcomes(by_failed))
check("失败桶不含 completed", "completed" not in outcomes(by_failed), outcomes(by_failed))

by_ok = P.recent_usage(limit=10, realm="intl", outcome="completed")
check("成功过滤只剩 completed",
      outcomes(by_ok) == ["completed", "completed", "completed"], outcomes(by_ok))

# --------------------------------------------------------------------------
# 4. 按 Key / 按模型
by_key = P.recent_usage(limit=10, realm="intl", key="key-2")
check("按 Key 过滤只回该 Key 的行",
      {r["key"] for r in by_key["rows"]} == {"key-2"}, by_key["rows"])
check("按 Key 过滤命中 3 条", by_key["total"] == 3, by_key["total"])

by_model = P.recent_usage(limit=10, realm="intl", model="model-1")
check("按模型过滤只回该模型的行",
      {r["model"] for r in by_model["rows"]} == {"model-1"}, by_model["rows"])

# --------------------------------------------------------------------------
# 5. 组合条件 + 分页
combo = P.recent_usage(limit=2, realm="intl", account="acct-a", page=1)
check("账号 + 分页：第一页 2 条、新的在前",
      len(combo["rows"]) == 2 and combo["rows"][0]["at"] == ROWS[6]["at"], combo["rows"])
combo2 = P.recent_usage(limit=2, realm="intl", account="acct-a", page=2)
check("账号 + 分页：第二页接着往下",
      len(combo2["rows"]) == 2 and combo2["rows"][0]["at"] == ROWS[1]["at"], combo2["rows"])

# --------------------------------------------------------------------------
# 6. 扫描上限：命中的行不够时如实报告下界
old_cap = P.RECENT_FILTER_SCAN_ROWS
P.RECENT_FILTER_SCAN_ROWS = 3
try:
    capped = P.recent_usage(limit=10, realm="intl", account="acct-a")
finally:
    P.RECENT_FILTER_SCAN_ROWS = old_cap
check("扫描上限生效：只检查了 3 行", capped["scanned"] == 3, capped["scanned"])
check("撞上限时 total_exact=False（total 是下界）",
      capped["total_exact"] is False, capped["total_exact"])
check("撞上限时 rows 只含已扫到的命中", len(capped["rows"]) <= 3, capped["rows"])

# 另一个区域的行也要计入扫描上限：否则一份国际版 / 国内版混写的日志里，
# 过滤国际版时上限会被国内版行无限绕过，等于退回全文件扫描。
mixed = [row(10, "acct-a", "model-1", "key-1", "completed", realm="intl")]
mixed += [row(11 + i, "noise", "model-1", "key-1", "completed", realm="cn")
          for i in range(10)]
write_rows(mixed)
P.RECENT_FILTER_SCAN_ROWS = 3
try:
    mixed_capped = P.recent_usage(limit=10, realm="intl", account="acct-a")
finally:
    P.RECENT_FILTER_SCAN_ROWS = old_cap
check("其它区域的行也计入扫描上限", mixed_capped["scanned"] == 3, mixed_capped["scanned"])
check("混合区域撞上限时仍如实标成下界",
      mixed_capped["total_exact"] is False, mixed_capped["total_exact"])

# --------------------------------------------------------------------------
# 7. 页数：截断且命中正好整页时多给一页（让「下一页」把剩下的读出来）
write_rows(ROWS)
P.RECENT_FILTER_SCAN_ROWS = 20000
hit_two = P.recent_usage(limit=2, realm="intl", account="acct-b")
check("截断且命中整页时 total_pages 多一页",
      hit_two["total_pages"] >= 2 and hit_two["total_exact"] is False,
      (hit_two["total_pages"], hit_two["total_exact"]))

shutil.rmtree(_TMP, ignore_errors=True)
print("\n  %d passed, %d failed (recent request filters)" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
