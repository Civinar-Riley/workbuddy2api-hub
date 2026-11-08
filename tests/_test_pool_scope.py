"""Text/media pool separation: dispatch boundaries, persistence, API surface.

PR #841 introduced the feature. The original branch also carried unrelated
reverts, so this suite pins the behaviour of the clean integration:

  * old accounts default to text; ``disabledModels`` never implies media;
  * text/media picks and counts never cross pools;
  * a pinned account of the wrong pool is rejected;
  * API-key ``allowed`` and the pool scope both apply;
  * ``WB_POOL_SCOPE`` enforces process-level entry boundaries;
  * ``WB_POOL_SEPARATION=0`` restores the old single-pool behaviour;
  * an empty media pool never falls back to text accounts;
  * ``poolScope`` persists and supports batch moves;
  * whole-pool pages still bind a long conversation to one account.

Run with: python tests/_test_pool_scope.py
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_accounts
import wb_pool_media
import wb_pool_scope
import wb_pool_text


def make_account(uid, scope=None, realm="cn", **extra):
    data = {"uid": uid, "accessToken": "token-%s" % uid, "realm": realm}
    if scope is not None:
        data["poolScope"] = scope
    data.update(extra)
    return wb_accounts.Account(data)


def make_pool(accounts):
    pool = wb_accounts.AccountPool(
        tempfile.mkdtemp(prefix="pool-scope-"), log=lambda _m: None)
    pool.accounts = list(accounts)
    return pool


class ScopeFieldTests(unittest.TestCase):
    def test_old_account_defaults_to_text(self):
        self.assertEqual(make_account("old").pool_scope,
                         wb_pool_scope.SCOPE_TEXT)

    def test_disabled_models_do_not_infer_media(self):
        account = make_account("deny", disabledModels=["glm-5.3"])
        self.assertEqual(account.pool_scope, wb_pool_scope.SCOPE_TEXT)

    def test_scope_round_trips_through_dict_and_public(self):
        account = make_account("media", scope="media")
        self.assertEqual(account.to_dict()["poolScope"], "media")
        self.assertEqual(account.public()["poolScope"], "media")

    def test_unknown_scope_falls_back_to_text(self):
        self.assertEqual(make_account("junk", scope="nonsense").pool_scope,
                         wb_pool_scope.SCOPE_TEXT)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.text = make_account("text-a")
        self.media = make_account("media-a", scope="media")
        self.pool = make_pool([self.text, self.media])
        self.texts = wb_pool_text.TextPool(self.pool)
        self.medias = wb_pool_media.MediaPool(self.pool)

    def test_text_pool_never_picks_media(self):
        self.assertEqual(self.texts.pick(realm="cn").uid, "text-a")

    def test_media_pool_never_picks_text(self):
        self.assertEqual(self.medias.pick(realm="cn").uid, "media-a")

    def test_empty_media_pool_does_not_fall_back(self):
        self.pool.accounts = [self.text]
        self.assertIsNone(self.medias.pick(realm="cn"))
        self.assertIn("文本号池", self.medias.empty_detail("cn"))

    def test_counts_are_per_pool(self):
        self.assertEqual(self.texts.count_ready("cn"), 1)
        self.assertEqual(self.medias.count_ready("cn"), 1)

    def test_api_key_allowed_and_scope_both_apply(self):
        other_text = make_account("text-b")
        other_media = make_account("media-b", scope="media")
        self.pool.accounts.extend([other_text, other_media])
        picked = self.texts.pick(realm="cn", allowed={"text-b", "media-b"})
        self.assertEqual(picked.uid, "text-b")
        picked = self.medias.pick(realm="cn", allowed={"text-b", "media-b"})
        self.assertEqual(picked.uid, "media-b")

    def test_require_rejects_cross_pool_accounts(self):
        with self.assertRaises(wb_pool_scope.ScopeViolation):
            self.texts.require(self.media)
        with self.assertRaises(wb_pool_scope.ScopeViolation):
            self.medias.require(self.text)

    def test_whole_pool_page_still_binds(self):
        pool = make_pool([make_account("a"), make_account("b")])
        texts = wb_pool_text.TextPool(pool)
        first = texts.pick_for_session(realm="cn", session_key="long",
                                       page=(0, 5))
        self.assertEqual(pool.affinity.get("long"), first.uid)
        for _ in range(4):
            self.assertEqual(
                texts.pick_for_session(realm="cn", session_key="long",
                                       page=(0, 5)).uid,
                first.uid)

    def test_whole_pool_page_counts_only_the_scoped_pool(self):
        pool = make_pool([
            make_account("a"),
            make_account("b"),
            make_account("m1", scope="media"),
            make_account("m2", scope="media"),
        ])
        texts = wb_pool_text.TextPool(pool)
        first = texts.pick_for_session(realm="cn", session_key="long",
                                       page=(0, 3))
        self.assertEqual(pool.affinity.get("long"), first.uid)
        self.assertIn(first.uid, {"a", "b"})


class BoundaryTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("WB_POOL_SCOPE", None)
        os.environ.pop("WB_POOL_SEPARATION", None)

    def test_media_instance_refuses_text_entry(self):
        os.environ["WB_POOL_SCOPE"] = "media"
        self.assertTrue(wb_pool_scope.entry_allows("media"))
        self.assertFalse(wb_pool_scope.entry_allows("text"))
        pool = make_pool([make_account("text-a"),
                          make_account("media-a", scope="media")])
        self.assertIsNone(wb_pool_text.TextPool(pool).pick(realm="cn"))
        self.assertEqual(wb_pool_media.MediaPool(pool).pick(realm="cn").uid,
                         "media-a")

    def test_text_instance_refuses_media_entry(self):
        os.environ["WB_POOL_SCOPE"] = "text"
        self.assertTrue(wb_pool_scope.entry_allows("text"))
        self.assertFalse(wb_pool_scope.entry_allows("media"))
        pool = make_pool([make_account("text-a"),
                          make_account("media-a", scope="media")])
        self.assertEqual(wb_pool_text.TextPool(pool).pick(realm="cn").uid,
                         "text-a")
        self.assertIsNone(wb_pool_media.MediaPool(pool).pick(realm="cn"))

    def test_separation_off_restores_the_single_pool(self):
        os.environ["WB_POOL_SEPARATION"] = "0"
        pool = make_pool([make_account("media-a", scope="media")])
        self.assertEqual(wb_pool_text.TextPool(pool).pick(realm="cn").uid,
                         "media-a")
        self.assertEqual(wb_pool_media.MediaPool(pool).pick(realm="cn").uid,
                         "media-a")


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="pool-scope-set-")
        self.pool = wb_accounts.AccountPool(self.directory,
                                            log=lambda _m: None)
        self.pool.accounts = [make_account("a"), make_account("b")]

    def test_set_and_persist_one(self):
        updated = self.pool.set_pool_scope("a", "media")
        self.assertEqual(updated["poolScope"], "media")
        reloaded = wb_accounts.AccountPool(self.directory,
                                           log=lambda _m: None).load()
        self.assertEqual({a.uid: a for a in reloaded}["a"].pool_scope, "media")

    def test_batch_move(self):
        moved = self.pool.set_pool_scope_many(["a", "b"], "media")
        self.assertEqual(sorted(moved), ["a", "b"])
        self.assertEqual({a.pool_scope for a in self.pool.accounts}, {"media"})

    def test_invalid_scope_is_rejected(self):
        self.assertIsNone(self.pool.set_pool_scope("a", "nonsense"))
        self.assertIsNone(self.pool.set_pool_scope_many(["a"], "nonsense"))


class LegacyPoolTests(unittest.TestCase):
    class LegacyPool(object):
        def __init__(self, accounts):
            self.accounts = accounts

        def pick(self, realm=None, exclude=None, model=None, page=None,
                 allowed=None):
            for account in self.accounts:
                if account.uid in (exclude or set()):
                    continue
                if allowed is not None and account.uid not in allowed:
                    continue
                return account
            return None

        def count_ready(self, realm=None, model=None, allowed=None,
                        refresh=True):
            return len(self.accounts)

    def test_legacy_pool_with_scope_fields_is_filtered(self):
        pool = self.LegacyPool([make_account("text-a"),
                                make_account("media-a", scope="media")])
        self.assertEqual(wb_pool_text.TextPool(pool).pick(realm="cn").uid,
                         "text-a")
        self.assertEqual(wb_pool_media.MediaPool(pool).pick(realm="cn").uid,
                         "media-a")

    def test_legacy_pool_without_scope_fields_keeps_old_behavior(self):
        class Bare(object):
            def __init__(self, uid):
                self.uid = uid
                self.realm = "cn"
                self.enabled = True
                self.access_token = "x"

            def ready(self, model=None, refresh=True):
                return True

        pool = self.LegacyPool([Bare("bare")])
        self.assertEqual(wb_pool_text.TextPool(pool).pick(realm="cn").uid,
                         "bare")
        self.assertEqual(wb_pool_media.MediaPool(pool).pick(realm="cn").uid,
                         "bare")

    def test_real_type_error_inside_pool_is_not_swallowed(self):
        class Broken(object):
            accounts = []

            def pick(self, *args, **kwargs):
                raise TypeError("bug inside pick")

        with self.assertRaises(TypeError):
            wb_pool_text.TextPool(Broken()).pick(realm="cn")


if __name__ == "__main__":
    unittest.main(verbosity=2)
