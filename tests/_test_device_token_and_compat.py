"""Per-account device tokens and the opt-in desktop chat compatibility profile."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_accounts
import wb_identity
import wb_settings


def clear_device_token_cache():
    with wb_accounts._DEVICE_TOKEN_LOCK:
        wb_accounts._DEVICE_TOKEN_CACHE.update(
            {"key": None, "token": "", "at": 0.0}
        )


def make_account(directory, **extra):
    data = {
        "uid": "uid-device",
        "accessToken": "fixture-token",
        "realm": "cn",
        "product": "workbuddy",
    }
    data.update(extra)
    return wb_accounts.Account(
        data, os.path.join(directory, data["uid"] + ".json")
    )


class AccountDeviceTokenTests(unittest.TestCase):
    def setUp(self):
        clear_device_token_cache()
        self.temp = tempfile.TemporaryDirectory(prefix="device-token-")
        self.directory = self.temp.name

    def tearDown(self):
        clear_device_token_cache()
        self.temp.cleanup()

    def test_account_token_round_trips_but_public_stays_redacted(self):
        account = make_account(self.directory, deviceToken=" account-token ")
        path = account.save(self.directory)
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)
        self.assertEqual(stored["deviceToken"], "account-token")

        reloaded = wb_accounts.Account(stored, path)
        self.assertEqual(reloaded.device_token, "account-token")
        public = reloaded.public()
        self.assertTrue(public["hasDeviceToken"])
        self.assertNotIn("deviceToken", public)
        self.assertNotIn("device_token", public)
        self.assertNotIn("account-token", json.dumps(public, ensure_ascii=False))

    def test_export_import_preserves_device_token_only_with_secrets(self):
        account = make_account(self.directory, accessToken="aaa.bbb.ccc", deviceToken="device-fixture")
        exported = wb_accounts.build_export_document([account], include_secrets=True)["accounts"][0]
        self.assertEqual(wb_accounts.normalise_import_row(exported)["deviceToken"], "device-fixture")
        redacted = wb_accounts.build_export_document([account], include_secrets=False)["accounts"][0]
        self.assertNotIn("deviceToken", redacted)

    def test_empty_token_is_removed_from_disk(self):
        account = make_account(
            self.directory, device_token=""
        )
        self.assertEqual(account.device_token, "")
        path = account.save(self.directory)
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)
        self.assertNotIn("deviceToken", stored)
        self.assertNotIn("device_token", stored)
        self.assertFalse(wb_accounts.Account(stored, path).public()["hasDeviceToken"])

    def test_device_token_precedence_and_identity_boundaries(self):
        token_file = os.path.join(self.directory, "device-token.txt")
        with open(token_file, "w", encoding="utf-8") as fh:
            fh.write("file-token\n")
        wb_settings.set_upstream_config(
            self.directory,
            {"device_token": "literal-token", "device_token_file": token_file},
        )

        account = make_account(self.directory, deviceToken="account-token")
        self.assertEqual(account._device_token(), "account-token")
        self.assertEqual(
            account.headers()["X-Device-Token"], "account-token"
        )

        fallback = make_account(self.directory, uid="uid-fallback")
        self.assertEqual(fallback._device_token(), "literal-token")
        wb_settings.set_upstream_config(
            self.directory,
            {"device_token": "", "device_token_file": token_file},
        )
        self.assertEqual(fallback._device_token(), "file-token")

        intl = make_account(
            self.directory,
            uid="uid-intl",
            realm="intl",
            deviceToken="intl-account-token",
        )
        cli = make_account(
            self.directory,
            uid="uid-cli",
            product="cli",
            deviceToken="cli-account-token",
        )
        self.assertNotIn("X-Device-Token", intl.headers())
        self.assertNotIn("X-Device-Token", cli.headers())

    def test_reload_and_status_are_redacted_diagnostics(self):
        token_file = os.path.join(self.directory, "device-token.txt")
        old_token = "device-secret-old-8f2c1e"
        new_token = "device-secret-new-7a4b9d"
        with open(token_file, "w", encoding="utf-8") as fh:
            fh.write(old_token)
        wb_settings.set_upstream_config(
            self.directory, {"device_token_file": token_file}
        )

        with mock.patch.object(wb_accounts.time, "time", return_value=1000.0):
            self.assertEqual(
                wb_accounts.resolve_device_token(self.directory), old_token
            )
            with open(token_file, "w", encoding="utf-8") as fh:
                fh.write(new_token)
            status = wb_accounts.device_token_status(self.directory)
        self.assertEqual(status, {
            "source": "file",
            "configured_file": token_file,
            "loaded": True,
            "length": len(old_token),
            "cached_at": 1000,
        })
        self.assertNotIn(old_token, json.dumps(status))

        with mock.patch.object(wb_accounts.time, "time", return_value=1001.0):
            self.assertEqual(
                wb_accounts.reload_device_token(self.directory), new_token
            )
            status = wb_accounts.device_token_status(self.directory)
        self.assertEqual(status["length"], len(new_token))
        self.assertEqual(status["cached_at"], 1001)
        self.assertNotIn(new_token, json.dumps(status))

        wb_settings.set_upstream_config(
            self.directory,
            {"device_token": "literal-token", "device_token_file": token_file},
        )
        self.assertEqual(
            wb_accounts.device_token_status(self.directory)["source"], "literal"
        )


class DesktopChatCompatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="desktop-compat-")
        self.directory = self.temp.name
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("WB_DESKTOP_CHAT_COMPAT", None)
        clear_device_token_cache()

    def tearDown(self):
        self.env.stop()
        clear_device_token_cache()
        self.temp.cleanup()

    def assert_compat_headers(self, headers):
        self.assertEqual(headers["X-Agent-Runtime"], "local-agent")
        self.assertEqual(
            headers["baggage"],
            "buddyId=,buddyName=,codebuddy.session_id=%s,"
            "codebuddy.conversation_request_id=%s" % (
                headers["X-Conversation-ID"],
                headers["X-Conversation-Request-ID"],
            ),
        )
        self.assertNotIn("x-stainless-timeout", headers)

    def test_environment_switch_adds_runtime_and_baggage(self):
        account = wb_accounts.Account({
            "uid": "uid-env",
            "accessToken": "fixture-token",
            "realm": "cn",
            "product": "workbuddy",
        })
        os.environ["WB_DESKTOP_CHAT_COMPAT"] = "1"
        self.assert_compat_headers(account.headers())

    def test_persisted_switch_reaches_account_headers(self):
        self.assertFalse(wb_settings.desktop_chat_compat(self.directory))
        self.assertFalse(
            wb_identity.desktop_chat_compat_enabled(self.directory)
        )
        self.assertTrue(
            wb_settings.set_desktop_chat_compat(self.directory, True)
        )
        self.assertTrue(
            wb_identity.desktop_chat_compat_enabled(self.directory)
        )

        account = make_account(self.directory)
        self.assert_compat_headers(account.headers())

        os.environ["WB_DESKTOP_CHAT_COMPAT"] = "0"
        self.assertNotIn("X-Agent-Runtime", account.headers())
        self.assertNotIn("baggage", account.headers())


class ConversationFieldInjectionTests(unittest.TestCase):
    def test_only_last_user_message_is_copied_and_updated(self):
        older_user = {"role": "user", "content": "older"}
        latest_user = {"role": "user", "content": "latest"}
        body = {
            "messages": [
                {"role": "system", "content": "system"},
                older_user,
                {"role": "assistant", "content": "answer"},
                latest_user,
                {"role": "assistant", "content": "trailing"},
            ],
            "stream": True,
        }

        out = wb_identity.inject_conversation_fields(body, " request-id ")
        self.assertIsNot(out, body)
        self.assertIsNot(out["messages"], body["messages"])
        self.assertIs(out["messages"][1], older_user)
        self.assertEqual(older_user, {"role": "user", "content": "older"})
        self.assertEqual(latest_user, {"role": "user", "content": "latest"})
        self.assertNotIn("conversationRequestId", body["messages"][3])
        self.assertEqual(
            out["messages"][3]["conversationRequestId"], "request-id"
        )
        self.assertTrue(out["messages"][3]["startsNewUserRequest"])
        self.assertEqual(out["messages"][3]["agent"], "cli")
        self.assertEqual(
            [message.get("conversationRequestId")
             for message in out["messages"] if isinstance(message, dict)],
            [None, None, None, "request-id", None],
        )

    def test_existing_request_id_is_not_overwritten(self):
        body = {"messages": [{
            "role": "user",
            "content": "latest",
            "conversationRequestId": "existing",
            "agent": "custom",
        }]}
        self.assertIs(
            wb_identity.inject_conversation_fields(body, "new-id"), body
        )
        self.assertEqual(
            body["messages"][0]["conversationRequestId"], "existing"
        )
        self.assertEqual(body["messages"][0]["agent"], "custom")

    def test_invalid_body_or_request_id_is_returned_unchanged(self):
        body = {"messages": [{"role": "user", "content": "latest"}]}
        self.assertIs(wb_identity.inject_conversation_fields(body, "  "), body)
        not_a_body = []
        self.assertIs(
            wb_identity.inject_conversation_fields(not_a_body, "id"),
            not_a_body,
        )
        no_messages = {"stream": True}
        self.assertIs(
            wb_identity.inject_conversation_fields(no_messages, "id"),
            no_messages,
        )
        no_user = {"messages": [{"role": "assistant", "content": "answer"}]}
        self.assertIs(
            wb_identity.inject_conversation_fields(no_user, "id"), no_user
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
