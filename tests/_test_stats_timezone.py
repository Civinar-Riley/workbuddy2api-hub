"""统计时区开关：默认关、开启后按固定 +08:00 分桶与格式化。

开关的契约有三条，缺一条就会在上线后以「时间错了但没人报错」的方式出问题：

  * 默认关。缺失键、显式 false、手编的字符串，都必须读作关；关着的时候
    wb_time 的每个助手都是 time 模块的直通，所以一个已存在部署的日 / 小时
    桶、日志时间戳、导出文件名在升级后一字不差。
  * 开与关只差一个固定偏移。中国自 1991 年起无夏令时，所以开启后
    day_key/iso_seconds/midnight/hour_start 相对关闭态正好差 8 小时（跨日
    的行才会换日键），而不是「看宿主时区漂多少算多少」。
  * 日 / 小时桶跟着开关走。同一个 at，关闭时落在宿主时区的日，开启后落在
    北京时间的那一天；跨过北京时间午夜的行必须换桶，否则「今日用量」在
    北京时间 0-8 点会算错一整天。

不冻结时钟：这里比的是同一个 epoch 在两种设置下的表现，跟「现在几点」无关。

    python tests/_test_stats_timezone.py
"""
import os
import sys
import tempfile
import time
import unittest
import calendar

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_settings
import wb_time


def utc_epoch(*fields):
    """Construct an epoch from explicit UTC fields, independent of host timezone."""
    return calendar.timegm(tuple(fields) + (0, 0, 0))


class SettingsSwitchTests(unittest.TestCase):
    def test_missing_key_reads_off(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(wb_settings.settings_path(directory), "w", encoding="utf-8") as fh:
                fh.write("{}")
            self.assertFalse(wb_settings.stats_tz_beijing(directory))

    def test_only_literal_true_reads_on(self):
        with tempfile.TemporaryDirectory() as directory:
            for raw in ("true", 1, "yes", [True], {"on": True}):
                with open(wb_settings.settings_path(directory), "w",
                          encoding="utf-8") as fh:
                    fh.write('{"%s": %s}' % (
                        wb_settings.STATS_TZ_BEIJING_KEY,
                        __import__("json").dumps(raw)))
                self.assertFalse(wb_settings.stats_tz_beijing(directory), raw)

    def test_round_trip_and_neighbours_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(wb_settings.settings_path(directory), "w", encoding="utf-8") as fh:
                fh.write('{"log_db_enabled": true, "hand_written": 7}')
            self.assertTrue(wb_settings.set_stats_tz_beijing(directory, True))
            self.assertTrue(wb_settings.stats_tz_beijing(directory))
            self.assertTrue(wb_settings.log_db_enabled(directory))
            self.assertFalse(wb_settings.set_stats_tz_beijing(directory, False))
            self.assertFalse(wb_settings.stats_tz_beijing(directory))


class HelperTests(unittest.TestCase):
    def tearDown(self):
        wb_time.set_enabled(False)

    def test_off_is_a_passthrough(self):
        wb_time.set_enabled(False)
        for ts in (0.0, 1791445200.0, time.time()):
            self.assertEqual(wb_time.localtime(ts), time.localtime(ts))
            self.assertEqual(wb_time.day_key(ts),
                             time.strftime("%Y-%m-%d", time.localtime(ts)))
            self.assertEqual(wb_time.iso_seconds(ts),
                             time.strftime("%Y-%m-%d %H:%M:%S",
                                           time.localtime(ts)))
        lt = time.localtime(1791445200.0)
        self.assertEqual(
            wb_time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)),
            time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))

    def test_on_shifts_wall_clock_by_eight_hours(self):
        wb_time.set_enabled(True)
        ts = 1791445200.0
        self.assertEqual(wb_time.localtime(ts), time.gmtime(ts + 8 * 3600))
        self.assertEqual(wb_time.day_key(ts),
                         time.strftime("%Y-%m-%d", time.gmtime(ts + 8 * 3600)))
        self.assertEqual(wb_time.iso_seconds(ts),
                         time.strftime("%Y-%m-%d %H:%M:%S",
                                       time.gmtime(ts + 8 * 3600)))

    def test_midnight_and_hour_start_round_trip(self):
        wb_time.set_enabled(True)
        ts = 1791445200.0 + 13 * 3600 + 37 * 60 + 11
        mid = wb_time.midnight(ts)
        self.assertEqual(wb_time.iso_seconds(mid)[11:], "00:00:00")
        self.assertLessEqual(mid, ts)
        self.assertGreater(mid + 86400, ts)
        hour = wb_time.hour_start(ts)
        self.assertEqual(wb_time.iso_seconds(hour)[14:], "00:00")
        self.assertLessEqual(hour, ts)
        self.assertGreater(hour + 3600, ts)
        # The helpers are each other's inverse: formatting a bucket start and
        # re-parsing it must land on the same epoch.
        lt = wb_time.localtime(mid)
        self.assertEqual(wb_time.mktime(tuple(lt)), mid)

    def test_day_rolls_over_at_beijing_midnight(self):
        wb_time.set_enabled(True)
        # 2026-11-11 00:00 Beijing == 2026-11-10 16:00 UTC.
        base = utc_epoch(2026, 11, 10, 16, 0, 0)
        before = base - 1
        self.assertNotEqual(wb_time.day_key(before), wb_time.day_key(base))
        self.assertEqual(wb_time.iso_seconds(base)[11:], "00:00:00")

    def test_day_key_differs_from_utc_for_late_evening(self):
        # A row at 23:30 Beijing is still the previous day in UTC; the switch
        # is exactly what makes the two disagree, so this pins the intent.
        wb_time.set_enabled(True)
        ts = utc_epoch(2026, 11, 10, 15, 30, 0)
        self.assertEqual(wb_time.day_key(ts), "2026-11-10")
        self.assertEqual(time.strftime("%Y-%m-%d", time.gmtime(ts)), "2026-11-10")
        ts2 = utc_epoch(2026, 11, 10, 16, 30, 0)
        self.assertEqual(wb_time.day_key(ts2), "2026-11-11")
        self.assertEqual(time.strftime("%Y-%m-%d", time.gmtime(ts2)), "2026-11-10")

    def test_mktime_normalises_out_of_range_days(self):
        # The pre-existing callers step the day back past the 1st and rely on
        # mktime to normalise; calendar.timegm has to do the same.
        wb_time.set_enabled(True)
        mid = wb_time.mktime((2026, 3, 1 - 1, 0, 0, 0, 0, 0, -1))
        self.assertEqual(wb_time.iso_seconds(mid), "2026-02-28 00:00:00")
        nxt = wb_time.mktime((2026, 2, 28 + 1, 0, 0, 0, 0, 0, -1))
        self.assertEqual(wb_time.iso_seconds(nxt), "2026-03-01 00:00:00")


