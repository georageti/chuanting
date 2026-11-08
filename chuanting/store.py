"""用户数据：文档、学习词条、语境句、复习状态、设置。

默认位置：macOS ~/Library/Application Support/川听/，Windows %APPDATA%\川听\，其他系统 ~/.local/share/川听/。
设置环境变量 CHUANTING_HOME 可以换位置（测试用）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

from . import srs

def _default_home() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "川听"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/川听"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "川听"


HOME = Path(os.environ.get("CHUANTING_HOME") or _default_home())
DB_PATH = HOME / "chuanting.db"
CONFIG_PATH = HOME / "config.json"
AUDIO_DIR = HOME / "audio"
EXPORT_DIR = HOME / "exports"

DEFAULT_CONFIG = {
    "ai": {
        "provider": "deepseek",  # deepseek | anthropic | openai | original（不用 AI，直接用原文句子）
        "deepseek": {"key": "", "model": "deepseek-chat", "base": "https://api.deepseek.com"},
        "anthropic": {"key": "", "model": "claude-opus-5-5"},
        "openai": {"key": "", "model": "", "base": ""},
        "per_item": 2,  # 每个词生成几句语境
    },
    "tts": {"voice": "en-US-AriaNeural", "rate": "-5%", "zh_voice": "zh-CN-XiaoxiaoNeural"},
    "filter": {"hide_level": "gk", "hide_rank": 2000},
    "extract": {"phrases": "ai"},  # 词组识别：ai（用设置里的 AI，没法用时退回词典）/ dict（只用词典）
    # read_mode 朗读方式：cloze 挖空（嘀一声，不显示句子）/ show 显示句子（嘀一声，显示挖空句）/ full 读全（读完整句，显示挖空句）
    "study": {"daily_new": 20, "session_size": 100, "pause_ms": 3000, "read_mode": "cloze"},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY, title TEXT, filename TEXT, chars INT, created REAL, text TEXT, cands TEXT);
CREATE TABLE IF NOT EXISTS items(
  key TEXT PRIMARY KEY, kind TEXT, translation TEXT, phonetic TEXT, tags TEXT, level TEXT, rank INT,
  added REAL, state TEXT);
CREATE TABLE IF NOT EXISTS examples(
  id INTEGER PRIMARY KEY, key TEXT, doc_id INT, sentence TEXT, surface TEXT, UNIQUE(key, sentence));
CREATE TABLE IF NOT EXISTS contexts(
  id INTEGER PRIMARY KEY, key TEXT, sentence TEXT, answer TEXT, hint TEXT, note TEXT, source TEXT,
  used INT DEFAULT 0, created REAL);
CREATE INDEX IF NOT EXISTS contexts_key ON contexts(key);
CREATE TABLE IF NOT EXISTS known(key TEXT PRIMARY KEY, added REAL);
CREATE TABLE IF NOT EXISTS log(day TEXT, key TEXT, grade INT, mistakes INT, at REAL);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        HOME.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _migrate(_conn)
    return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    """给老数据库补新列"""
    def cols(t: str) -> set[str]:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
    if "meta" not in cols("docs"):
        conn.execute("ALTER TABLE docs ADD COLUMN meta TEXT")
    if "pieces" not in cols("examples"):
        conn.execute("ALTER TABLE examples ADD COLUMN pieces TEXT")
    conn.commit()


def tx(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        cur = db().execute(sql, args)
        db().commit()
        return cur


def q(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in db().execute(sql, args).fetchall()]


# ---------- 设置 ----------

def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config() -> dict:
    cfg = DEFAULT_CONFIG
    if CONFIG_PATH.exists():
        try:
            cfg = _merge(DEFAULT_CONFIG, json.loads(CONFIG_PATH.read_text("utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    cfg = json.loads(json.dumps(cfg))
    # 没填密钥时读环境变量
    cfg["ai"]["deepseek"]["key"] = cfg["ai"]["deepseek"]["key"] or os.environ.get("DEEPSEEK_API_KEY", "")
    cfg["ai"]["anthropic"]["key"] = cfg["ai"]["anthropic"]["key"] or os.environ.get("ANTHROPIC_API_KEY", "")
    return cfg


def save_config(patch: dict) -> dict:
    current = {}
    if CONFIG_PATH.exists():
        try:
            current = json.loads(CONFIG_PATH.read_text("utf-8"))
        except json.JSONDecodeError:
            current = {}
    merged = _merge(_merge(DEFAULT_CONFIG, current), patch)
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), "utf-8")
    os.chmod(CONFIG_PATH, 0o600)  # 里面有 API Key
    return load_config()


def public_config() -> dict:
    """给网页看的设置：密钥只露后四位"""
    cfg = load_config()
    for p in ("deepseek", "anthropic", "openai"):
        k = cfg["ai"][p]["key"]
        cfg["ai"][p]["key"] = ("••••" + k[-4:]) if k else ""
        cfg["ai"][p]["hasKey"] = bool(k)
    return cfg


# ---------- 文档 ----------

def add_doc(title: str, filename: str, text: str, cands: list[dict], meta: dict | None = None) -> int:
    cur = tx("INSERT INTO docs(title, filename, chars, created, text, cands, meta) VALUES(?,?,?,?,?,?,?)",
             (title, filename, len(text), time.time(), text, json.dumps(cands, ensure_ascii=False),
              json.dumps(meta or {}, ensure_ascii=False)))
    return cur.lastrowid


def update_doc(doc_id: int, cands: list[dict], meta: dict) -> None:
    tx("UPDATE docs SET cands=?, meta=? WHERE id=?",
       (json.dumps(cands, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), doc_id))


def list_docs() -> list[dict]:
    return q("SELECT id, title, filename, chars, created FROM docs ORDER BY created DESC")


def get_doc(doc_id: int) -> dict | None:
    rows = q("SELECT * FROM docs WHERE id=?", (doc_id,))
    if not rows:
        return None
    d = rows[0]
    d["cands"] = json.loads(d["cands"] or "[]")
    d["meta"] = json.loads(d.get("meta") or "{}")
    return d


def delete_doc(doc_id: int) -> None:
    tx("DELETE FROM docs WHERE id=?", (doc_id,))


# ---------- 已会 ----------

def known_set() -> set[str]:
    return {r["key"] for r in q("SELECT key FROM known")}


def set_known(key: str, on: bool) -> None:
    if on:
        tx("INSERT OR IGNORE INTO known VALUES(?,?)", (key, time.time()))
        tx("DELETE FROM items WHERE key=?", (key,))
    else:
        tx("DELETE FROM known WHERE key=?", (key,))


# ---------- 学习词条 ----------

def _translation(c: dict) -> str:
    """单词有 AI 给的本文意思时放在前面，后面跟词典释义"""
    tr, ctx = c.get("translation", ""), c.get("ctx", "")
    if ctx and ctx not in tr:
        return f"本文：{ctx}；{tr}" if tr else ctx
    return tr


def add_items(cands: list[dict], doc_id: int | None) -> int:
    added = 0
    for c in cands:
        exists = q("SELECT 1 FROM items WHERE key=?", (c["key"],))
        if not exists:
            tx("INSERT INTO items(key, kind, translation, phonetic, tags, level, rank, added, state) VALUES(?,?,?,?,?,?,?,?,NULL)",
               (c["key"], c["kind"], _translation(c), c.get("phonetic", ""), json.dumps(c.get("tags", [])),
                c.get("level"), c.get("rank", 0), time.time()))
            added += 1
        for ex in c.get("examples", []):
            tx("INSERT OR IGNORE INTO examples(key, doc_id, sentence, surface, pieces) VALUES(?,?,?,?,?)",
               (c["key"], doc_id, ex["sentence"], ex["surface"],
                json.dumps(ex["pieces"], ensure_ascii=False) if ex.get("pieces") else None))
    return added


def _item_row(r: dict) -> dict:
    r["tags"] = json.loads(r.get("tags") or "[]")
    r["state"] = json.loads(r["state"]) if r.get("state") else None
    return r


def get_item(key: str) -> dict | None:
    rows = q("SELECT * FROM items WHERE key=?", (key,))
    return _item_row(rows[0]) if rows else None


def list_items() -> list[dict]:
    items = [_item_row(r) for r in q("SELECT * FROM items ORDER BY added DESC")]
    counts = {r["key"]: r["n"] for r in q("SELECT key, COUNT(*) n FROM contexts GROUP BY key")}
    # 来源文档：这个词第一次出现的那篇（生词本按文档分组用）
    src = {r["key"]: (r["doc_id"], r["title"]) for r in q(
        "SELECT e.key, e.doc_id, d.title FROM examples e LEFT JOIN docs d ON d.id = e.doc_id "
        "WHERE e.id IN (SELECT MIN(id) FROM examples GROUP BY key)")}
    for it in items:
        it["contexts"] = counts.get(it["key"], 0)
        doc_id, title = src.get(it["key"], (None, None))
        it["docId"], it["docTitle"] = doc_id, title or ""
    return items


def remove_item(key: str) -> None:
    tx("DELETE FROM items WHERE key=?", (key,))


def examples_of(key: str) -> list[dict]:
    rows = q("SELECT sentence, surface, pieces FROM examples WHERE key=? ORDER BY id", (key,))
    for r in rows:
        r["pieces"] = json.loads(r["pieces"]) if r["pieces"] else None
    return rows


# ---------- 语境句 ----------

def add_contexts(key: str, ctxs: list[dict], source: str) -> None:
    for c in ctxs:
        tx("INSERT INTO contexts(key, sentence, answer, hint, note, source, created) VALUES(?,?,?,?,?,?,?)",
           (key, c["sentence"], json.dumps(c["answer"], ensure_ascii=False), c.get("hint", ""), c.get("note", ""),
            source, time.time()))


def contexts_of(key: str) -> list[dict]:
    rows = q("SELECT * FROM contexts WHERE key=? ORDER BY used, id", (key,))
    for r in rows:
        r["answer"] = json.loads(r["answer"])
    return rows


def delete_contexts(key: str) -> None:
    tx("DELETE FROM contexts WHERE key=?", (key,))


def mark_used(ctx_id: int) -> None:
    tx("UPDATE contexts SET used = used + 1 WHERE id=?", (ctx_id,))


# ---------- 复习 ----------

def due_items(limit: int | None = 50) -> list[dict]:
    day = srs.today()
    rows = [_item_row(r) for r in q("SELECT * FROM items WHERE state IS NOT NULL")]
    due = [r for r in rows if r["state"]["due"] <= day]
    due.sort(key=lambda r: (r["state"]["due"], r["state"]["s"]))
    return due[:limit]


def new_items(limit: int | None = 20) -> list[dict]:
    return [_item_row(r) for r in q("SELECT * FROM items WHERE state IS NULL ORDER BY added LIMIT ?",
                                  (-1 if limit is None else limit,))]


def learned_today() -> int:
    day = srs.today()
    return sum(1 for r in q("SELECT state FROM items WHERE state IS NOT NULL")
               if json.loads(r["state"]).get("learned") == day)


def record(key: str, mistakes: int, peeked: bool, hinted: bool) -> dict | None:
    it = get_item(key)
    if not it:
        return None
    g = srs.grade(mistakes, peeked, hinted)
    state = srs.learn(g) if it["state"] is None else srs.review(it["state"], g)
    tx("UPDATE items SET state=? WHERE key=?", (json.dumps(state), key))
    tx("INSERT INTO log VALUES(?,?,?,?,?)", (srs.today(), key, g, mistakes, time.time()))
    return {"grade": g, "state": state}


def stats() -> dict:
    items = [_item_row(r) for r in q("SELECT * FROM items")]
    day = srs.today()
    learned = [i for i in items if i["state"]]
    return {
        "items": len(items),
        "new": len(items) - len(learned),
        "due": sum(1 for i in learned if i["state"]["due"] <= day),
        "mastered": sum(1 for i in learned if i["state"]["s"] >= srs.MASTERED_DAYS),
        "known": q("SELECT COUNT(*) n FROM known")[0]["n"],
        "today": q("SELECT COUNT(*) n FROM log WHERE day=?", (day,))[0]["n"],
        "learnedToday": learned_today(),
        "history": history(14),
        "dailyNew": load_config()["study"]["daily_new"],
    }


def history(days: int = 14) -> list[dict]:
    """最近 days 天每天练了多少次（今天在最后）"""
    today = srs.today()
    counts = {r["day"]: r["n"] for r in q("SELECT day, COUNT(*) n FROM log WHERE day >= ? GROUP BY day",
                                         (srs.add_days(today, -(days - 1)),))}
    return [{"day": d, "n": counts.get(d, 0)} for d in (srs.add_days(today, -i) for i in range(days - 1, -1, -1))]
