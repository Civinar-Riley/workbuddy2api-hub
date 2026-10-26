"""熔断要「连续」才算数，本机断网不能算在账号头上。

线上两个现象，一个比一个难解释：

  1. 「明明测了能用，它还熔断」——`fails` 老实现只加不减（只有成功才归零），于是
     几小时前的一次 502 + 昨天的一次超时 + 刚才一次抖动凑成「连续 3 次」，账号被
     关 30 分钟；而且下一个窗口按 1800 → 3600 → 7200… 翻倍，越关越久。
  2. 「主机没网当成上游出问题」——DNS 解析不了 / 路由不可达也会逐账号记一笔并冷却
     60 秒；断网那一分钟里池子被逐个走一遍，等网络恢复，账号全在冷却里。

这个套件钉住修法：失败只在 FAIL_WINDOW 内才算连续；「本机没网」这一类失败单独
识别、一个账号都不记，并按「本机网络不可用」上报（附 gateway_hint）。

    python tests/_test_breaker_fairness.py
"""
import io
import json
import os
import socket
import ssl
import sys
import time
import types
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolated_dirs import isolated_data_dirs    # noqa: E402

_TMP = isolated_data_dirs("wb-breaker-fair-")

import wb_accounts as A        # noqa: E402
import wb_proxy as P           # noqa: E402


def account(uid="aaaa1111-bbbb-cccc-dddd-eeeeffff0000", realm="intl", **overrides):
    data = {"uid": uid, "accessToken": "x.y.z", "nickname": uid[:8],
            "enabled": True, "realm": realm}
    data.update(overrides)
    return A.Account(data)


class StrikeWindowTests(unittest.TestCase):
    """连续失败要有时间含义：隔得久的两笔不能凑成一次熔断。"""

    def setUp(self):
        self.window = A.FAIL_WINDOW

    def tearDown(self):
        A.FAIL_WINDOW = self.window

    def test_three_in_a_row_trip_the_breaker(self):
        acc = account()
        for _ in range(A.BREAKER_THRESHOLD):
            acc.note_failure("HTTP 502")
        self.assertEqual(acc.fails, A.BREAKER_THRESHOLD)
        self.assertGreater(acc.throttle_wait(), 0, "阈值内三次失败应当熔断")

    def test_stale_strike_restarts_the_count(self):
        acc = account()
        acc.note_failure("HTTP 502")
        acc.note_failure("HTTP 502")
        self.assertEqual(acc.fails, 2)
        # 把上一次失败挪到窗口之外：这一笔是新的一轮，不是「连续第 3 次」。
        acc.last_fail_at = time.time() - (A.FAIL_WINDOW + 1)
        acc.note_failure("HTTP 502")
        self.assertEqual(acc.fails, 1, "窗口外的旧账不该累计")
        self.assertEqual(acc.throttle_wait(), 0, "不该因为几小时前的一次失败被熔断")

    def test_window_applies_to_unknown_failures_too(self):
        acc = account()
        acc.note_unknown_failure("connection: URLError")
        acc.note_unknown_failure("connection: URLError")
        acc.last_fail_at = time.time() - (A.FAIL_WINDOW + 1)
        acc.note_unknown_failure("connection: URLError")
        self.assertEqual(acc.fails, 1)
        self.assertEqual(acc.degrade_count, 1, "降权计数同样按窗口重算")

    def test_success_resets_the_window(self):
        acc = account()
        acc.note_failure("HTTP 502")
        acc.note_success()
        self.assertEqual(acc.last_fail_at, 0.0)
        acc.note_failure("HTTP 502")
        self.assertEqual(acc.fails, 1, "成功之后重新从 1 数起")

    def test_zero_window_keeps_the_old_accumulating_behaviour(self):
        A.FAIL_WINDOW = 0
        acc = account()
        acc.note_failure("HTTP 502")
        acc.last_fail_at = time.time() - 86400
        acc.note_failure("HTTP 502")
        acc.note_failure("HTTP 502")
        self.assertEqual(acc.fails, 3, "WB_FAIL_WINDOW=0 时保持旧的累计语义")

    def test_public_exposes_the_ladder(self):
        acc = account()
        acc.note_failure("HTTP 502")
        view = acc.public()
        self.assertEqual(view["fails"], 1)
        self.assertEqual(view["failWindow"], int(A.FAIL_WINDOW))
        self.assertIsNotNone(view["lastFailFor"])


