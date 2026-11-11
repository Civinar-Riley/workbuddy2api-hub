"""账号活动历史（issue #34）：持久化、记录点、读取 API、保留与并发。

数据目录来自 tests/_isolated_dirs.py 的共享 helper，每条用例再在它下面换一个
自己的目录，所以既不会碰仓库自己的 usage/ 与 accounts/，用例之间也互相看不见
对方的记录。
"""
import calendar
import datetime
import importlib
import inspect
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _isolated_dirs import isolated_data_dirs  # noqa: E402  (its own data root)
_TMP = isolated_data_dirs("wb-activity-")

import wb_accounts
import wb_activity
import wb_proxy
import wb_scheduler

SAFE_FIELDS = {"ts", "uid", "nickname", "realm", "task", "trigger", "ok", "message"}


def _response(payload):
    class Response(object):
        def read(self, *_args):
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    return Response()


def _web_urlopen():
    """网页通道的两个上游答案（建会话、取沙箱），其余请求回一个完成态。"""
    def fake(req, **_kwargs):
        if req.full_url.endswith("/session"):
            return _response({"code": 0, "msg": "OK",
                              "data": {"link": "https://box.example/acp",
                                       "token": "sandbox-token",
                                       "sessionId": "2102411494602919936",
                                       "cwd": "/workspace"}})
        if req.full_url.endswith("/conversations/"):
            return _response({"code": 0, "msg": "OK",
                              "data": {"id": "2102411494602919936"}})
        return _response({"code": 0, "msg": "OK", "data": {"status": "completed"}})
    return fake


def _finished_turn(*_args, **_kwargs):
    return {"ok": True, "status": "completed", "chunks": 2, "elapsed_ms": 10,
            "error": ""}


