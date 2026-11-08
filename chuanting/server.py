"""川听本地服务：FastAPI，只监听 127.0.0.1。网页在 web/。"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import os
import signal
import threading

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import ai, docs, extract, store, tts
from . import lexicon as lx

WEB = Path(__file__).resolve().parent.parent / "web"
app = FastAPI(title="川听", docs_url=None, redoc_url=None)
UVICORN = None  # run.py 启动时放进 uvicorn.Server，用来正常退出


@app.middleware("http")
async def revalidate_web_files(request, call_next):
    """网页文件每次都向服务器确认（本地 304 很快），更新界面后浏览器不会继续用旧的 app.css / app.js"""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def err(msg: str, code: int = 400):
    raise HTTPException(code, msg)


# ---------- 卡片 ----------

SPOKEN = {"sb": "somebody", "sth": "something", "sb's": "somebody's", "sth's": "something's"}


def meaning_of(tr: str) -> str:
    first = tr.split("；")[0]
    first = re.sub(r"^\s*([a-z]+\.\s*)+", "", first)  # 去掉 vt. n. 这类词性
    first = re.sub(r"\[[^\]]*\]", "", first)
    senses = [s.strip() for s in re.split(r"[,，;；]", first) if s.strip()]
    return "，".join(senses[:2])


def make_card(item: dict, ctx: dict) -> dict | None:
    segs = ai.make_cloze(ctx["sentence"], ctx["answer"])
    if not segs:
        return None
    return {
        "key": item["key"], "kind": item["kind"], "translation": item["translation"], "phonetic": item.get("phonetic", ""),
        "levelName": lx.LEVEL_NAMES.get(item.get("level") or "", ""), "isNew": item.get("state") is None,
        "ctxId": ctx["id"], "sentence": ctx["sentence"], "answer": ctx["answer"], "segments": segs,
        "hint": ctx.get("hint", ""), "note": ctx.get("note", ""), "source": ctx.get("source", ""),
        "meaning": item["translation"] if item["kind"] == "sentence" else meaning_of(item["translation"]),
        "spoken_key": " ".join(SPOKEN.get(w, w) for w in item["key"].split()),
    }


async def ensure_contexts(items: list[dict], cfg: dict) -> list[str]:
    """没有语境句的词先生成；AI 出错或没给出合格句子时，用原文句子兜底"""
    warnings: list[str] = []
    missing = [it for it in items if not any(ai.make_cloze(c["sentence"], c["answer"])
                                            for c in store.contexts_of(it["key"]))]
    for it in [m for m in missing if m["kind"] == "sentence"]:
        store.add_contexts(it["key"], [{"sentence": it["key"], "answer": [it["key"]], "hint": it["translation"],
                                        "note": "整句听写"}], "sentence")
    missing = [m for m in missing if m["kind"] != "sentence"]
    if not missing:
        return warnings
    got: dict[str, list[dict]] = {}
    provider = cfg["ai"]["provider"]
    if provider != "original":
        reqs = []
        for it in missing:
            exs = store.examples_of(it["key"])
            reqs.append({"key": it["key"], "kind": it["kind"], "translation": it["translation"],
                         "source": exs[0]["sentence"] if exs else ""})
        try:
            got = await asyncio.to_thread(ai.generate, reqs, cfg)
        except ai.AIError as e:
            warnings.append(f"AI 生成失败，先用原文句子：{e}")
        except Exception as e:  # 网络、解析之外的意外
            warnings.append(f"AI 生成出错，先用原文句子：{e.__class__.__name__}")
    for it in missing:
        if got.get(it["key"]):
            store.add_contexts(it["key"], got[it["key"]], f"ai:{provider}")
        else:
            orig = ai.from_original(it, store.examples_of(it["key"]))
            if orig:
                store.add_contexts(it["key"], orig, "original")
            else:
                warnings.append(f"“{it['key']}”没有能挖空的句子，跳过")
    return warnings


# ---------- 扫描 ----------

def scan_text(content: str, cfg: dict) -> tuple[list[dict], dict]:
    """AI 逐句分析（翻译、意群、本句释义）；AI 用不了或出错时，词组退回词典匹配。返回（候选，meta）"""
    sentences = extract.usable_sentences(content)
    warnings: list[str] = []
    analysis: dict = {}
    source = "dict"
    if cfg.get("extract", {}).get("phrases", "ai") == "ai":
        reason = ai.unavailable_reason(cfg)
        if reason and cfg["ai"]["provider"] != "original":  # 自己选了“不用 AI”就不提示
            warnings.append(f"没有用 AI 分析，词组改用词典匹配：{reason}")
        elif not reason:
            try:
                analysis = ai.analyze(sentences, cfg)
                warnings += analysis["warnings"]
                source = "ai" if len(analysis["covered"]) >= len(sentences) else "mixed"
            except ai.AIError as e:
                warnings.append(f"AI 分析失败，词组改用词典匹配：{e}")
                analysis = {}
            except Exception as e:  # 网络、解析之外的意外
                warnings.append(f"AI 分析出错，词组改用词典匹配：{e.__class__.__name__}")
                analysis = {}
    cands = [c.to_dict() for c in extract.extract(content, analysis=analysis)]
    meta = {"phrases": source, "aiPhrases": sum(1 for c in cands if c["source"] == "ai" and c["kind"] == "phrase"),
            "warnings": warnings, "sents": analysis.get("sents", [])}
    return cands, meta


# ---------- 接口 ----------

@app.get("/api/stats")
def api_stats():
    return store.stats()


@app.get("/api/settings")
def api_settings():
    cfg = store.public_config()
    cfg["levels"] = [{"id": k, "name": lx.LEVEL_NAMES[k]} for k in lx.LEVELS]
    return cfg


@app.post("/api/settings")
def api_save_settings(patch: dict = Body(...)):
    if "session_size" in patch.get("study", {}):
        patch["study"]["session_size"] = session_size(patch["study"]["session_size"])
    if "daily_new" in patch.get("study", {}):
        daily = patch["study"]["daily_new"]
        if isinstance(daily, bool) or not isinstance(daily, int) or not 0 <= daily <= 1000:
            err("每天新词须为 0（不限）或 1–1000 的整数")
    # 网页上显示的是打码的 Key；没改就别覆盖
    for p in ("deepseek", "anthropic", "openai"):
        k = patch.get("ai", {}).get(p, {}).get("key")
        if isinstance(k, str) and k.startswith("••••"):
            patch["ai"][p].pop("key")
    store.save_config(patch)
    return api_settings()


@app.post("/api/settings/test")
async def api_test_ai():
    cfg = store.load_config()
    if cfg["ai"]["provider"] == "original":
        return {"ok": True, "sample": "当前是“原文句子”模式，不调用 AI"}
    try:
        sample = await asyncio.to_thread(ai.test_connection, cfg)
        return {"ok": True, "sample": sample}
    except ai.AIError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/docs")
async def api_add_doc(file: UploadFile | None = File(None), text: str = Form(""), title: str = Form("")):
    if file is not None and file.filename:
        data = await file.read()
        if len(data) > 20 * 1024 * 1024:
            err("文件太大了（超过 20MB）")
        try:
            content = docs.to_text(file.filename, data)
        except docs.UnsupportedFile as e:
            err(str(e))
        filename = file.filename
        title = title or Path(file.filename).stem
    else:
        content, filename = text, ""
        if not title and text.strip():
            first = text.strip().split("\n")[0]
            title = first if len(first) <= 40 else first[:40].rsplit(" ", 1)[0] + "…"
    if not content.strip():
        err("文档里没有文字")
    if not re.search(r"[A-Za-z]{3,}", content):
        err("没找到英文内容")
    cands, meta = await asyncio.to_thread(scan_text, content, store.load_config())
    doc_id = store.add_doc(title or "未命名", filename, content, cands, meta)
    prefetch_sentences(content)
    return api_doc(doc_id, None, None, False)


def prefetch_sentences(content: str) -> None:
    cfg = store.load_config()["tts"]
    tts.prefetch(extract.usable_sentences(content)[:300], cfg["voice"], cfg["rate"])


@app.post("/api/docs/{doc_id}/phrases")
async def api_rescan_phrases(doc_id: int):
    """重新识别词组（比如刚填好 Key，或者上次 AI 出错退回了词典）"""
    d = store.get_doc(doc_id)
    if not d:
        err("文档不存在", 404)
    cands, meta = await asyncio.to_thread(scan_text, d["text"], store.load_config())
    store.update_doc(doc_id, cands, meta)
    prefetch_sentences(d["text"])
    return api_doc(doc_id, None, None, False)


@app.get("/api/docs")
def api_docs():
    return store.list_docs()


@app.get("/api/docs/{doc_id}")
def api_doc(doc_id: int, hide_level: str | None = Query(None), hide_rank: int | None = Query(None),
            show_all: bool = Query(False)):
    d = store.get_doc(doc_id)
    if not d:
        err("文档不存在", 404)
    cfg = store.load_config()["filter"]
    known = store.known_set()
    in_list = {r["key"] for r in store.q("SELECT key FROM items")}
    if show_all:
        cands = [c for c in d["cands"] if c["key"] not in known]
    else:
        cands = extract.filter_candidates(d["cands"], hide_level or cfg["hide_level"],
                                          cfg["hide_rank"] if hide_rank is None else hide_rank, known)
    for c in cands:
        c["inList"] = c["key"] in in_list
    order = {"phrase": 0, "word": 1}
    cands.sort(key=lambda c: (order[c["kind"]], -c["count"], -(c["rank"] or 10**6)))
    return {"id": d["id"], "title": d["title"], "filename": d["filename"], "chars": d["chars"],
            "total": len(d["cands"]), "cands": cands,
            "totalWords": sum(1 for c in d["cands"] if c["kind"] == "word"),
            "totalPhrases": sum(1 for c in d["cands"] if c["kind"] == "phrase"),
            "meta": {k: v for k, v in d["meta"].items() if k != "sents"}, "hasAnalysis": bool(d["meta"].get("sents"))}


@app.get("/api/docs/{doc_id}/sentences")
def api_doc_sentences(doc_id: int):
    """逐句解析：每句的中文翻译和意群。没经过 AI 分析的文档只给原句（照样可以划选加词）"""
    d = store.get_doc(doc_id)
    if not d:
        err("文档不存在", 404)
    sents = d["meta"].get("sents") or dict_sentences(d)
    in_list = {r["key"] for r in store.q("SELECT key FROM items")}
    for s in sents:
        s["inList"] = re.sub(r"\s+", " ", s["text"]).strip() in in_list
        for u in s["units"]:
            u["inList"] = u["form"] in in_list or lx.lemma(u["form"]) in in_list
    return sents


def dict_sentences(d: dict) -> list[dict]:
    """没经过 AI 分析时的逐句解析：原句 + 词典认出的词组（扫描时记下的例句位置）"""
    texts = extract.usable_sentences(d["text"])
    index = {t: i for i, t in enumerate(texts)}
    sents = [{"sid": i, "text": t, "zh": "", "units": []} for i, t in enumerate(texts)]
    for c in d["cands"]:
        if c["kind"] != "phrase" or c.get("basic"):
            continue
        for ex in c["examples"]:
            sid = index.get(ex["sentence"])
            if sid is None:
                continue
            pieces = ex.get("pieces") or ai.original_pieces(c["key"], "phrase", ex["surface"])
            segs = ai.make_cloze(ex["sentence"], pieces)
            if not segs:
                continue
            sents[sid]["units"].append({"pieces": [x["blank"] for x in segs if "blank" in x], "form": c["key"],
                                        "type": c.get("ptype") or "phrase", "meaning": c["translation"].split("；")[0][:30],
                                        "spans": ai._spans(segs)})
    for s in sents:
        s["units"].sort(key=lambda u: u["spans"][0][0])
    return sents


@app.post("/api/items/pick")
async def api_pick_items(body: dict = Body(...)):
    """从逐句解析里加词：勾选的意群（带 form/meaning），或者自己划选的任意文字（只有 text）。
    body: {doc_id, picks: [{sentence, text 或 pieces, form?, type?, meaning?}]}"""
    d = store.get_doc(int(body["doc_id"]))
    if not d:
        err("文档不存在", 404)
    cfg = store.load_config()
    cands, keys, warnings = [], [], []
    for pk in body.get("picks", [])[:200]:
        sentence = str(pk.get("sentence", ""))
        if pk.get("whole"):  # 整句加入：练的时候整句听写
            key = re.sub(r"\s+", " ", sentence).strip()
            if not key or len(key) > 400:
                warnings.append("句子为空或太长（超过 400 个字符）")
                continue
            cands.append({"key": key, "kind": "sentence", "translation": str(pk.get("meaning") or "").strip(),
                          "phonetic": "", "tags": [], "level": None, "rank": 0, "ctx": "",
                          "examples": [{"sentence": sentence, "surface": sentence, "pieces": [sentence]}]})
            keys.append(key)
            continue
        pieces = pk.get("pieces") or [str(pk.get("text", "")).strip()]
        pieces = [re.sub(r"\s+", " ", p).strip(" ,.;:!?\"'“”‘’()") for p in pieces if p and p.strip()]
        segs = ai.make_cloze(sentence, pieces) if pieces and all(pieces) else None
        if not segs or len(" ".join(pieces)) > 80:
            warnings.append(f"“{' … '.join(pieces)[:30]}”在这句里找不到，或者太长了")
            continue
        pieces = [x["blank"] for x in segs if "blank" in x]
        spans = ai._spans(segs)
        surface = sentence[spans[0][0] : spans[-1][1]]
        words = " ".join(pieces).split()
        form = str(pk.get("form") or "").strip().lower()
        kind_ai = str(pk.get("type") or "")
        meaning = str(pk.get("meaning") or "").strip()
        if not form:  # 自己划选的：先查词典，再问 AI 这句里是什么意思
            if len(words) == 1:
                form = lx.lemma(words[0])
            else:
                form = " ".join(w.lower().replace("’", "'") for w in words)
            if not meaning and not ai.unavailable_reason(cfg):
                try:
                    g = await asyncio.to_thread(ai.gloss, " … ".join(pieces), sentence, cfg)
                    meaning = str(g.get("meaning_zh", "")).strip()[:30]
                    kind_ai = str(g.get("type", ""))
                    if len(words) > 1 and ai.normalize_phrase(g.get("form", "")):
                        form = ai.normalize_phrase(g["form"])
                    elif len(words) == 1 and str(g.get("form", "")).isalpha() and not lx.word(form):
                        form = g["form"].lower()
                except ai.AIError as e:
                    warnings.append(f"没查到“{form}”在这句里的意思：{e}")
        kind = "word" if len(form.split()) == 1 else "phrase"
        c = {"key": form, "kind": kind, "translation": "", "phonetic": "", "tags": [], "level": None, "rank": 0,
             "ctx": "", "examples": [{"sentence": sentence, "surface": surface, "pieces": pieces, "meaning": meaning}]}
        if kind == "word":
            info = lx.word(form)
            if info:
                c.update(translation=lx.short_tr(info["translation"]), phonetic=info["phonetic"], tags=info["tags"],
                         level=info["level"], rank=info["rank"])
            c["ctx"] = meaning
            if not c["translation"]:
                c["translation"], c["ctx"] = meaning, ""
        else:
            info = lx.phrase(form)
            c["translation"] = meaning or (lx.short_tr(info["translation"]) if info else "")
        if not c["translation"]:
            c["translation"] = "（自选）"
        cands.append(c)
        keys.append(form)
    added = store.add_items(cands, d["id"]) if cands else 0
    return {"added": added, "keys": keys, "warnings": warnings}


@app.delete("/api/docs/{doc_id}")
def api_del_doc(doc_id: int):
    store.delete_doc(doc_id)
    return {"ok": True}


@app.post("/api/items")
def api_add_items(body: dict = Body(...)):
    d = store.get_doc(int(body["doc_id"]))
    if not d:
        err("文档不存在", 404)
    keys = set(body.get("keys", []))
    picked = [c for c in d["cands"] if c["key"] in keys]
    return {"added": store.add_items(picked, d["id"])}


@app.get("/api/items")
def api_items():
    return store.list_items()


@app.get("/api/item")
def api_item(key: str):
    it = store.get_item(key)
    if not it:
        err("不在生词本里", 404)
    it["examples"] = store.examples_of(key)
    it["contextList"] = store.contexts_of(key)
    return it


@app.delete("/api/item")
def api_del_item(key: str):
    store.remove_item(key)
    return {"ok": True}


@app.post("/api/item/regenerate")
async def api_regen(body: dict = Body(...)):
    key = body["key"]
    it = store.get_item(key)
    if not it:
        err("不在生词本里", 404)
    store.delete_contexts(key)
    warnings = await ensure_contexts([it], store.load_config())
    return {"warnings": warnings, "contextList": store.contexts_of(key)}


@app.post("/api/known")
def api_known(body: dict = Body(...)):
    store.set_known(body["key"], bool(body.get("on", True)))
    return {"ok": True}


@app.get("/api/known")
def api_known_list():
    return store.q("SELECT key, added FROM known ORDER BY added DESC")


@app.post("/api/contexts/generate")
async def api_generate(body: dict = Body(default={})):
    keys = body.get("keys")
    items = [store.get_item(k) for k in keys] if keys else store.list_items()
    items = [i for i in items if i]
    warnings = await ensure_contexts(items, store.load_config())
    return {"warnings": warnings}


def daily_new_quota(cfg: dict, limit: int | None) -> int | None:
    """每日新词设为 0 时不限量，但仍遵守本轮剩余名额。"""
    daily = cfg["study"]["daily_new"]
    if daily == 0:
        return limit
    room = max(0, daily - store.learned_today())
    return room if limit is None else min(limit, room)


def pick_items(mode: str, limit: int | None) -> list[dict]:
    cfg = store.load_config()
    if mode == "review":
        return store.due_items(limit)
    if mode == "learn":
        return store.new_items(daily_new_quota(cfg, limit))
    if mode == "all":  # 生词本全部过一遍（不限当天）
        return store.list_items()[:limit]
    # mix：先复习到期的，再学新词
    due = store.due_items(limit)
    remaining = None if limit is None else max(0, limit - len(due))
    return due + store.new_items(daily_new_quota(cfg, remaining))


def session_size(value) -> int:
    """0 表示本轮不限量；每天新词额度单独计算。"""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000:
        err("每轮词数须为 0（全部）或 1–1000 的整数")
    return value


def session_items(body: dict, cfg: dict) -> list[dict]:
    mode = body.get("mode", "mix")
    if mode not in {"mix", "review", "learn", "all"}:
        err("未知的练习方式")
    keys = body.get("keys")
    if keys is not None:
        if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys):
            err("词条列表格式不正确")
        items = [it for k in dict.fromkeys(keys) if (it := store.get_item(k))]
    else:
        size = session_size(body.get("limit", cfg["study"]["session_size"]))
        # “全部过一遍”始终覆盖全本，不受旧客户端传来的 limit 或每日新词额度限制。
        items = pick_items(mode, None if mode == "all" or size == 0 else size)
    if keys is None or not body.get("ordered", False):
        random.shuffle(items)
    return items


@app.post("/api/session/plan")
def api_session_plan(body: dict = Body(default={})):
    """固定本轮范围和随机顺序；语境句由客户端分批请求，避免一次等全本生成。"""
    cfg = store.load_config()
    keys = [it["key"] for it in session_items(body, cfg)]
    return {"keys": keys, "total": len(keys), "readMode": cfg["study"]["read_mode"]}


@app.post("/api/session")
async def api_session(body: dict = Body(default={})):
    cfg = store.load_config()
    items = session_items(body, cfg)
    warnings = await ensure_contexts(items, cfg)
    cards = []
    for it in items:
        for ctx in store.contexts_of(it["key"]):
            card = make_card(it, ctx)
            if card:
                cards.append(card)
                break
    prepared = {c["key"] for c in cards}
    requested = body.get("keys") if body.get("keys") is not None else [it["key"] for it in items]
    skipped = [key for key in dict.fromkeys(requested) if key not in prepared]
    if skipped:
        warnings.append(f"有 {len(skipped)} 个词暂时无法出题，结束时会列出，未计入已练词数")
    return {"cards": cards, "warnings": warnings, "skipped": skipped}


@app.post("/api/review")
def api_review(body: dict = Body(...)):
    if body.get("ctxId"):
        store.mark_used(int(body["ctxId"]))
    res = store.record(body["key"], int(body.get("mistakes", 0)), bool(body.get("peeked")), bool(body.get("hinted")))
    if not res:
        err("不在生词本里", 404)
    return res


@app.get("/api/tts")
async def api_tts(text: str, lang: str = "en", slow: bool = False):
    if not tts.speakable(text) or len(text) > 600:
        err("没有可朗读的文字，或者太长")
    cfg = store.load_config()["tts"]
    voice = cfg["zh_voice"] if lang == "zh" else cfg["voice"]
    rate = "-25%" if slow else (cfg["rate"] if lang != "zh" else "+0%")
    try:
        path = await tts.synth(text, voice, rate)
    except tts.TTSError as e:
        err(str(e), 503)
    media = {".mp3": "audio/mpeg", ".wav": "audio/wav"}.get(path.suffix, "audio/mp4")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "max-age=31536000"})


@app.post("/api/export/mp3")
async def api_export_mp3(body: dict = Body(default={})):
    mode = body.get("mode", "mix")
    limit = max(1, min(200, int(body.get("limit", 30))))
    items = [i for i in pick_items(mode, limit) if i] if not body.get("keys") else \
        [i for i in (store.get_item(k) for k in body["keys"]) if i]
    if not items:
        err("没有可以导出的词")
    cfg = store.load_config()
    await ensure_contexts(items, cfg)
    cards = []
    for it in items:
        ctxs = store.contexts_of(it["key"])
        card = make_card(it, ctxs[0]) if ctxs else None
        if card:
            cards.append(card)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out = store.EXPORT_DIR / f"川听-{stamp}-{len(cards)}词.mp3"
    await tts.build_pack(cards, cfg, out, int(cfg["study"]["pause_ms"]))
    return FileResponse(out, media_type="audio/mpeg", filename=out.name)


@app.get("/api/export/wordbook")
def api_export_wordbook():
    """导出成川词的 wordbook/1 文件"""
    entries = []
    for it in store.list_items():
        exs = store.examples_of(it["key"])
        ctxs = store.contexts_of(it["key"])
        note = "\n\n".join([e["sentence"] for e in exs[:2]] + [c["sentence"] for c in ctxs[:2]])
        entries.append({"word": it["key"], "note": note, "tags": ["川听"], "customTrans": it["translation"], "groups": []})
    data = {"format": "wordbook/1", "name": "川听生词", "desc": "从文档里扫出的单词和词组",
            "exportedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "appVersion": "1.0", "languageCode": "en", "entries": entries}
    name = f"川听生词-{datetime.now():%Y%m%d}.json"
    return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{__import__('urllib.parse').parse.quote(name)}"})


@app.post("/api/quit")
def api_quit(request: Request):
    """关闭川听（从“川听.app”在后台启动时没有终端窗口，用这个退出）。
    要求自定义请求头：别的网页跨域发不了带这个头的请求，没法偷偷关掉它。"""
    if request.headers.get("x-chuanting") != "1":
        err("不允许", 403)
    def stop() -> None:
        if UVICORN is not None:
            UVICORN.should_exit = True  # 正常退出，Windows 上也适用
        else:
            os.kill(os.getpid(), signal.SIGINT)

    threading.Timer(0.4, stop).start()
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_error(_req, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=WEB), name="web")