class LocalNetworkClassifierTests(unittest.TestCase):
    def test_wrapped_windows_transport_errors_are_transient(self):
        for code in (10053, 10054, 10060):
            for reason in (OSError(code, "连接已中断"),
                           "[WinError %d] 连接已中断" % code):
                exc = urllib.error.URLError(reason)
                with self.subTest(code=code, reason=repr(reason)):
                    self.assertFalse(P.looks_like_local_network(exc))
                    self.assertTrue(P.is_transient(exc))
        exc = urllib.error.URLError(ConnectionResetError("连接已中断"))
        self.assertTrue(P.is_transient(exc))

    def test_connection_refused_is_not_transient(self):
        # Refused means nothing is listening (typically a dead per-account
        # proxy): it must keep the cooldown path, not the retry path.
        for reason in (ConnectionRefusedError(10061, "目标计算机积极拒绝"),
                       ConnectionRefusedError(111, "Connection refused"),
                       "[WinError 10061] 目标计算机积极拒绝"):
            exc = urllib.error.URLError(reason)
            with self.subTest(reason=repr(reason)):
                self.assertFalse(P.looks_like_local_network(exc))
                self.assertFalse(P.is_transient(exc))

    def test_numeric_network_codes_match_whole_numbers(self):
        for code in (100, 101, 113, 10050, 10051, 10065, 11001):
            exc = urllib.error.URLError(OSError(code, "网络不可达"))
            with self.subTest(local_code=code):
                self.assertTrue(P.looks_like_local_network(exc))
        for code in (1000, 10054, 100540, 1010, 1130, 13):
            exc = urllib.error.URLError(OSError(code, "error"))
            with self.subTest(other_code=code):
                self.assertFalse(P.looks_like_local_network(exc))

    def test_http_errors_without_response_are_not_os_failures(self):
        # HTTPError delegates missing attributes to its response on Python
        # 3.9. With fp=None, probing errno/winerror used to raise KeyError.
        class DelegatingHTTPError(urllib.error.HTTPError):
            # Model that Linux 3.9 lookup on every test platform, including
            # Windows versions whose OSError slots return None instead.
            def __getattribute__(self, name):
                if name in ("errno", "winerror"):
                    raise KeyError("file")
                return super().__getattribute__(name)

        for error_type in (urllib.error.HTTPError, DelegatingHTTPError):
            for status in (401, 429, 502):
                error = error_type("https://up", status, "HTTP failure", {}, None)
                for exc in (error, urllib.error.URLError(error)):
                    with self.subTest(status=status, error_type=error_type.__name__,
                                      wrapped=exc is not error):
                        self.assertFalse(P.looks_like_local_network(exc))
                        self.assertEqual(P.is_transient(exc), status >= 500)

    def test_host_side_failures_are_recognised(self):
        cases = [
            socket.gaierror(-3, "Temporary failure in name resolution"),
            urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")),
            urllib.error.URLError(OSError(101, "Network is unreachable")),
            urllib.error.URLError(OSError("No route to host")),
            OSError("[WinError 10051] A socket operation was attempted to an unreachable network"),
        ]
        for exc in cases:
            self.assertTrue(P.looks_like_local_network(exc), repr(exc))

    def test_upstream_side_failures_are_not(self):
        cases = [
            urllib.error.URLError("SSL: UNEXPECTED_EOF_WHILE_READING"),
            urllib.error.URLError("connection reset by peer"),
            urllib.error.URLError("timed out"),
            urllib.error.HTTPError("https://up", 502, "bad gateway", {}, None),
            urllib.error.HTTPError("https://up", 429, "rate limited", {}, None),
        ]
        for exc in cases:
            self.assertFalse(P.looks_like_local_network(exc), repr(exc))

    def test_hint_explains_it(self):
        hint = P.gateway_hint(503, "本机网络不可用：DNS 解析失败：（-3, 'Temporary failure'）")
        self.assertIn("no working network", hint)
        self.assertIn("not penalised", hint)