class ProxyBucketTests(unittest.TestCase):
    """wb_proxy 的日 / 小时键确实跟着开关走，而不只是 wb_time 自己一致。"""

    def setUp(self):
        os.environ.setdefault("ACCOUNTS_DIR",
                              tempfile.mkdtemp(prefix="wb-tz-acct-"))
        import wb_proxy as P
        self.P = P
        self._orig = wb_time.enabled()

    def tearDown(self):
        wb_time.set_enabled(self._orig)
        self.P._row_day_memo_clear()

    def test_local_day_key_follows_switch(self):
        P = self.P
        ts = utc_epoch(2026, 11, 10, 16, 30, 0)
        wb_time.set_enabled(False)
        P._row_day_memo_clear()
        off = P._local_day_key(ts)
        host_day = time.strftime("%Y-%m-%d", time.localtime(ts))
        wb_time.set_enabled(True)
        P._row_day_memo_clear()
        on = P._local_day_key(ts)
        self.assertEqual(off, host_day)
        self.assertEqual(on, "2026-11-11")

    def test_local_midnight_follows_switch(self):
        P = self.P
        ts = utc_epoch(2026, 11, 10, 16, 30, 0)
        wb_time.set_enabled(True)
        self.assertEqual(wb_time.iso_seconds(P._local_midnight(ts)),
                         "2026-11-11 00:00:00")
        self.assertEqual(wb_time.iso_seconds(P._local_hour_start(ts)),
                         "2026-11-11 00:00:00")

    def test_configure_timezone_applies_and_invalidates_memo(self):
        P = self.P
        wb_time.set_enabled(False)
        P._row_day_memo_clear()
        ts = utc_epoch(2026, 11, 10, 16, 30, 0)
        host_day = time.strftime("%Y-%m-%d", time.localtime(ts))
        self.assertEqual(P._local_day_key(ts), host_day)
        P.configure_timezone(True)
        self.assertTrue(wb_time.enabled())
        self.assertEqual(P._local_day_key(ts), "2026-11-11")
        P.configure_timezone(False)
        self.assertFalse(wb_time.enabled())
        self.assertEqual(P._local_day_key(ts), host_day)


if __name__ == "__main__":
    unittest.main(verbosity=2)
