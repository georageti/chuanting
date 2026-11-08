"""把 ECDICT（skywind3000/ECDICT，MIT）预处理成川听用的小词典 data/dict.sqlite。

用法：python3 -I scripts/build_dict.py [data/raw/ecdict.csv]

产出三张表：
  words   单词：音标、中文释义、考试标签、词频排名、柯林斯星级
  forms   变形 → 原形（took → take，analyses → analysis）
  phrases 词组：只收通用释义的条目（只有 [计][医] 这类专业释义的去掉），支持 sb / sth / one's 占位
"""
import csv
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data/raw/ecdict.csv"
OUT = ROOT / "data/dict.sqlite"

WORD_RE = re.compile(r"[a-z]+(?:-[a-z]+)*")
PLACEHOLDERS = {"sb", "sth", "sb's", "sth's", "one's", "oneself", "someone", "something", "somebody", "someone's", "somebody's"}
PHRASE_TOKEN_RE = re.compile(r"[a-z]+(?:'s)?")
# 太基础、几乎不需要学的“词组”（大多是自由组合）
TRIVIAL_PHRASES = {
    "go to", "come to", "to be", "there is", "there are", "have to", "a lot", "at home", "in the",
    "of the", "is it", "do it", "get to", "go on", "come in", "come on", "go out", "sit down",
    "stand up", "so that", "such as", "as well", "and so", "or so", "is to", "am to", "be to",
    "to do", "how much", "how many", "thank you", "of it", "for it", "in it", "on it", "at it",
    "the way", "this way", "a little", "a few", "very much", "too much", "so much", "next to",
    "in one", "all in", "at that", "by the", "for the", "to the", "on the", "at the", "in that",
}


def clean_tr(t: str) -> str:
    t = t.replace("\\n", "\n").replace("\\r", "")
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    return "\n".join(lines)


def general_senses(tr: str) -> str:
    """去掉 [计] [医] 之类的专业释义，只留通用释义；全是专业释义就返回空串"""
    keep = [ln for ln in tr.split("\n") if not ln.startswith("[")]
    return "\n".join(keep)


def to_int(x: str) -> int:
    try:
        return int(x or 0)
    except ValueError:
        return 0


def main() -> None:
    rows = csv.DictReader(open(SRC, encoding="utf-8"))
    words: dict[str, tuple] = {}
    infl: dict[str, str] = {}          # 变形词条自己标的 0:原形
    base_forms: dict[str, list] = {}   # 原形标的 p:/d:/i:/3:/s:/r:/t: 变形
    phrases: dict[str, tuple] = {}

    for r in rows:
        w = r["word"].strip()
        tr = clean_tr(r["translation"])
        if not tr:
            continue
        ex = dict(p.split(":", 1) for p in r["exchange"].split("/") if ":" in p)
        if WORD_RE.fullmatch(w):
            f, b = to_int(r["frq"]), to_int(r["bnc"])
            rank = min([x for x in (f, b) if x > 0], default=0)
            words[w] = (r["phonetic"].strip(), tr, r["tag"].strip(), rank, to_int(r["collins"]), to_int(r["oxford"]))
            if "0" in ex and WORD_RE.fullmatch(ex["0"]):
                infl[w] = ex["0"]
            for k in "pdi3srt":
                for form in ex.get(k, "").split(","):
                    form = form.strip()
                    if WORD_RE.fullmatch(form) and form != w:
                        base_forms.setdefault(w, []).append(form)
        elif " " in w:
            toks = w.split()
            if not (2 <= len(toks) <= 5) or not all(PHRASE_TOKEN_RE.fullmatch(t) for t in toks):
                continue
            if toks[0] in PLACEHOLDERS or all(t in PLACEHOLDERS for t in toks[1:]) and len(toks) == 2:
                continue
            if w in TRIVIAL_PHRASES:
                continue
            g = general_senses(tr)
            if not g:
                continue
            phrases[w] = (g,)

    def rank_of(x: str) -> int:
        rk = words.get(x, (None, None, None, 0))[3]
        return rk if rk > 0 else 10**7

    forms: dict[str, str] = {}
    for base, fl in base_forms.items():
        for f in fl:
            forms.setdefault(f, base)
    for f, base in infl.items():
        if base in words:
            forms[f] = base
    # 变形本身比原形还常用（data 比 datum 常用），就别还原
    for f in list(forms):
        if f in words and rank_of(f) < rank_of(forms[f]):
            del forms[f]

    def lemma(t: str) -> str:
        return forms.get(t, t)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    db = sqlite3.connect(OUT)
    db.executescript(
        """
        CREATE TABLE words(w TEXT PRIMARY KEY, ph TEXT, tr TEXT, tags TEXT, rank INT, collins INT, oxford INT);
        CREATE TABLE forms(f TEXT PRIMARY KEY, base TEXT);
        CREATE TABLE phrases(p TEXT PRIMARY KEY, head TEXT, n INT, tr TEXT);
        CREATE INDEX phrases_head ON phrases(head);
        """
    )
    db.executemany("INSERT INTO words VALUES(?,?,?,?,?,?,?)", [(w, *v) for w, v in words.items()])
    db.executemany("INSERT INTO forms VALUES(?,?)", forms.items())
    db.executemany(
        "INSERT INTO phrases VALUES(?,?,?,?)",
        [(p, lemma(p.split()[0]), len(p.split()), v[0]) for p, v in phrases.items()],
    )
    db.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    db.execute("INSERT INTO meta VALUES('source', 'ECDICT (MIT) github.com/skywind3000/ECDICT')")
    db.commit()
    db.execute("VACUUM")
    db.close()
    print(f"words={len(words)} forms={len(forms)} phrases={len(phrases)} → {OUT} ({OUT.stat().st_size // 1024 // 1024} MB)")


if __name__ == "__main__":
    main()