class ChatPathTests(unittest.TestCase):
    """整条路：本机断网时一个账号都不许记账，且要按网络问题上报。"""

    def setUp(self):
        self.account = account()
        self.pool = _FakePool(self.account)
        self.original_pool = P.POOL
        self.original_urlopen = A.urlopen
        P.POOL = self.pool

    def tearDown(self):
        P.POOL = self.original_pool
        A.urlopen = self.original_urlopen

    def _run(self, failure):
        def fake_urlopen(req, timeout=30, proxy=""):
            raise failure
        A.urlopen = fake_urlopen
        with mock.patch.object(P.time, "sleep", lambda *_: None):
            return P.open_upstream({"model": "deepseek-v4.1-flash",
                                    "messages": [{"role": "user", "content": "hi"}]},
                                   target_realm="intl")

    def test_offline_host_does_not_charge_the_account(self):
        with self.assertRaises(P.LocalNetworkError) as ctx:
            self._run(urllib.error.URLError(socket.gaierror(-3, "Temporary failure in name resolution")))
        self.assertIn("本机网络不可用", str(ctx.exception))
        self.assertEqual(self.account.fails, 0, "断网不该记在账号头上")
        self.assertEqual(self.account.last_fail_at, 0.0)
        self.assertEqual(self.account.throttle_wait(), 0, "账号不该进冷却")

    def test_dns_failure_by_type_is_enough(self):
        with self.assertRaises(P.LocalNetworkError):
            self._run(socket.gaierror(11001, "getaddrinfo failed"))
        self.assertEqual(self.account.fails, 0)

    def test_upstream_transport_failure_still_charges(self):
        """对照组：上游丢连接照旧记账，否则熔断就形同虚设。"""
        with self.assertRaises(Exception) as ctx:
            self._run(urllib.error.URLError("SSL: UNEXPECTED_EOF_WHILE_READING"))
        self.assertNotIsInstance(ctx.exception, P.LocalNetworkError)
        self.assertGreaterEqual(self.account.fails, 1, "上游侧失败仍然要记账")

    def test_five_hundred_still_charges(self):
        with self.assertRaises(Exception) as ctx:
            self._run(urllib.error.HTTPError("https://up", 502, "bad gateway", {}, None))
        self.assertNotIsInstance(ctx.exception, P.LocalNetworkError)
        self.assertGreaterEqual(self.account.fails, 1)

    def test_model_scoped_500_does_not_charge_the_account(self):
        body = io.BytesIO(
            b'{"code":11134,"msg":"the model provider is temporarily unavailable"}')
        failure = urllib.error.HTTPError(
            "https://up", 500, "Internal Server Error", {}, body)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._run(failure)
        self.assertEqual(self.account.fails, 0, "模型级 5xx 不该记在账号头上")
        self.assertEqual(self.account.breaker_until, 0.0)
        self.assertIn("11134",
                      P.read_http_error(ctx.exception).decode("utf-8"))

    def test_plain_500_still_charges_the_account(self):
        body = io.BytesIO(b'{"code":99999,"msg":"boom"}')
        failure = urllib.error.HTTPError(
            "https://up", 500, "Internal Server Error", {}, body)
        with self.assertRaises(urllib.error.HTTPError):
            self._run(failure)
        self.assertGreaterEqual(self.account.fails, 1)

    def test_model_scoped_penalty_escape_hatch(self):
        body = io.BytesIO(b'{"code":11134,"msg":"model unavailable"}')
        failure = urllib.error.HTTPError(
            "https://up", 500, "Internal Server Error", {}, body)
        with mock.patch.dict(os.environ, {"WB_MODEL_SCOPED_ERRORS_PENALIZE": "1"}):
            with self.assertRaises(urllib.error.HTTPError):
                self._run(failure)
        self.assertGreaterEqual(self.account.fails, 1)

    def test_generic_failure_is_logged_with_the_account(self):
        """通用失败分支以前不写日志：账号被冷却，可事后查不到是谁、为什么。"""
        lines = []
        with mock.patch.object(P, "log", lambda msg, **kw: lines.append(str(msg))):
            with self.assertRaises(Exception):
                self._run(ValueError("boom from the request body"))
        self.assertTrue(any(self.account.uid[:8] in line and "cooling" in line
                            for line in lines), lines)
        self.assertTrue(any("boom from the request body" in line for line in lines), lines)
        self.assertGreaterEqual(self.account.fails, 1)