class IsolatedCase(unittest.TestCase):
    """每条用例一个自己的数据目录。"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="wb-activity-case-")
        self.addCleanup(self.dir.cleanup)
        wb_activity.set_data_dir(self.dir.name)
        self.addCleanup(wb_activity.set_data_dir, None)

    def rows(self):
        """按写入顺序读出记录。"""
        return wb_activity.load(usage_dir=self.dir.name)

    def query(self, **kwargs):
        """读取 API 的口径（数据目录固定在本用例的临时目录）。"""
        return wb_activity.query(usage_dir=self.dir.name, **kwargs)

    def messages(self, **kwargs):
        return [row["message"] for row in self.query(**kwargs)["rows"]]

    def latest(self):
        """最新一条记录（读取 API 的口径）。"""
        rows = self.query(range_key="all")["rows"]
        return rows[0] if rows else None

    def account(self, realm="cn", uid="uid-1", **extra):
        data = {"uid": uid, "nickname": "nick-" + uid, "realm": realm,
                "accessToken": "dummy-token", "expiresAt": 0}
        data.update(extra)
        return wb_accounts.Account(data)


class PersistenceTests(IsolatedCase):
    def test_one_attempt_is_one_row_of_allowlisted_fields(self):
        row = wb_activity.record(uid="u1", nickname="Nick", realm="cn",
                                 task=wb_activity.TASK_CHECKIN, trigger="manual",
                                 ok=True, message="签到成功")
        self.assertIsNotNone(row)
        self.assertEqual(set(row), SAFE_FIELDS)
        stored = self.rows()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["uid"], "u1")
        self.assertEqual(stored[0]["nickname"], "Nick")
        self.assertTrue(stored[0]["ok"])
        # 时间带本地偏移量，读取端不必猜时区
        self.assertRegex(stored[0]["ts"],
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

    def test_an_unknown_trigger_is_kept_as_unknown_not_dropped(self):
        row = wb_activity.record(uid="u1", nickname="Nick", realm="cn",
                                 task="checkin", trigger="something-new",
                                 ok=True, message="ok")
        self.assertEqual(row["trigger"], "unknown")

    def test_malformed_and_truncated_lines_are_skipped(self):
        good = {"ts": wb_activity._timestamp(), "uid": "good", "nickname": "n",
                "realm": "cn", "task": "checkin", "trigger": "manual",
                "ok": True, "message": "ok"}
        with open(wb_activity.history_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(good, ensure_ascii=False) + "\n")
            fh.write("{not json at all\n")
            fh.write('{"ts": "2026-01-01T00:00:00+08:00", "uid": "trunc\n')
            fh.write("[1, 2, 3]\n")          # 合法 JSON，但不是一条记录
            fh.write("\n")
        self.assertEqual([r["uid"] for r in self.rows()], ["good"])

    def test_restart_keeps_the_history(self):
        # 用环境变量指定的目录（也就是进程重启后模块自己会选的那个），再整模块
        # 重新加载一次：历史只可能在磁盘上，内存里没有任何东西可依赖。
        wb_activity.set_data_dir(None)
        path = wb_activity.history_path()
        self.assertTrue(os.path.abspath(path).startswith(os.path.abspath(_TMP.name)),
                        "这个用例必须写在临时目录里")
        wb_activity.record(uid="u1", nickname="Nick", realm="cn", task="checkin",
                           trigger="scheduler", ok=True, message="签到成功")
        module = importlib.reload(wb_activity)
        self.assertEqual([r["uid"] for r in module.load()], ["u1"])

    def test_retention_caps_age_and_records(self):
        now = datetime.datetime.now().astimezone()
        old = (now - datetime.timedelta(days=200)).isoformat(timespec="seconds")
        fresh = now.isoformat(timespec="seconds")
        lines = []
        for i in range(3):
            lines.append({"ts": old, "uid": "old-%d" % i, "nickname": "n",
                          "realm": "cn", "task": "checkin",
                          "trigger": "scheduler", "ok": True, "message": "old"})
        for i in range(5):
            lines.append({"ts": fresh, "uid": "fresh-%d" % i, "nickname": "n",
                          "realm": "cn", "task": "checkin",
                          "trigger": "scheduler", "ok": True, "message": "fresh"})
        with open(wb_activity.history_path(), "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        kept, dropped = wb_activity.compact(usage_dir=self.dir.name, max_records=4,
                                            max_age_days=90)
        self.assertEqual((kept, dropped), (4, 4))
        remaining = self.rows()
        self.assertEqual([r["uid"] for r in remaining],
                         ["fresh-1", "fresh-2", "fresh-3", "fresh-4"])

    def test_compaction_runs_on_size_not_on_every_append(self):
        # 时间节奏在这里关掉（区间调到很大），单独验大小阈值那条触发。
        with mock.patch.object(wb_activity, "COMPACT_TRIGGER_BYTES", 300), \
                mock.patch.object(wb_activity, "COMPACT_INTERVAL_SECONDS", 10 ** 9), \
                mock.patch.object(wb_activity, "MAX_RECORDS", 2):
            for i in range(10):
                wb_activity.record(uid="u%d" % i, nickname="n", realm="cn",
                                   task="checkin", trigger="scheduler", ok=True,
                                   message="row %d" % i)
        self.assertLessEqual(len(self.rows()), 2)

    def test_retention_happens_on_the_normal_recording_path(self):
        """不手动调 compact()、也不跨大小阈值：时间节奏自己把条数上限压下来。"""
        now = time.time()
        with mock.patch.object(wb_activity, "MAX_RECORDS", 3), \
                mock.patch.object(wb_activity, "COMPACT_TRIGGER_BYTES", 10 ** 9), \
                mock.patch.object(wb_activity, "COMPACT_INTERVAL_SECONDS", 3600), \
                mock.patch.object(wb_activity, "_last_compact", now):
            for i in range(10):
                wb_activity.record(uid="u%d" % i, nickname="n", realm="cn",
                                   task="checkin", trigger="scheduler", ok=True,
                                   message="row %d" % i)
            self.assertEqual(len(self.rows()), 10, "节奏没到，不该每条都整理")
            # 六小时后的第一条正常记录：到点了，整理发生。
            with mock.patch.object(wb_activity, "_last_compact", now - 6 * 3600):
                wb_activity.record(uid="last", nickname="n", realm="cn",
                                   task="checkin", trigger="scheduler", ok=True,
                                   message="row last")
        remaining = self.rows()
        self.assertEqual(len(remaining), 3)
        self.assertEqual(remaining[-1]["uid"], "last")

    def test_the_cadence_also_enforces_the_age_bound(self):
        """低流量历史跨不过大小阈值，年龄上限同样靠时间节奏落地。"""
        now = datetime.datetime.now().astimezone()
        old = (now - datetime.timedelta(days=200)).isoformat(timespec="seconds")
        with open(wb_activity.history_path(), "a", encoding="utf-8") as fh:
            for i in range(2):
                fh.write(json.dumps({"ts": old, "uid": "ancient-%d" % i,
                                     "nickname": "n", "realm": "cn",
                                     "task": "checkin", "trigger": "scheduler",
                                     "ok": True, "message": "old"}) + "\n")
        with mock.patch.object(wb_activity, "COMPACT_TRIGGER_BYTES", 10 ** 9), \
                mock.patch.object(wb_activity, "COMPACT_INTERVAL_SECONDS", 3600), \
                mock.patch.object(wb_activity, "_last_compact",
                                  time.time() - 6 * 3600):
            wb_activity.record(uid="fresh", nickname="n", realm="cn",
                               task="checkin", trigger="scheduler", ok=True,
                               message="row fresh")
        self.assertEqual([r["uid"] for r in self.rows()], ["fresh"])

    def test_concurrent_appends_and_compaction_keep_the_file_readable(self):
        written = []
        errors = []
        stop = threading.Event()

        def appender(number):
            try:
                for i in range(25):
                    row = wb_activity.record(uid="u%d" % number, nickname="n",
                                             realm="cn", task="checkin",
                                             trigger="scheduler", ok=True,
                                             message="row %d" % i)
                    written.append(row)
            except Exception as exc:          # pragma: no cover - 带回主线程断言
                errors.append(exc)

        def compactor():
            try:
                while not stop.is_set():
                    wb_activity.compact(max_records=100000, max_age_days=3650)
            except Exception as exc:          # pragma: no cover
                errors.append(exc)

        worker = threading.Thread(target=compactor)
        worker.start()
        threads = [threading.Thread(target=appender, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        stop.set()
        worker.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(written), 200)
        self.assertTrue(all(row is not None for row in written), "有记录没写进去")
        with open(wb_activity.history_path(), encoding="utf-8") as fh:
            raw = fh.read().splitlines()
        self.assertEqual(len(raw), 200, "并发追加/整理丢了行")
        for line in raw:
            json.loads(line)                  # 半行、交错写入都会在这里炸


class RecordingTests(IsolatedCase):
    def test_cn_checkin_success_and_failure_write_one_row_each(self):
        account = self.account("cn", "cn-1")
        with mock.patch.object(wb_accounts, "http_json",
                               lambda url, **kw: {"code": 0, "msg": "签到成功"}):
            res = account.checkin(trigger="manual")
        self.assertTrue(res.get("ok"), res)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task"], "checkin")
        self.assertEqual(rows[0]["trigger"], "manual")
        self.assertEqual(rows[0]["realm"], "cn")
        self.assertEqual(rows[0]["uid"], "cn-1")
        self.assertEqual(rows[0]["nickname"], "nick-cn-1")
        self.assertTrue(rows[0]["ok"])
        self.assertIn("签到成功", rows[0]["message"])

        with mock.patch.object(
                wb_accounts, "http_json",
                lambda url, **kw: {"code": 500, "msg": "签到失败：活动未开始"}):
            res = account.checkin(trigger="scheduler")
        self.assertFalse(res.get("ok"))
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[-1]["ok"])
        self.assertEqual(rows[-1]["trigger"], "scheduler")
        self.assertIn("活动未开始", rows[-1]["message"])
        self.assertEqual(self.latest()["ok"], False)

    def test_intl_daily_chat_success_and_failure_write_one_row_each(self):
        account = self.account("intl", "intl-1")
        with mock.patch.object(wb_accounts, "urlopen",
                               lambda req, **kw: _response({})), \
                mock.patch.object(wb_accounts.Account, "fetch_credits",
                                  lambda self: {"ok": True}):
            res = account.daily_chat(web=False, trigger="scheduler")
        self.assertTrue(res.get("ok"), res)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task"], "daily_chat")
        self.assertEqual(rows[0]["trigger"], "scheduler")
        self.assertEqual(rows[0]["realm"], "intl")
        self.assertTrue(rows[0]["ok"])

        # The same account on a later day: a success today must not hide the
        # next day's failure row.
        account.last_daily_chat = None

        def boom(req, **kw):
            raise RuntimeError("upstream down")
        with mock.patch.object(wb_accounts, "urlopen", boom):
            res = account.daily_chat(web=False, trigger="manual")
        self.assertFalse(res.get("ok"))
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[-1]["ok"])
        self.assertIn("upstream down", rows[-1]["message"])

    def test_the_web_substep_folds_into_the_same_row(self):
        account = self.account("intl", "intl-2")
        with mock.patch.object(wb_accounts, "urlopen", _web_urlopen()), \
                mock.patch.object(wb_accounts.Account, "fetch_credits",
                                  lambda self: {"ok": True}), \
                mock.patch.object(wb_accounts.wb_webagent, "run_turn",
                                  _finished_turn):
            res = account.daily_chat(web=True, trigger="scheduler")
        self.assertTrue(res.get("ok"), res)
        self.assertTrue(res.get("web", {}).get("ok"), res)
        rows = self.rows()
        self.assertEqual(len(rows), 1, "网页通道那一步不该多记一行")
        self.assertIn("网页通道", rows[0]["message"])

    def test_a_failed_web_substep_is_reported_in_the_same_row(self):
        account = self.account("intl", "intl-web-fail")
        with mock.patch.object(wb_accounts, "urlopen", _web_urlopen()), \
                mock.patch.object(wb_accounts.Account, "fetch_credits",
                                  lambda self: {"ok": True}), \
                mock.patch.object(wb_accounts.wb_webagent, "run_turn",
                                  lambda *a, **kw: {"ok": False, "status": "working",
                                                    "chunks": 0, "elapsed_ms": 5,
                                                    "error": "会话在 120s 内没有跑完"}):
            res = account.daily_chat(web=True, trigger="scheduler")
        self.assertFalse(res.get("ok"), res)     # 网页腿才算数，它失败整次就是失败
        self.assertFalse(res["web"]["ok"])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["ok"])
        self.assertIn("网页通道失败", rows[0]["message"])

    def test_a_direct_web_trigger_records_its_own_row(self):
        account = self.account("intl", "intl-3")
        with mock.patch.object(wb_accounts, "urlopen", _web_urlopen()), \
                mock.patch.object(wb_accounts.wb_webagent, "run_turn",
                                  _finished_turn):
            res = account.daily_chat_web(trigger="manual")
        self.assertTrue(res.get("ok"), res)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trigger"], "manual")
        self.assertEqual(rows[0]["task"], "daily_chat")

    def test_guard_rejections_are_not_attempts(self):
        cn = self.account("cn", "cn-2")
        intl = self.account("intl", "intl-4")
        self.assertFalse(cn.daily_chat().get("ok"))
        self.assertFalse(cn.daily_chat_web().get("ok"))
        self.assertFalse(intl.checkin().get("ok"))
        self.assertEqual(self.rows(), [], "区域不符是调用方的问题，不是一次签到尝试")


class SchedulerRecordingTests(IsolatedCase):
    def test_the_scheduler_records_only_the_attempts_it_makes(self):
        due = self.account("cn", "cn-due")
        done = self.account("cn", "cn-done")
        done.last_checkin = time.strftime("%Y-%m-%d %H:%M:%S")
        intl = self.account("intl", "intl-due")
        pool = type("Pool", (), {"accounts": [due, done, intl]})()

        calls = []

        def fake_http_json(url, **kwargs):
            calls.append(url)
            return {"code": 0, "msg": "签到成功"}

        with mock.patch.object(wb_accounts, "http_json", fake_http_json), \
                mock.patch.object(wb_accounts, "urlopen", _web_urlopen()), \
                mock.patch.object(wb_accounts.Account, "fetch_credits",
                                  lambda self: {"ok": True}), \
                mock.patch.object(wb_scheduler, "do_cat_travel",
                                  lambda acc: {"action": None}), \
                mock.patch.object(wb_scheduler.wb_tasks, "run_night_growth",
                                  lambda acc: {"logs": []}), \
                mock.patch.object(wb_scheduler.time, "sleep", lambda *_: None):
            scheduler = wb_scheduler.Scheduler(pool)
            scheduler.cat_hours = []
            scheduler._run_cycle("测试第一轮", force=True)
            first_round = len(self.rows())
            scheduler._run_cycle("测试第二轮", force=True)

        rows = self.rows()
        self.assertEqual(first_round, 2, "两个到期账号各记一行")
        self.assertEqual(len(rows), 2, "已完成的账号第二轮不该刷出假成功")
        self.assertEqual(sorted(r["task"] for r in rows), ["checkin", "daily_chat"])
        self.assertTrue(all(r["trigger"] == "scheduler" for r in rows))
        self.assertEqual(len(calls), 1, "只有真正到期的那个账号签了一次")


class CallSiteAuditTests(unittest.TestCase):
    """调用点的源码级审计：漏了 trigger 的调用点会在历史里留下 unknown。"""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_every_call_site_names_its_trigger(self):
        bare = re.compile(r"\.(checkin|daily_chat|daily_chat_web)\(\s*\)")
        offenders = []
        for name in ("wb_proxy.py", "wb_accounts.py", "wb_scheduler.py",
                     "wb_tasks.py"):
            with open(os.path.join(self.ROOT, name), encoding="utf-8") as fh:
                for number, line in enumerate(fh, 1):
                    if bare.search(line):
                        offenders.append("%s:%d" % (name, number))
        self.assertEqual(offenders, [], "这些调用点没有说明触发来源")

    def test_account_add_and_import_carry_their_own_trigger(self):
        # OAuth 添加账号在 poll_login 里落地，桌面端导入在 import_desktop_credential
        # 里落地；两条路径都顺手签一次到，来源要各自说清楚。
        self.assertIn('checkin(trigger="account_add")',
                      inspect.getsource(wb_accounts.AccountPool.poll_login))
        self.assertIn('checkin(trigger="account_import")',
                      inspect.getsource(
                          wb_accounts.AccountPool.import_desktop_credential))

    def test_the_three_attempt_methods_are_the_recording_points(self):
        for name, task in (("checkin", "TASK_CHECKIN"),
                           ("daily_chat", "TASK_DAILY_CHAT"),
                           ("daily_chat_web", "TASK_DAILY_CHAT")):
            source = inspect.getsource(getattr(wb_accounts.Account, name))
            self.assertIn("wb_activity.record_attempt", source, name)
            self.assertIn(task, source, name)

    def test_the_nested_web_call_is_marked_as_already_recorded(self):
        # Desktop fallback and web execution are separate phases: the web leg
        # runs once after a desktop candidate succeeds, not once per candidate.
        source = inspect.getsource(wb_accounts.Account._daily_chat_web_step)
        self.assertIn("daily_chat_web(trigger=None)", source)


class ReadApiTests(IsolatedCase):
    """读取口径：窗口、筛选、上限，以及坏时间戳不能绕过下界。"""

    def seed(self, rows):
        """按时间顺序写入（追加序即时间序）。"""
        for message, uid, realm, task, trigger, ok, ts in sorted(
                rows, key=lambda item: item[6]):
            with mock.patch.object(wb_activity, "_timestamp", lambda ts=ts: ts):
                wb_activity.record(uid=uid, nickname="nick-" + uid, realm=realm,
                                   task=task, trigger=trigger, ok=ok,
                                   message=message)

    def window_rows(self):
        """默认窗口那批：今天三条、23 小时前、25 小时前、40 天前。"""
        now = datetime.datetime.now().astimezone()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

        def at(seconds):
            return (midnight + datetime.timedelta(seconds=seconds)).isoformat(
                timespec="seconds")

        return [
            ("old-40d", "u-cn", "cn", "checkin", "scheduler", True,
             (now - datetime.timedelta(days=40)).isoformat(timespec="seconds")),
            ("h25", "u-cn", "cn", "checkin", "scheduler", True,
             (now - datetime.timedelta(hours=25)).isoformat(timespec="seconds")),
            ("h23", "u-cn", "cn", "checkin", "scheduler", True,
             (now - datetime.timedelta(hours=23)).isoformat(timespec="seconds")),
            ("ok-2min", "u-cn", "cn", "checkin", "scheduler", True, at(1)),
            ("failed-1min", "u-cn", "cn", "checkin", "manual", False, at(2)),
            ("newest", "u-intl", "intl", "daily_chat", "scheduler", True, at(3)),
        ]

    def test_newest_first_and_the_default_window(self):
        self.seed(self.window_rows())
        payload = self.query()
        self.assertEqual(payload["range"], "7d")
        self.assertEqual(payload["limit"], wb_activity.DEFAULT_LIMIT)
        self.assertEqual(payload["total"], 5, "40 天前那条落在默认窗口外")
        stamps = [wb_activity._parse_ts(row["ts"]) for row in payload["rows"]]
        self.assertEqual(stamps, sorted(stamps, reverse=True), "必须最新在前")
        self.assertNotIn("old-40d", self.messages())

    def test_rolling_windows(self):
        self.seed(self.window_rows())
        self.assertEqual(sorted(self.messages(range_key="1d")),
                         ["failed-1min", "h23", "newest", "ok-2min"],
                         "1d 是滚动 24 小时：25 小时前那条在外")
        self.assertEqual(sorted(self.messages(range_key="30d")),
                         ["failed-1min", "h23", "h25", "newest", "ok-2min"])
        self.assertEqual(len(self.messages(range_key="90d")), 6)
        self.assertEqual(len(self.messages(range_key="all")), 6)

    def test_today_is_the_local_calendar_day_not_a_rolling_24h(self):
        # 昨天 23:59 与今天 00:00:01 只差一分钟，但只有后者算「今日」。边界按平台
        # 对本地零点的规则算（mktime），所以在夏令时切换日这条依然成立。
        today = time.localtime()
        midnight = time.mktime((today.tm_year, today.tm_mon, today.tm_mday,
                                0, 0, 0, 0, 0, -1))

        def stamp(epoch):
            return datetime.datetime.fromtimestamp(epoch).astimezone().isoformat(
                timespec="seconds")

        self.seed([
            ("late-yesterday", "u-cn", "cn", "checkin", "scheduler", True,
             stamp(midnight - 60)),
            ("just-after-midnight", "u-cn", "cn", "checkin", "scheduler", True,
             stamp(midnight + 1)),
        ])
        self.assertEqual(self.messages(range_key="today"), ["just-after-midnight"],
                         "today 是本地日历日，昨天深夜那条不该混进来")
        self.assertEqual(len(self.messages(range_key="all")), 2)

    def test_local_midnight_is_the_local_calendar_zero(self):
        """零点的 epoch：它的本地时间必须是今天 00:00:00，而不是昨天 23:00。"""
        injected = time.time()
        midnight = wb_activity.local_midnight(injected)
        local = time.localtime(midnight)
        today = time.localtime(injected)
        self.assertEqual((local.tm_hour, local.tm_min, local.tm_sec), (0, 0, 0))
        self.assertEqual((local.tm_year, local.tm_mon, local.tm_mday),
                         (today.tm_year, today.tm_mon, today.tm_mday))
        now_midnight = time.localtime(wb_activity.local_midnight())
        self.assertEqual((now_midnight.tm_hour, now_midnight.tm_min), (0, 0))

    def test_local_midnight_uses_the_offset_in_force_at_midnight(self):
        """夏令时切换日：本地零点与此刻的偏移不同，零点要按零点那一刻的规则算。

        美国 2026-03-08 02:00 进入夏令时：当天 12:00 是 EDT(-4)，而当天零点还是
        EST(-5)。拿此刻的偏移去替换字段会得到 04:00Z（早一小时），把前一天 23:00
        之后的记录算进「今日」；11-01 回拨那天反过来晚一小时，把今天 00:00-01:00
        的记录漏掉。这条检查需要平台能在进程内切换时区（POSIX 的 tzset），
        Windows 上跳过 —— 核心用例不依赖它。
        """
        if not hasattr(time, "tzset"):
            self.skipTest("this platform cannot switch time zones at runtime")
        original = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "America/New_York"
            time.tzset()
            spring_noon = calendar.timegm((2026, 3, 8, 16, 0, 0, 0, 0, 0))
            if time.localtime(spring_noon).tm_hour != 12:
                self.skipTest("no America/New_York tz data on this platform")
            # 春季：真零点 05:00Z（EST），按此刻的 -4 会算成 04:00Z
            self.assertEqual(wb_activity.local_midnight(spring_noon),
                             calendar.timegm((2026, 3, 8, 5, 0, 0, 0, 0, 0)))
            # 秋季：真零点 04:00Z（EDT），按此刻的 -5 会算成 05:00Z
            fall_noon = calendar.timegm((2026, 11, 1, 17, 0, 0, 0, 0, 0))
            self.assertEqual(time.localtime(fall_noon).tm_hour, 12)
            self.assertEqual(wb_activity.local_midnight(fall_noon),
                             calendar.timegm((2026, 11, 1, 4, 0, 0, 0, 0, 0)))
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()

    def test_filters(self):
        self.seed(self.window_rows())
        self.assertEqual(self.messages(uid="u-intl"), ["newest"])
        self.assertEqual(self.query(task="checkin")["total"], 4)
        self.assertEqual(self.messages(result="failed"), ["failed-1min"])
        self.assertEqual(self.query(result="fail")["total"], 1, "别名要认")
        self.assertEqual(self.query(task="daily_chat", result="ok")["total"], 1)

    def test_limit_is_bounded_and_tolerates_junk(self):
        self.seed(self.window_rows())
        bounded = self.query(limit=2)
        self.assertEqual(len(bounded["rows"]), 2)
        self.assertEqual(bounded["total"], 5, "total 是筛选后的总数，不是本页条数")
        self.assertEqual(self.query(limit=99999)["limit"], wb_activity.MAX_LIMIT)
        self.assertEqual(self.query(limit="junk")["limit"], wb_activity.DEFAULT_LIMIT)
        self.assertEqual(self.query(limit=0)["limit"], 1)

    def test_an_unknown_filter_is_rejected_not_ignored(self):
        self.seed(self.window_rows())
        for kwargs in ({"range_key": "2w"}, {"task": "chat"}, {"result": "maybe"}):
            with self.assertRaises(ValueError):
                self.query(**kwargs)


class ReadPathSchemaTests(IsolatedCase):
    """读取路径只认 8 个字段：文件里多出来的键不能从 API 漏出去。"""

    def append_raw(self, row):
        with open(wb_activity.history_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def test_a_row_with_extra_credential_fields_does_not_leak_them(self):
        self.append_raw({
            "ts": wb_activity._timestamp(), "uid": "u1", "nickname": "Nick",
            "realm": "cn", "task": "checkin", "trigger": "scheduler", "ok": True,
            "message": "签到成功",
            # 合法 JSON，但 schema 不对：夹带了凭证与内部字段
            "accessToken": "eyJhbGciOiJIUzI1NiJ9.secretpayload.signature",
            "refreshToken": "rt-deadbeefdeadbeefdeadbeef",
            "cookie": "session=abcdef123456",
            "headers": {"Authorization": "Bearer abcdef1234567890"},
            "body": {"password": "hunter2"},
            "note": "internal",
        })
        payload = self.query(range_key="all")
        self.assertEqual(len(payload["rows"]), 1)
        row = payload["rows"][0]
        self.assertEqual(set(row), SAFE_FIELDS)
        served = json.dumps(payload, ensure_ascii=False)
        for leaked in ("eyJhbGciOiJIUzI1NiJ9.secretpayload.signature",
                       "rt-deadbeefdeadbeefdeadbeef", "session=abcdef123456",
                       "Bearer abcdef1234567890", "hunter2", "internal",
                       "accessToken", "refreshToken", "cookie", "headers",
                       "body"):
            self.assertNotIn(leaked, served)
        self.assertEqual(row["uid"], "u1")
        self.assertTrue(row["ok"])

    def test_a_wrong_schema_row_is_normalised_not_trusted(self):
        self.append_raw({"ts": wb_activity._timestamp(), "uid": 42,
                         "nickname": None, "realm": "mars", "task": "mining",
                         "trigger": "somebody", "ok": "false",
                         "message": ["not", "a", "string"]})
        row = self.query(range_key="all")["rows"][0]
        self.assertEqual(set(row), SAFE_FIELDS)
        self.assertEqual(row["uid"], "42")
        self.assertEqual(row["nickname"], "")
        self.assertEqual(row["realm"], "")
        self.assertEqual(row["task"], "")
        self.assertEqual(row["trigger"], "unknown")
        self.assertFalse(row["ok"], '"false" 不能读成成功')
        self.assertIsInstance(row["message"], str)

    def test_an_unreadable_timestamp_cannot_bypass_a_bounded_range(self):
        for ts in ("", None, "not a timestamp", 12345, {"nested": True}):
            self.append_raw({"ts": ts, "uid": "u1", "nickname": "n",
                             "realm": "cn", "task": "checkin",
                             "trigger": "manual", "ok": True, "message": "bad ts"})
        self.append_raw({"ts": wb_activity._timestamp(), "uid": "u2",
                         "nickname": "n", "realm": "cn", "task": "checkin",
                         "trigger": "manual", "ok": True, "message": "good ts"})
        for key in ("today", "7d", "30d", "90d"):
            self.assertEqual([row["uid"] for row in self.query(range_key=key)["rows"]],
                             ["u2"], "%s 里坏时间戳的行漏出来了" % key)
        # all 没有下界，也就没有可绕过的边界；行照常返回，仍然只有 8 个字段
        rows = self.query(range_key="all")["rows"]
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(set(row) == SAFE_FIELDS for row in rows))


class RouteTests(IsolatedCase):
    class Handler(wb_proxy.Handler):
        def __init__(self, authorized=True):
            self.headers = {}
            self.path = "/activity/history"
            self.captured = None
            self.authorized = authorized

        def _authorized(self):
            if self.authorized:
                return True
            return wb_proxy.Handler._authorized(self)

        def _json(self, code, obj):
            self.captured = (code, obj)

        def _error(self, code, message, err_type="server_error"):
            self.captured = (code, {"error": message})

    def test_route_serves_the_history_from_the_gateway_data_dir(self):
        wb_activity.record(uid="u1", nickname="Nick", realm="cn", task="checkin",
                           trigger="scheduler", ok=True, message="签到成功")
        handler = self.Handler()
        handler._get_activity_history({"range": ["7d"], "limit": ["10"]})
        code, payload = handler.captured
        self.assertEqual(code, 200)
        self.assertEqual(payload["limit"], 10)
        self.assertEqual([r["uid"] for r in payload["rows"]], ["u1"])

    def test_route_rejects_a_bad_filter_instead_of_ignoring_it(self):
        handler = self.Handler()
        handler._get_activity_history({"result": ["maybe"]})
        code, payload = handler.captured
        self.assertEqual(code, 400)
        self.assertIn("result must be one of", payload["error"])

    def test_route_needs_credentials(self):
        with mock.patch.object(wb_proxy, "auth_required", lambda: True):
            handler = self.Handler(authorized=False)
            handler._get_activity_history({})
        code, _payload = handler.captured
        self.assertEqual(code, 401)

    def test_route_is_registered_and_panel_gated(self):
        self.assertIn('"/activity/history"',
                      inspect.getsource(wb_proxy.Handler.do_GET))
        self.assertTrue(wb_proxy.Handler._is_panel_route("/activity/history"))
        self.assertFalse(wb_proxy.Handler._is_panel_route("/v1/chat/completions"))

    def test_startup_points_the_module_at_the_gateway_data_dir(self):
        self.assertIn("wb_activity.set_data_dir(USAGE_DIR)",
                      inspect.getsource(wb_proxy))


class SecretSafetyTests(IsolatedCase):
    def test_the_record_is_an_allowlist_of_operational_fields(self):
        row = wb_activity.record(uid="u1", nickname="Nick", realm="cn",
                                 task="checkin", trigger="manual", ok=True,
                                 message="ok")
        self.assertEqual(set(row), SAFE_FIELDS)
        for banned in ("token", "cookie", "authorization", "header", "body",
                       "key", "secret", "password"):
            self.assertNotIn(banned, {name.lower() for name in row})

    def test_secret_shaped_text_is_redacted_on_disk_and_through_the_api(self):
        secrets = [
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1aWQiOiJ1MSJ9.abcdefghijklmnop",
            "sk-live-abcdefghijklmnopqrst",
            "cookie: session=abcdef123456",
            "Authorization: Bearer abcdef1234567890",
            "accessToken=deadbeefdeadbeefdeadbeefdeadbeef",
            "9f2c1b4a7e6d8c0f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7081",
        ]
        wb_activity.record(uid="u1", nickname="Nick", realm="cn", task="checkin",
                           trigger="manual", ok=False,
                           message="HTTP 401: " + " | ".join(secrets))
        with open(wb_activity.history_path(), encoding="utf-8") as fh:
            raw = fh.read()
        self.assertIn("[redacted]", raw)
        for secret in secrets:
            self.assertNotIn(secret, raw)
            self.assertNotIn(secret.split()[-1], raw)
        served = json.dumps(wb_activity.query(usage_dir=self.dir.name,
                                              range_key="all"),
                            ensure_ascii=False)
        for secret in secrets:
            self.assertNotIn(secret, served)
            self.assertNotIn(secret.split()[-1], served)

    def test_a_long_upstream_message_is_truncated_to_one_line(self):
        row = wb_activity.record(uid="u1", nickname="Nick", realm="cn",
                                 task="checkin", trigger="manual", ok=False,
                                 message="line one\nline two " + "x" * 500)
        self.assertLessEqual(len(row["message"]), wb_activity.MAX_MESSAGE)
        self.assertNotIn("\n", row["message"])

    def test_the_history_never_lands_in_the_repository_tree(self):
        repository = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertFalse(
            os.path.abspath(wb_activity.data_dir()).startswith(
                os.path.abspath(repository)),
            "用例必须写在临时目录里，而不是仓库的 usage/")


if __name__ == "__main__":
    unittest.main(verbosity=2)
