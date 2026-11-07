"""国际版 DeepSeek 出站别名：base 显示，-sg 实际请求。

    python tests/_test_intl_model_alias.py

背景：国际版上游 2026-11 把对话模型换成了 `-sg` 这一支，官方客户端的模型目录
里只有 `-sg`；继续发旧 ID 的账号会收到 429 / code 6004（旧额度桶已空）。网关的
约定是：客户端与 /v1/models 仍使用逻辑 ID `deepseek-v4.1-flash`，API Key 权限也按
逻辑 ID 检查；只有真正发往 intl 上游的 body 改成 `-sg`。这里钉住这层边界，以及
聊天 / Responses / Anthropic 三条协议和用量记录。
"""
import gzip
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import wb_accounts as A  # noqa: E402
import wb_proxy as P  # noqa: E402
import wb_settings as S  # noqa: E402

LEGACY = "deepseek-v4.1-flash"
RENAMED = "deepseek-v4.1-flash-sg"


class AliasTests(unittest.TestCase):
    def test_intl_renames_the_legacy_id(self):
        self.assertEqual(P.intl_model_alias(LEGACY, "intl"), RENAMED)

    def test_renamed_id_is_stable(self):
        self.assertEqual(P.intl_model_alias(RENAMED, "intl"), RENAMED)

    def test_other_realms_keep_the_id(self):
        for realm in ("cn", "codearts"):
            self.assertEqual(P.intl_model_alias(LEGACY, realm), LEGACY)

    def test_apply_rewrites_the_body(self):
        payload = {"model": LEGACY}
        self.assertEqual(P.apply_intl_model_alias(payload, "intl"), RENAMED)
        self.assertEqual(payload["model"], RENAMED)

    def test_ingress_hook_is_a_noop(self):
        payload = {"model": LEGACY}
        P._apply_deepseek_flash_target(payload, "intl")
        self.assertEqual(payload["model"], LEGACY)

    def test_catalog_keeps_both_logical_ids(self):
        entries = P.merge_catalog(
            [(LEGACY, {}), (RENAMED, {})], realm="intl", extras=True)
        ids = [mid for mid, _meta in entries]
        self.assertIn(LEGACY, ids)
        self.assertIn(RENAMED, ids)

    def test_escape_hatch(self):
        previous = os.environ.get("WB_INTL_MODEL_ALIAS")
        os.environ["WB_INTL_MODEL_ALIAS"] = "0"
        try:
            self.assertEqual(P.intl_model_alias(LEGACY, "intl"), LEGACY)
        finally:
            if previous is None:
                os.environ.pop("WB_INTL_MODEL_ALIAS", None)
            else:
                os.environ["WB_INTL_MODEL_ALIAS"] = previous


class _Sock(object):
    def settimeout(self, value):
        pass


class _Response(object):
    def __init__(self):
        self.closed = False

    @property
    def fp(self):
        sock = _Sock()

        class Raw(object):
            _sock = sock

        class Fp(object):
            raw = Raw()

        return Fp()

    def close(self):
        self.closed = True


class _Pool(object):
    affinity = type("Affinity", (), {
        "unbind": lambda self, key: None,
        "demote": lambda self, key, uid=None: None})()

    def __init__(self, accounts):
        self.accounts = accounts

    def count_ready(self, realm, model=None):
        return len(self.accounts)

    def pick_for_session(self, realm, session_key=None, exclude=(),
                         model=None, page=None):
        for account in self.accounts:
            if account.uid not in exclude:
                return account
        return None

    def get(self, uid):
        return next((a for a in self.accounts if a.uid == uid), None)

    def apply_remaining_weights(self, weights=None):
        return weights or {}


class OutboundBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.account = A.Account(
            {"uid": "u-intl-boundary", "accessToken": "t", "realm": "intl"},
            os.path.join(self.tmp.name, "account.json"))
        self.pool = _Pool([self.account])
        self.old_pool = P.POOL
        P.POOL = self.pool
        self.addCleanup(setattr, P, "POOL", self.old_pool)
        patches = [
            mock.patch.object(P, "apply_daily_guards", return_value=0),
            mock.patch.object(P, "remaining_priority_enabled", return_value=False),
            mock.patch.object(P, "upstream_timeouts", return_value=(2, 2)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def open(self, payload):
        sent = {}

        def urlopen(req, timeout=30, proxy=""):
            sent["model"] = json.loads(req.data.decode("utf-8"))["model"]
            sent["x-model-id"] = dict(
                (k.lower(), v) for k, v in req.header_items()).get("x-model-id")
            return _Response()

        with mock.patch.object(A, "urlopen", side_effect=urlopen):
            response, account, _effort = P.open_upstream(
                payload, target_realm="intl")
        self.addCleanup(response.close)
        return response, account, sent

    def test_open_upstream_sends_the_alias_and_keeps_logical_input(self):
        payload = {"model": LEGACY, "messages": [
            {"role": "user", "content": "hi"}]}
        response, _account, sent = self.open(payload)
        self.assertEqual(payload["model"], LEGACY)
        self.assertEqual(sent["model"], RENAMED)
        self.assertEqual(response._wb_billed_model, RENAMED)

    def test_prebuilt_body_is_copied_before_alias(self):
        payload = {"model": LEGACY, "messages": [
            {"role": "user", "content": "hi"}]}
        forwarded = {"model": LEGACY, "messages": payload["messages"]}
        sent = {}

        def urlopen(req, timeout=30, proxy=""):
            sent["model"] = json.loads(req.data.decode("utf-8"))["model"]
            return _Response()

        with mock.patch.object(A, "urlopen", side_effect=urlopen):
            response, _account, _effort = P.open_upstream(
                payload, target_realm="intl", prebuilt_body=forwarded)
        self.addCleanup(response.close)
        self.assertEqual(forwarded["model"], LEGACY)
        self.assertEqual(sent["model"], RENAMED)

    def test_key_restriction_still_checks_the_logical_id(self):
        entry = {"id": "k1", "name": "base only", "models": [LEGACY]}
        self.assertTrue(S.key_allows_model(entry, LEGACY))
        self.assertFalse(S.key_allows_model(entry, RENAMED))

    def test_usage_records_requested_and_billed_models(self):
        response, _account, _sent = self.open(
            {"model": LEGACY, "messages": [{"role": "user", "content": "hi"}]})
        saved = []
        with mock.patch.object(P, "_persist_usage",
                               side_effect=lambda row, _label: saved.append(dict(row))):
            P.record_upstream_usage(
                response, LEGACY,
                {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3})
        self.assertEqual(saved[0]["model"], RENAMED)
        self.assertEqual(saved[0]["requested_model"], LEGACY)


class BridgeLogicalModelTests(unittest.TestCase):
    def test_chat_aggregate_does_not_echo_the_alias(self):
        stream = iter([
            b'data: {"id":"c1","model":"deepseek-v4.1-flash-sg","choices":['
            b'{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n',
            b"data: [DONE]\n",
        ])
        obj = P.aggregate_stream(stream, LEGACY, None)
        self.assertEqual(obj["model"], LEGACY)

    def test_anthropic_stream_keeps_the_logical_model(self):
        stream = iter([
            b'data: {"id":"c1","model":"deepseek-v4.1-flash-sg","choices":['
            b'{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n',
            b"data: [DONE]\n",
        ])
        frames = list(P.stream_messages_events(stream, LEGACY, {}))
        start = next(f for f in frames if b"event: message_start" in f)
        payload = json.loads(start.decode("utf-8").split("data: ", 1)[1])
        self.assertEqual(payload["message"]["model"], LEGACY)

    def test_responses_stream_keeps_the_logical_model(self):
        stream = iter([
            b'data: {"id":"c1","model":"deepseek-v4.1-flash-sg","choices":['
            b'{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n',
            b"data: [DONE]\n",
        ])
        frames = list(P.stream_responses_events(stream, LEGACY, {}))
        created = next(f for f in frames if b"event: response.created" in f)
        payload = json.loads(created.decode("utf-8").split("data: ", 1)[1])
        self.assertEqual(payload["response"]["model"], LEGACY)


class ProbeAccountTests(unittest.TestCase):
    def account(self, product):
        return A.Account({"uid": "u-intl-test", "nickname": "intl-test",
                          "domain": "www.workbuddy.ai", "realm": "intl",
                          "product": product, "accessToken": "opaque-token"},
                         "")

    def sent_model(self, product, model):
        seen = {}

        def fake_urlopen(req, timeout=None, proxy=None):
            raw = req.data
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            seen["model"] = json.loads(raw.decode("utf-8"))["model"]
            raise urllib.error.HTTPError(
                req.full_url, 400, "stop", {}, io.BytesIO(b'{"code":0}'))

        previous = A.urlopen
        A.urlopen = fake_urlopen
        try:
            seen["row"] = P.probe_account(self.account(product), model)
        finally:
            A.urlopen = previous
        return seen.get("model"), seen.get("row")

    def test_panel_test_sends_the_renamed_id(self):
        sent, row = self.sent_model("workbuddy", LEGACY)
        self.assertEqual(sent, RENAMED)
        self.assertEqual(row["model"], LEGACY)

    def test_panel_test_renames_the_gzipped_cli_body(self):
        sent, row = self.sent_model("cli", LEGACY)
        self.assertEqual(sent, RENAMED)
        self.assertEqual(row["model"], LEGACY)


if __name__ == "__main__":
    unittest.main()