class ProbeTests(unittest.TestCase):
    """一键测试里的连通性探针：只连写死的 https 上游，且拒绝私有/环回地址。"""

    def test_rejects_non_https_upstream(self):
        cfg = dict(A.get_realm_config("intl"))
        cfg["chat_upstream"] = "http://insecure.example.com"
        with mock.patch.object(A, "get_realm_config", lambda realm: cfg):
            info = P.probe_local_network("intl")
        self.assertFalse(info["ok"])
        self.assertIn("https", info["error"])

    def test_refuses_private_address_before_connecting(self):
        cfg = dict(A.get_realm_config("intl"))
        cfg["chat_upstream"] = "https://upstream.example.com"
        fake = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))]
        with mock.patch.object(A, "get_realm_config", lambda realm: cfg), \
                mock.patch.object(P.socket, "getaddrinfo", lambda *a, **k: fake), \
                mock.patch.object(P.socket, "socket") as sock:
            info = P.probe_local_network("intl")
        self.assertFalse(info["ok"])
        self.assertIn("非公网地址", info["error"])
        self.assertFalse(sock.called, "校验没过就不该去连")

    def test_accepts_fake_ip_from_local_dns_proxy(self):
        """Clash / sing-box 的 fake-IP 是本地代理的占位地址，不是内网目标。

        开着 fake-IP 时所有域名都解析到 198.18.0.0/15；TCP 握手会由本机 TUN 按
        域名转发出去，探针必须照常放行，否则一键测试永远显示「本机网络不通」。
        """
        cfg = dict(A.get_realm_config("intl"))
        cfg["chat_upstream"] = "https://upstream.example.com"
        fake = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 ("198.18.0.11", 443))]
        with mock.patch.object(A, "get_realm_config", lambda realm: cfg), \
                mock.patch.object(P.socket, "getaddrinfo", lambda *a, **k: fake), \
                mock.patch.object(P.socket, "socket") as sock:
            info = P.probe_local_network("intl")
        self.assertTrue(info["ok"], info)
        self.assertEqual(info["ip"], "198.18.0.11")
        self.assertTrue(sock.return_value.__enter__.return_value.connect.called,
                        "fake-IP 应当继续走 TCP 握手，而不是提前拒绝")

    def test_reports_dns_failure_as_offline(self):
        cfg = dict(A.get_realm_config("intl"))
        cfg["chat_upstream"] = "https://upstream.example.com"
        with mock.patch.object(A, "get_realm_config", lambda realm: cfg), \
                mock.patch.object(P.socket, "getaddrinfo",
                                  side_effect=socket.gaierror(-3, "Temporary failure in name resolution")):
            info = P.probe_local_network("intl")
        self.assertFalse(info["ok"])
        self.assertIn("DNS", info["error"])


