"""从 data/dict.sqlite 生成手机网页版用的精简词典（gzip 压缩的 JSON），放在 mobile/dict/。

- words.json.gz   常用单词：有考试标签、词频前 6 万或有柯林斯星级的，约 5 万个 → {词: [音标, 释义, 标签, 词频排名]}
- forms.json.gz   变形 → 原形（变形的原形也在常用单词里才收）
- phrases.json.gz 词组：[[词组, 开头词原形, 释义], ...]

网页首次打开时下载一次（约 3MB），之后存在浏览器缓存里，离线也能用。
用法：python3 -I scripts/build_mobile_dict.py   （先跑过 setup，保证 data/dict.sqlite 存在）
"""
import gzip
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data/dict.sqlite"
OUT = ROOT / "mobile/dict"
MAX_RANK = 60000


def short(tr: str, lines: int = 2, chars: int = 80) -> str:
    out = "；".join(tr.split("\n")[:lines])
    return out if len(out) <= chars else out[: chars - 1] + "…"


def write(name: str, obj) -> None:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # mtime=0：内容不变时文件也逐字节不变，git 不会每次都看到改动
    data = gzip.compress(raw, compresslevel=9, mtime=0)
    (OUT / name).write_bytes(data)
    print(f"{name}: {len(obj)} 条，{len(data) / 1e6:.2f} MB")


def main() -> None:
    db = sqlite3.connect(SRC)
    OUT.mkdir(parents=True, exist_ok=True)
    words = {}
    for w, ph, tr, tags, rank, collins in db.execute("SELECT w, ph, tr, tags, rank, collins FROM words"):
        if tags or (rank and rank <= MAX_RANK) or collins:
            words[w] = [ph, short(tr), tags, rank]
    forms = {f: b for f, b in db.execute("SELECT f, base FROM forms") if b in words}
    phrases = [[p, head, short(tr, 1, 50)] for p, head, tr in db.execute("SELECT p, head, tr FROM phrases")]
    write("words.json.gz", words)
    write("forms.json.gz", forms)
    write("phrases.json.gz", phrases)
    write_shared()


def write_shared() -> None:
    """把 Python 版的常量（停用词、提示词、默认设置……）导出成 mobile/lib/shared.js，两边只维护一份"""
    import sys

    sys.path.insert(0, str(ROOT))
    from chuanting import ai, extract, server, store
    from chuanting import lexicon as lx

    shared = {
        "STOPWORDS": sorted(extract.STOPWORDS), "PARTICLES": sorted(extract.PARTICLES),
        "POSSESSIVES": sorted(extract.POSSESSIVES), "REFLEXIVES": sorted(extract.REFLEXIVES),
        "OBJ_PLACEHOLDERS": sorted(extract.OBJ_PLACEHOLDERS), "POSS_PLACEHOLDERS": sorted(extract.POSS_PLACEHOLDERS),
        "DETERMINERS": sorted(extract.DETERMINERS), "OBJ_PRONOUNS": sorted(extract.OBJ_PRONOUNS),
        "ADVERBS": sorted(extract.ADVERBS), "IDIOM_PARTICLES": sorted(extract.IDIOM_PARTICLES),
        "ABBREV": sorted(extract.ABBREV),
        "LEVELS": lx.LEVELS, "LEVEL_NAMES": lx.LEVEL_NAMES,
        "SYSTEM": ai.SYSTEM, "SCHEMA": ai.SCHEMA, "EXAMPLE": ai.EXAMPLE, "BATCH": ai.BATCH,
        "ANALYZE_SYSTEM": ai.ANALYZE_SYSTEM, "ANALYZE_SCHEMA": ai.ANALYZE_SCHEMA, "ANALYZE_EXAMPLE": ai.ANALYZE_EXAMPLE,
        "ANALYZE_CHUNK_CHARS": ai.ANALYZE_CHUNK_CHARS, "ANALYZE_MAX_CHARS": ai.ANALYZE_MAX_CHARS,
        "PHRASE_TYPES": sorted(ai.PHRASE_TYPES), "SLOTS": sorted(ai.SLOTS),
        "GLOSS_SYSTEM": ai.GLOSS_SYSTEM, "GLOSS_SCHEMA": ai.GLOSS_SCHEMA,
        "SPOKEN": server.SPOKEN,
        "DEFAULT_CONFIG": store.DEFAULT_CONFIG,
    }
    js = ("// 由 scripts/build_mobile_dict.py 从 Python 版导出，请不要手改：改 chuanting/*.py 后重新运行脚本。\n"
          "export default " + json.dumps(shared, ensure_ascii=False, indent=1) + ";\n")
    out = ROOT / "mobile/lib/shared.js"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(js, encoding="utf-8")
    print(f"shared.js: {len(shared)} 项常量")


if __name__ == "__main__":
    main()
