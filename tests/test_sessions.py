""".venv/bin/python tests/test_sessions.py — 独立临时词库，不调用 AI 或语音服务。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from chuanting import server, srs, store


class SessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chuanting-sessions-")
        home = Path(self.tmp.name)
        self.paths = patch.multiple(store, HOME=home, DB_PATH=home / "test.db",
                                    CONFIG_PATH=home / "config.json", _conn=None)
        self.paths.start()
        self.client = TestClient(server.app)
        store.save_config({"ai": {"provider": "original"}})

    def tearDown(self):
        self.client.close()
        if store._conn:
            store._conn.close()
        self.paths.stop()
        self.tmp.cleanup()

    def seed(self, n, due=0, future=0, contexts=True):
        keys = [f"term{i:04}" for i in range(n)]
        for i, key in enumerate(keys):
            store.add_items([{"key": key, "kind": "word", "translation": "测试词",
                              "examples": [{"sentence": f"Please practice {key} today.", "surface": key}]}], None)
            if contexts:
                store.add_contexts(key, [{"sentence": f"Please practice {key} today.", "answer": [key]}], "original")
            if i < due + future:
                day = srs.add_days(srs.today(), -2 if i < due else 2)
                store.tx("UPDATE items SET state=? WHERE key=?", (json.dumps(srs.learn(3, day)), key))
        return keys

    def post(self, path, **body):
        r = self.client.post(path, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_all_covers_more_than_old_twenty_and_hundred_caps(self):
        keys = self.seed(237, due=105, future=12)
        plan = self.post("/api/session/plan", mode="all", limit=20)
        self.assertEqual(plan["total"], 237)
        self.assertCountEqual(plan["keys"], keys)
        cards = self.post("/api/session", mode="all", limit=20)["cards"]
        self.assertCountEqual([c["key"] for c in cards], keys)

    def test_review_defaults_to_one_hundred_and_can_include_all_due(self):
        keys = self.seed(180, due=157, future=13)
        default = self.post("/api/session/plan", mode="review")
        self.assertEqual(default["total"], 100)
        for size in (20, 50, 100, 200, 0):
            plan = self.post("/api/session/plan", mode="review", limit=size)
            self.assertEqual(plan["total"], min(size, 157) if size else 157)
            self.assertTrue(set(plan["keys"]) <= set(keys[:157]))

    def test_orders_change_without_losing_or_duplicating_words(self):
        keys = self.seed(137, due=137)
        for mode in ("review", "all"):
            plans = [self.post("/api/session/plan", mode=mode, limit=0)["keys"] for _ in range(3)]
            for plan in plans:
                self.assertCountEqual(plan, keys)
            self.assertGreater(len({tuple(plan) for plan in plans}), 1)

    def test_daily_new_quota_and_future_review_dates_stay_separate(self):
        keys = self.seed(190, due=130, future=20)
        store.save_config({"study": {"daily_new": 7}})
        mix = self.post("/api/session/plan", mode="mix", limit=0)
        self.assertEqual(mix["total"], 137)
        self.assertTrue(set(keys[:130]) <= set(mix["keys"]))
        self.assertFalse(set(keys[130:150]) & set(mix["keys"]))
        learn = self.post("/api/session/plan", mode="learn", limit=0)
        self.assertEqual(learn["total"], 7)
        for key in learn["keys"]:
            store.record(key, 0, False, False)
        self.assertEqual(self.post("/api/session/plan", mode="learn", limit=0)["total"], 0)
        self.assertEqual(self.post("/api/session/plan", mode="all")["total"], 190)

    def test_large_groups_empty_selection_and_duplicate_keys(self):
        keys = self.seed(203)
        plan = self.post("/api/session/plan", mode="all", keys=keys + keys[:5])
        self.assertCountEqual(plan["keys"], keys)
        self.assertEqual(plan["total"], 203)
        self.assertEqual(self.post("/api/session/plan", keys=[])["total"], 0)
        self.assertEqual(self.post("/api/session", keys=[])["cards"], [])

    def test_higher_daily_quotas_and_unlimited_after_learning_today(self):
        keys = self.seed(260)
        for key in keys[:55]:
            store.record(key, 0, False, False)
        for daily, expected in ((50, 0), (100, 45), (200, 145), (0, 205)):
            self.post("/api/settings", study={"daily_new": daily})
            stats = self.client.get("/api/stats").json()
            self.assertEqual(stats["dailyNew"], daily)
            self.assertEqual(stats["learnedToday"], 55)
            plan = self.post("/api/session/plan", mode="learn", limit=0)
            self.assertEqual(plan["total"], expected)
            self.assertTrue(set(plan["keys"]) <= set(keys[55:]))
        # 不限每日量时，每轮 100 仍只出 100；保存的不限值在重新读取后保留。
        self.assertEqual(self.post("/api/session/plan", mode="learn", limit=100)["total"], 100)
        self.assertEqual(store.load_config()["study"]["daily_new"], 0)

    def test_unlimited_daily_new_respects_mixed_session_capacity(self):
        self.seed(250, due=30, future=20)
        self.post("/api/settings", study={"daily_new": 0})
        for size, expected in ((20, 20), (100, 100), (200, 200), (0, 230)):
            plan = self.post("/api/session/plan", mode="mix", limit=size)
            self.assertEqual(plan["total"], expected)
        self.assertEqual(self.post("/api/session/plan", mode="review", limit=0)["total"], 30)

    def test_invalid_daily_quota_does_not_change_saved_settings(self):
        self.post("/api/settings", study={"daily_new": 200})
        for value in (-1, 1001, True, "unlimited", None, 2.5):
            response = self.client.post("/api/settings", json={"study": {"daily_new": value}})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(store.load_config()["study"]["daily_new"], 200)

    def test_batch_walk_covers_snapshot_once_despite_srs_changes(self):
        keys = self.seed(143, due=120)
        plan = self.post("/api/session/plan", mode="all")
        seen = []
        for offset in range(0, len(keys), 20):
            chunk = plan["keys"][offset:offset + 20]
            batch = self.post("/api/session", keys=chunk, ordered=True)
            self.assertEqual([c["key"] for c in batch["cards"]], chunk)
            self.assertEqual(batch["skipped"], [])
            for card in batch["cards"]:
                self.post("/api/review", key=card["key"], ctxId=card["ctxId"], mistakes=0)
                seen.append(card["key"])
        self.assertEqual(seen, plan["keys"])
        self.assertCountEqual(seen, keys)
        self.assertEqual(store.q("SELECT COUNT(*) n FROM log")[0]["n"], 143)

    def test_planning_does_not_generate_or_change_study_data(self):
        self.seed(133, due=110, contexts=False)
        before = store.q("SELECT key, state FROM items ORDER BY key")
        with patch.object(server, "ensure_contexts", side_effect=AssertionError("planning must not generate")):
            self.post("/api/session/plan", mode="all")
        self.assertEqual(before, store.q("SELECT key, state FROM items ORDER BY key"))
        self.assertEqual(store.q("SELECT * FROM contexts"), [])
        self.assertEqual(store.q("SELECT * FROM log"), [])

    def test_missing_and_invalid_contexts_are_accounted_for(self):
        keys = self.seed(4)
        store.remove_item(keys[0])
        store.delete_contexts(keys[1])
        store.tx("DELETE FROM examples WHERE key=?", (keys[1],))
        store.delete_contexts(keys[2])
        store.add_contexts(keys[2], [{"sentence": "Invalid sentence.", "answer": [keys[2]]}], "original")
        batch = self.post("/api/session", keys=keys, ordered=True)
        self.assertEqual(batch["skipped"], keys[:2])
        self.assertEqual([c["key"] for c in batch["cards"]], keys[2:])
        self.assertTrue(batch["warnings"])

    def test_bad_first_context_does_not_hide_valid_later_context(self):
        key = self.seed(1, contexts=False)[0]
        store.add_contexts(key, [{"sentence": "Invalid sentence.", "answer": [key]},
                                 {"sentence": f"Use {key} today.", "answer": [key]}], "original")
        batch = self.post("/api/session", keys=[key])
        self.assertEqual(batch["cards"][0]["sentence"], f"Use {key} today.")
        self.assertEqual(batch["skipped"], [])

    def test_existing_config_gains_default_without_overwriting_preferences(self):
        store.CONFIG_PATH.write_text(json.dumps({"study": {"daily_new": 7, "read_mode": "full"}}))
        before = store.CONFIG_PATH.read_bytes()
        cfg = self.client.get("/api/settings").json()
        self.assertEqual(cfg["study"]["session_size"], 100)
        self.assertEqual(store.CONFIG_PATH.read_bytes(), before)
        cfg = self.post("/api/settings", study={"session_size": 0})
        self.assertEqual(cfg["study"]["daily_new"], 7)
        self.assertEqual(cfg["study"]["read_mode"], "full")
        self.assertEqual(store.load_config()["study"]["session_size"], 0)

    def test_validation_does_not_save_bad_session_sizes(self):
        for value in (-1, 1001, True, "all", None, 1.5):
            for path, body in (("/api/settings", {"study": {"session_size": value}}),
                               ("/api/session/plan", {"limit": value})):
                self.assertEqual(self.client.post(path, json=body).status_code, 400)
        self.assertEqual(store.load_config()["study"]["session_size"], 100)
        self.assertEqual(self.client.post("/api/session/plan", json={"keys": "wrong"}).status_code, 400)

    def test_export_retains_explicit_pack_limit(self):
        self.seed(90)
        self.assertEqual(len(server.pick_items("all", 2)), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