class TestAllRouteTests(unittest.TestCase):
    """一键测试的聚合结果：网络行 + 每账号一行 + 汇总。"""

    def setUp(self):
        self.account = account(uid="cn000001-0000-0000-0000-000000000000", realm="cn")
        self.pool = _FakePool(self.account)
        self.original_pool = P.POOL
        P.POOL = self.pool

    def tearDown(self):
        P.POOL = self.original_pool

    def _call(self, payload, rows, network):
        handler = types.SimpleNamespace(
            _json=lambda code, body: {"code": code, "body": body})
        # 本仓库的账号类面板出口统一走 _json_panel（`_redact_account_names` + `_json`）。
        # 桩里补上同一语义，才能让「一键测试」的返回体走与生产一致的路径；断言本身
        # 不看手机号，脱敏对这里的昵称是恒等变换。
        handler._json_panel = lambda code, body: {
            "code": code, "body": P._redact_account_names(body)}
        with mock.patch.object(P, "probe_account", lambda acc, model=None, timeout=30: dict(rows[0])), \
                mock.patch.object(P, "probe_local_network", lambda realm: dict(network)):
            return P.Handler._route_accounts_test_all(handler, payload)

    def test_summary_and_rows(self):
        rows = [{"uid": self.account.uid, "nickname": "某账号", "realm": "cn", "model": "m",
                 "ok": True, "elapsed_ms": 12, "status": None, "reply": "hi",
                 "error": "", "network": False}]
        network = {"realm": "cn", "url": "https://up", "host": "up", "ip": "1.2.3.4",
                   "ok": True, "dns_ms": 3, "tcp_ms": 8, "error": ""}
        out = self._call({"realm": "cn"}, rows, network)
        self.assertEqual(out["code"], 200)
        body = out["body"]
        self.assertTrue(body["network"]["ok"])
        self.assertEqual(body["summary"]["tested"], 1)
        self.assertEqual(body["summary"]["passed"], 1)
        self.assertFalse(body["summary"]["networkDown"])
        self.assertEqual(body["results"][0]["uid"], self.account.uid)

    def test_offline_host_is_flagged_in_the_summary(self):
        rows = [{"uid": self.account.uid, "nickname": "某账号", "realm": "cn", "model": "m",
                 "ok": False, "elapsed_ms": 5, "status": None, "reply": "",
                 "error": "本机网络不可用：DNS 解析失败", "network": True}]
        network = {"realm": "cn", "url": "https://up", "host": "up", "ip": "",
                   "ok": False, "dns_ms": None, "tcp_ms": None, "error": "DNS 解析失败：gaierror"}
        body = self._call({"realm": "cn"}, rows, network)["body"]
        self.assertTrue(body["summary"]["networkDown"], "断网要在汇总里标出来")
        self.assertEqual(body["summary"]["failed"], 1)
        self.assertTrue(body["results"][0]["network"], "行上要标明是网络问题")

    def test_realm_falls_back_to_current(self):
        network = {"realm": "intl", "url": "https://up", "host": "up", "ip": "", "ok": True,
                   "dns_ms": 1, "tcp_ms": 1, "error": ""}
        # account 是 cn；请求里写一个非法区域 → 回落到 CURRENT_REALM（intl），
        # 于是这次没有可测账号，但网络行仍然返回。
        body = self._call({"realm": "bogus"}, [], network)["body"]
        self.assertEqual(body["summary"]["tested"], 0)
        self.assertEqual(body["summary"]["realm"], P.CURRENT_REALM)


