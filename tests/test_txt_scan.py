"""TXT 扫描回归：只用临时数据库和本地词典，不调用 AI/TTS。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from chuanting import extract, server, store

WORDS = ["painstaking", "prefix", "affix", "suffix", "subconscious", "ubiquitous", "ephemeral", "in fact"]
ENTRIES = [WORDS[i % len(WORDS)] for i in range(722)] + ["eloquent"]
WORD_LIST = "\n".join(ENTRIES) + "\n"


class TxtScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chuanting-txt-test-")
        home = Path(self.tmp.name)
        self.paths = patch.multiple(store, HOME=home, DB_PATH=home / "test.db",
                                    CONFIG_PATH=home / "config.json", _conn=None)
        self.paths.start()
        self.env = patch.dict(os.environ, {"CHUANTING_NO_PREFETCH": "1"})
        self.env.start()
        self.client = TestClient(server.app)
        store.save_config({"ai": {"provider": "original"}, "filter": {"hide_level": "none", "hide_rank": 0}})

    def tearDown(self):
        self.client.close()
        if store._conn:
            store._conn.close()
        self.paths.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_723_line_vocabulary_list_keeps_every_entry(self):
        for separator in ("\n", "\r\n", "\r"):
            text = separator.join(ENTRIES) + separator
            self.assertGreater(len(text), 1200)
            self.assertEqual(extract.usable_sentences(text), ENTRIES)

    def test_upload_encodings_recognize_words_and_phrases(self):
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "gb18030"):
            text = "英语词表\n\n" + WORD_LIST
            response = self.client.post("/api/docs", files={"file": ("wordlist.TXT", text.encode(encoding), "text/plain")})
            self.assertEqual(response.status_code, 200, response.text)
            doc = response.json()
            keys = {c["key"] for c in doc["cands"]}
            self.assertTrue({"painstaking", "ubiquitous", "eloquent", "in fact"} <= keys)
            sents = self.client.get(f"/api/docs/{doc['id']}/sentences").json()
            self.assertEqual([s["text"] for s in sents][1:], ENTRIES)
            added = self.client.post("/api/items", json={"doc_id": doc["id"], "keys": ["painstaking"]})
            self.assertEqual(added.status_code, 200)
            self.assertEqual(store.examples_of("painstaking")[0]["sentence"], "painstaking")

    def test_adjacent_word_list_lines_do_not_form_a_phrase(self):
        cands = extract.extract("carry\nout\nubiquitous\n")
        self.assertNotIn("carry out", {c.key for c in cands})
        self.assertIn("ubiquitous", {c.key for c in cands})

    def test_wrapped_prose_keeps_its_sentence_and_phrase(self):
        text = "She turned\nthe offer down at once.\nDr. Smith arrived later."
        self.assertEqual(extract.usable_sentences(text), [
            "She turned the offer down at once.", "Dr. Smith arrived later."])
        phrase = next(c for c in extract.extract(text) if c.key == "turn sb down")
        self.assertEqual(phrase.examples[0]["surface"], "turned the offer down")

    def test_long_unpunctuated_text_keeps_all_words(self):
        text = "painstaking " + "ubiquitous ephemeral reluctant " * 180 + "eloquent"
        parts = extract.usable_sentences(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(s) <= 1200 for s in parts))
        self.assertEqual(" ".join(parts), text)
        keys = {c.key for c in extract.extract(text)}
        self.assertTrue({"painstaking", "ubiquitous", "eloquent"} <= keys)

    def test_long_single_token_is_not_cut_into_fake_words(self):
        token = "x" * 1500
        self.assertEqual(extract.usable_sentences(token), [token])

    def test_failed_scan_can_be_reanalyzed_without_adding_a_document(self):
        doc_id = store.add_doc("wordlist", "wordlist.txt", WORD_LIST, [],
                               {"phrases": "ai", "sents": [], "warnings": []})
        response = self.client.post(f"/api/docs/{doc_id}/phrases")
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json()["totalWords"], 0)
        self.assertEqual(response.json()["id"], doc_id)
        self.assertEqual(len(store.list_docs()), 1)
        self.assertEqual(store.list_items(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
