"""媒体设置只保留四项限额，并兼容旧版 settings.json 的残留字段。

总开关、媒体出口、出站身份已从产品中移除。旧用户升级后
``settings.json.media`` 里可能仍有 ``enabled`` / ``realm`` / ``identity``；
读取时忽略它们，下一次保存时清除。

Run with: python tests/_test_media_settings.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_settings


EXPECTED = {
    "image_max_per_minute": 6,
    "video_max_seconds": 15,
    "daily_credits_per_key": 0,
    "video_concurrency": 3,
}


class MediaSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wb-media-settings-")
        self.accounts = self.tmp.name
        self.path = wb_settings.settings_path(self.accounts)

    def tearDown(self):
        self.tmp.cleanup()

    def stored(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_defaults_contain_only_the_four_limits(self):
        self.assertEqual(wb_settings.media_config(self.accounts), EXPECTED)
        self.assertEqual(set(wb_settings.MEDIA_DEFAULTS), set(EXPECTED))

    def test_save_replaces_obsolete_fields(self):
        wb_settings.set_media_config(self.accounts, {
            "image_max_per_minute": 9,
            "video_max_seconds": 20,
            "daily_credits_per_key": 120,
            "video_concurrency": 2,
        })
        self.assertEqual(self.stored()["media"], {
            "image_max_per_minute": 9,
            "video_max_seconds": 20,
            "daily_credits_per_key": 120,
            "video_concurrency": 2,
        })

    def test_old_fields_are_ignored_and_removed_on_save(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"media": {
                "enabled": False,
                "realm": "cn",
                "identity": "cli",
                "image_max_per_minute": 11,
                "video_max_seconds": 18,
                "daily_credits_per_key": 50,
                "video_concurrency": 4,
            }}, handle)
        loaded = wb_settings.media_config(self.accounts)
        self.assertEqual(loaded["image_max_per_minute"], 11)
        self.assertNotIn("enabled", loaded)
        self.assertNotIn("realm", loaded)
        self.assertNotIn("identity", loaded)

        wb_settings.set_media_config(self.accounts, loaded)
        self.assertEqual(set(self.stored()["media"]), set(EXPECTED))

    def test_partial_save_keeps_existing_limits(self):
        wb_settings.set_media_config(self.accounts, {"image_max_per_minute": 12})
        self.assertEqual(wb_settings.media_config(self.accounts), {
            "image_max_per_minute": 12,
            "video_max_seconds": 15,
            "daily_credits_per_key": 0,
            "video_concurrency": 3,
        })

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(ValueError):
            wb_settings.validate_media_patch({"not_a_limit": 1})

    def test_bool_and_out_of_range_are_rejected(self):
        with self.assertRaises(ValueError):
            wb_settings.validate_media_patch({"video_concurrency": True})
        with self.assertRaises(ValueError):
            wb_settings.validate_media_patch({"image_max_per_minute": 0})
        with self.assertRaises(ValueError):
            wb_settings.validate_media_patch({"video_max_seconds": 61})


if __name__ == "__main__":
    unittest.main(verbosity=2)