class SoftLadderTests(unittest.TestCase):
    """软限流的两条阶梯同样要有时间窗（老实现只加不减、只有成功才归零）。"""

    def setUp(self):
        self.window = A.FAIL_WINDOW

    def tearDown(self):
        A.FAIL_WINDOW = self.window

    def test_account_ladder_restarts_after_the_window(self):
        acc = account()
        for _ in range(3):
            acc.note_soft_rate("HTTP 429 (account soft rate)")
        self.assertEqual(acc.soft_streak, 3)
        self.assertEqual(acc.note_soft_rate("HTTP 429 (account soft rate)"), 4800.0,
                         "窗口内第 4 次：600 * 2**3")
        acc.last_soft_at = time.time() - (A.FAIL_WINDOW + 1)
        self.assertEqual(acc.note_soft_rate("HTTP 429 (account soft rate)"), 600.0,
                         "隔了许久再来一次，该回到第一档而不是继续抬价")
        self.assertEqual(acc.soft_streak, 1)

    def test_model_ladder_restarts_after_the_window(self):
        acc = account()
        for _ in range(3):
            acc.note_unscoped_rate("deepseek-v4.1-flash")
        self.assertEqual(acc.unscoped_streak, 3)
        self.assertEqual(acc.note_unscoped_rate("deepseek-v4.1-flash"), 480.0,
                         "窗口内第 4 次：60 * 2**3")
        acc.last_soft_at = time.time() - (A.FAIL_WINDOW + 1)
        self.assertEqual(acc.note_unscoped_rate("deepseek-v4.1-flash"), 60.0)
        self.assertEqual(acc.unscoped_streak, 1)

    def test_success_resets_the_soft_window(self):
        acc = account()
        acc.note_soft_rate("HTTP 429 (account soft rate)")
        acc.note_success()
        self.assertEqual(acc.last_soft_at, 0.0)
        self.assertEqual(acc.soft_streak, 0)
        self.assertEqual(acc.note_soft_rate("HTTP 429 (account soft rate)"), 600.0)

    def test_zero_window_keeps_the_old_ladder(self):
        A.FAIL_WINDOW = 0
        acc = account()
        acc.note_soft_rate("HTTP 429 (account soft rate)")
        acc.last_soft_at = time.time() - 86400
        acc.note_soft_rate("HTTP 429 (account soft rate)")
        self.assertEqual(acc.soft_streak, 2, "WB_FAIL_WINDOW=0 时保持旧的累计语义")


class LeftTimeFormatTests(unittest.TestCase):
    """剩余时间要说人话：还剩几十秒时写「0 min」会被读成「早该能用了」。"""

    def test_seconds_minutes_hours(self):
        self.assertEqual(A._human_left(42), "42s")
        self.assertEqual(A._human_left(90), "1m30s")
        self.assertEqual(A._human_left(600), "10m")
        self.assertEqual(A._human_left(7200), "2h")
        self.assertEqual(A._human_left(9000), "2h30m")
        self.assertEqual(A._human_left(-5), "0s")

    def test_reason_never_says_zero_minutes(self):
        acc = account()
        acc.cooldown_until = time.time() + 42
        acc.soft_streak = 3
        reason = acc.unavailable_reason(model="deepseek-v4.1-flash")
        self.assertIn("软限流冷却", reason)
        self.assertIn("剩余 42s", reason)
        self.assertNotIn("0 min", reason, "「剩余 0 min」正是那条误导人的日志")
        self.assertIn("连续第 3 次", reason, "升级到第几档要看得出来")

    def test_model_window_shows_its_own_streak(self):
        acc = account()
        acc.model_cooldowns["deepseek-v4.1-flash"] = time.time() + 90
        acc.unscoped_streak = 2
        reason = acc.unavailable_reason(model="deepseek-v4.1-flash")
        self.assertIn("模型 deepseek-v4.1-flash 冷却", reason)
        self.assertIn("剩余 1m30s", reason)
        self.assertIn("连续第 2 次", reason)


class TLSVerifyTests(unittest.TestCase):
    """A bad certificate chain is a configuration error, not a transient fault."""

    def test_error_shape_is_recognised_through_urlerror(self):
        exc = ssl.SSLCertVerificationError(
            1, "certificate verify failed: self-signed certificate in certificate chain")
        self.assertTrue(A.is_tls_verify_error(exc))
        wrapped = urllib.error.URLError(exc)
        self.assertTrue(A.is_tls_verify_error(wrapped))
        self.assertFalse(A._retryable(wrapped),
                         "证书错误重试三次只会把同一个失败放大")

    def test_http_json_does_not_retry_a_certificate_error(self):
        calls = []

        def fake_urlopen(req, timeout=30, proxy=""):
            calls.append(1)
            raise urllib.error.URLError(ssl.SSLCertVerificationError(
                1, "certificate verify failed: self-signed certificate in certificate chain"))

        old = A.urlopen
        A.urlopen = fake_urlopen
        try:
            with self.assertRaises(A.TLSVerifyError) as ctx:
                A.http_json("https://example.invalid/v2/plugin/auth/state",
                            data=b"{}", retries=3)
        finally:
            A.urlopen = old
        self.assertEqual(len(calls), 1, "证书错误应当一次就停")
        self.assertIn("WB_CA_BUNDLE", str(ctx.exception))

    def test_open_upstream_stops_without_charging_the_account(self):
        acc = account()
        old_pool, old_urlopen = P.POOL, A.urlopen
        P.POOL = _FakePool(acc)

        def fake_urlopen(req, timeout=30, proxy=""):
            raise urllib.error.URLError(ssl.SSLCertVerificationError(
                1, "certificate verify failed: self-signed certificate in certificate chain"))

        A.urlopen = fake_urlopen
        try:
            with self.assertRaises(A.TLSVerifyError) as ctx:
                P.open_upstream({"model": "deepseek-v4.1-flash",
                                 "messages": [{"role": "user", "content": "hi"}]},
                                target_realm="intl")
        finally:
            P.POOL, A.urlopen = old_pool, old_urlopen
        self.assertIn("WB_CA_BUNDLE", str(ctx.exception))
        self.assertEqual(acc.fails, 0)
        self.assertEqual(acc.cooldown_until, 0.0)


