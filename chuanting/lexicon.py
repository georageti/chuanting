"""词典查询：单词释义、词形还原、按开头词找词组。数据来自 scripts/build_dict.py 产出的 data/dict.sqlite。"""
from __future__ import annotations

import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

DICT_PATH = Path(__file__).resolve().parent.parent / "data/dict.sqlite"

# 考试标签从易到难；“隐藏到某一级”= 这一级及更简单的词都不显示
LEVELS = ["zk", "gk", "cet4", "cet6", "ky", "ielts", "toefl", "gre"]
LEVEL_NAMES = {"zk": "中考", "gk": "高考", "cet4": "四级", "cet6": "六级", "ky": "考研",
               "ielts": "雅思", "toefl": "托福", "gre": "GRE"}

_local = threading.local()


def _db() -> sqlite3.Connection:
    db = getattr(_local, "db", None)
    if db is None:
        if not DICT_PATH.exists():
            raise RuntimeError("还没有词典文件，请先运行 python3 -I scripts/build_dict.py")
        db = sqlite3.connect(f"file:{DICT_PATH}?mode=ro", uri=True, check_same_thread=False)
        _local.db = db
    return db


@lru_cache(maxsize=200_000)
def word(w: str) -> dict | None:
    row = _db().execute("SELECT w, ph, tr, tags, rank, collins, oxford FROM words WHERE w=?", (w,)).fetchone()
    if not row:
        return None
    tags = row[3].split()
    return {"w": row[0], "phonetic": row[1], "translation": row[2], "tags": tags,
            "rank": row[4], "collins": row[5], "oxford": row[6], "level": level_of(tags)}


@lru_cache(maxsize=200_000)
def _form(f: str) -> str | None:
    row = _db().execute("SELECT base FROM forms WHERE f=?", (f,)).fetchone()
    return row[0] if row else None


@lru_cache(maxsize=200_000)
def lemma(t: str) -> str:
    """还原成原形；词典里查不到的，用常见词尾规则再试一次；都不行就原样返回"""
    t = t.lower().replace("’", "'")
    if t.endswith("'s"):
        t = t[:-2]
    base = _form(t)
    if base:
        return base
    if word(t):
        return t
    for suffix, repl in (("ies", "y"), ("ied", "y"), ("ves", "f"), ("es", ""), ("s", ""),
                         ("ed", ""), ("ed", "e"), ("d", ""), ("ing", ""), ("ing", "e"), ("ly", "")):
        if t.endswith(suffix) and len(t) - len(suffix) >= 3:
            cand = t[: len(t) - len(suffix)] + repl
            # stopped → stopp → stop
            if suffix in ("ed", "ing") and not repl and len(cand) > 3 and cand[-1] == cand[-2]:
                if word(cand[:-1]):
                    return _form(cand[:-1]) or cand[:-1]
            if word(cand):
                return _form(cand) or cand
    return t


@lru_cache(maxsize=50_000)
def phrases_starting_with(head: str) -> tuple:
    rows = _db().execute("SELECT p, n, tr FROM phrases WHERE head=?", (head,)).fetchall()
    return tuple((p, p.split(), tr) for p, n, tr in rows)


@lru_cache(maxsize=100_000)
def phrase(p: str) -> dict | None:
    row = _db().execute("SELECT p, tr FROM phrases WHERE p=?", (p,)).fetchone()
    return {"w": row[0], "translation": row[1]} if row else None


def level_of(tags: list[str]) -> str | None:
    idx = [LEVELS.index(t) for t in tags if t in LEVELS]
    return LEVELS[min(idx)] if idx else None


def short_tr(tr: str, lines: int = 2, chars: int = 80) -> str:
    out = "；".join(tr.split("\n")[:lines])
    return out if len(out) <= chars else out[: chars - 1] + "…"