class RefreshOn401Tests(unittest.TestCase):
    """401 先刷新一次 token 再换号；刷新成功的同一个号要优先重试。"""

    def setUp(self):
        self.account = account()
        self.pool = _FakePool(self.account)
        self.old_pool, self.old_urlopen = P.POOL, A.urlopen
        P.POOL = self.pool
        self.calls = 0
        self.refreshes = 0

        def refresh():
            self.refreshes += 1
            return True

        self.account.refresh = refresh

    def tearDown(self):
        P.POOL, A.urlopen = self.old_pool, self.old_urlopen

    def _run(self, urlopen):
        A.urlopen = urlopen
        with mock.patch.object(P, "_apply_stream_idle_timeout", lambda *a: None), \
                mock.patch.object(P.time, "sleep", lambda *_: None):
            return P.open_upstream(
                {"model": "deepseek-v4.1-flash",
                 "messages": [{"role": "user", "content": "hi"}]},
                target_realm="intl")

    def test_401_refreshes_and_retries_the_same_account(self):
        class Resp(object):
            def close(self):
                pass

        def fake_urlopen(req, timeout=30, proxy=""):
            self.calls += 1
            if self.calls == 1:
                raise urllib.error.HTTPError(
                    req.full_url, 401, "Unauthorized", {}, None)
            return Resp()

        resp, acc, _ = self._run(fake_urlopen)
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.refreshes, 1)
        self.assertIs(acc, self.account)
        self.assertIsInstance(resp, Resp)

    def test_401_after_refresh_does_not_refresh_in_a_loop(self):
        def fake_urlopen(req, timeout=30, proxy=""):
            self.calls += 1
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", {}, None)

        with self.assertRaises(urllib.error.HTTPError):
            self._run(fake_urlopen)
        self.assertEqual(self.calls, 2, "刷新后只重试一次，第二次 401 就换号/收尾")
        self.assertEqual(self.refreshes, 1)


class _FakePool(object):
    """够 open_upstream / 测试路由用的小池子。"""

    def __init__(self, *accounts):
        self.accounts = list(accounts)
        self.affinity = types.SimpleNamespace(unbind=lambda key: None,
                                              demote=lambda key, uid=None: None)

    def count_ready(self, realm, model=None):
        return len([a for a in self.accounts if a.realm == realm and a.enabled])

    def pick_for_session(self, realm, session_key=None, exclude=(), model=None, page=None):
        for acc in self.accounts:
            if acc.realm == realm and acc.enabled and acc.uid not in set(exclude or ()):
                return acc
        return None

    def list_public(self, realm=None):
        return [a.public() for a in self.accounts if not realm or a.realm == realm]

    def apply_remaining_weights(self, weights=None):
        """当前 main 的调度接口：这里不需要权重，收下就丢。"""
        return None

    def get(self, uid):
        for acc in self.accounts:
            if acc.uid == uid:
                return acc
        return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
