"""川听自测：.venv/bin/python tests/run_tests.py [--tts]

用临时数据目录（CHUANTING_HOME），不碰真实学习记录。AI 调用用假的返回值模拟，不花钱。
加 --tts 会真的连微软语音合成，并导出一个小 MP3。
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CHUANTING_HOME"] = tempfile.mkdtemp(prefix="chuanting-test-")
os.environ["CHUANTING_NO_PREFETCH"] = "1"  # 不在后台连微软合成语音
for k in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(k, None)

from fastapi.testclient import TestClient  # noqa: E402

from chuanting import ai, docs, extract, server, srs, store  # noqa: E402

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}  {detail}")


SAMPLE = (ROOT / "tests/sample.txt").read_text()

print("抽词")
cands = [c.to_dict() for c in extract.extract(SAMPLE)]
keys = {c["key"] for c in cands}
for p in ["take off", "give up on", "turn sb down", "put up with", "carry out", "in fact", "look forward to",
          "break down", "make a decision", "get rid of", "as a result", "shake sb's hand"]:
    check(f"认出词组 {p}", p in keys)
check("不误认 have sb up", "have sb up" not in keys)
f = {c["key"] for c in extract.filter_candidates(cands)}
for w in ["ubiquitous", "ephemeral", "eloquent", "reluctant", "anticipate", "allegation"]:
    check(f"保留生词 {w}", w in f)
for w in ["plane", "hour", "meeting", "need to", "try to", "smith", "sydney"]:
    check(f"过滤掉 {w}", w not in f)
take_off = next(c for c in cands if c["key"] == "turn sb down")
check("记下原文表面形式", take_off["examples"][0]["surface"] == "turned the offer down")
check("分句识别 Mr. 缩写", any(ex["sentence"].startswith("The plane took off") and "Mr. Smith" in ex["sentence"]
                            for c in cands for ex in c["examples"]))

print("挖空")
segs = ai.make_cloze("She turned the offer down at once.", ["turned", "down"])
check("拆开的词组挖两个空", [s.get("blank") for s in segs if "blank" in s] == ["turned", "down"])
check("答案不存在时返回 None", ai.make_cloze("Hello world.", ["took off"]) is None)
check("不在单词中间误挖", ai.make_cloze("They often fight.", ["ten"]) is None)
check("原文兜底只挖词组本身", ai.original_pieces("turn sb down", "phrase", "turned the offer down") == ["turned", "down"])
check("原文兜底 shake sb's hand", ai.original_pieces("shake sb's hand", "phrase", "shook the minister's hand") == ["shook", "hand"])

print("文档解析")
md = "# Title\n\nThis is **bold** and a [link](http://x.y).\n\n- item one\n```\ncode\n```"
t = docs.markdown_to_text(md)
check("Markdown 去格式", "bold" in t and "**" not in t and "http" not in t and "code" not in t, t)
import docx as _docx  # noqa: E402

d = _docx.Document()
d.add_paragraph("The committee came up with a compromise.")
buf = io.BytesIO()
d.save(buf)
check("读取 .docx", "came up with" in docs.to_text("a.docx", buf.getvalue()))
check("GBK 编码的 txt", "ubiquitous" in docs.to_text("a.txt", "中文 ubiquitous".encode("gb18030")))
try:
    docs.to_text("a.doc", b"x")
    check("拒绝 .doc", False)
except docs.UnsupportedFile:
    check("拒绝 .doc", True)

print("FSRS")
st = srs.learn(srs.GOOD, "2026-11-08")
check("新词最多按“模糊”起步，明天复习", st["due"] == "2026-11-09", st)
st2 = srs.review(st, srs.GOOD, "2026-11-09")
check("记得后间隔变长", srs.days_between("2026-11-09", st2["due"]) >= 2, st2)
st3 = srs.review(st2, srs.AGAIN, st2["due"])
check("忘记后稳定度下降", st3["s"] < st2["s"] and st3["lapses"] == 1)
check("同一天第二遍不改排程", srs.review(st2, srs.AGAIN, "2026-11-09") == st2)
check("评分：错 3 次=忘记", srs.grade(3, False) == 1 and srs.grade(1, False) == 2 and srs.grade(0, False) == 3)

print("接口（原文模式）")
c = TestClient(server.app)
r = c.post("/api/settings", json={"ai": {"provider": "original"}})
check("保存设置", r.status_code == 200 and r.json()["ai"]["provider"] == "original")
r = c.post("/api/docs", files={"file": ("sample.md", SAMPLE.encode(), "text/markdown")})
check("上传文档", r.status_code == 200, r.text[:200])
doc = r.json()
check("返回筛选后的候选", doc["cands"] and doc["cands"][0]["kind"] == "phrase" and len(doc["cands"]) < doc["total"],
      f'{len(doc["cands"])} / {doc["total"]}')
r = c.post("/api/docs", files={"file": ("x.pdf", b"%PDF", "application/pdf")})
check("不支持的格式给出中文提示", r.status_code == 400 and "支持" in r.json()["error"])
pick = ["turn sb down", "ubiquitous", "get rid of"]
r = c.post("/api/items", json={"doc_id": doc["id"], "keys": pick})
check("加入生词本", r.json()["added"] == 3)
r = c.post("/api/session", json={"mode": "learn"})
cards = r.json()["cards"]
check("出 3 张卡", len(cards) == 3, r.json())
card = next(x for x in cards if x["key"] == "turn sb down")
check("原文模式的卡片", card["answer"] == ["turned", "down"] and card["source"] == "original")
r = c.post("/api/review", json={"key": card["key"], "ctxId": card["ctxId"], "mistakes": 0})
check("提交复习", r.json()["state"]["due"] == srs.add_days(srs.today(), 1))
check("统计", c.get("/api/stats").json()["new"] == 2)
c.post("/api/known", json={"key": "ubiquitous", "on": True})
check("标为已会后从生词本移除", "ubiquitous" not in {i["key"] for i in c.get("/api/items").json()})
d2 = c.get(f"/api/docs/{doc['id']}").json()
check("已会的词不再出现在扫描结果", "ubiquitous" not in {x["key"] for x in d2["cands"]})
wb = c.get("/api/export/wordbook")
data = json.loads(wb.content)
check("导出 wordbook/1", data["format"] == "wordbook/1" and len(data["entries"]) == 2)

print("接口（模拟 AI）")
fake = {"items": [{"key": "get rid of", "contexts": [
    {"sentence": "We finally got rid of the old sofa last weekend.", "answer": ["got rid of"],
     "hint_zh": "上周末我们终于把旧沙发处理掉了。", "note_zh": "get rid of 摆脱、扔掉"},
    {"sentence": "This sentence lacks the target.", "answer": ["got rid of"], "hint_zh": "", "note_zh": ""}]}]}
ai._call_openai_compatible = lambda *a, **k: json.dumps(fake)
c.post("/api/settings", json={"ai": {"provider": "deepseek", "deepseek": {"key": "sk-test1234"}}})
check("Key 打码显示", c.get("/api/settings").json()["ai"]["deepseek"]["key"] == "••••1234")
c.post("/api/settings", json={"ai": {"deepseek": {"key": "••••1234"}}})
check("打码的 Key 不覆盖原值", store.load_config()["ai"]["deepseek"]["key"] == "sk-test1234")
r = c.post("/api/item/regenerate", json={"key": "get rid of"})
ctx = r.json()["contextList"]
check("AI 句子入库、丢弃不合格的", len(ctx) == 1 and ctx[0]["source"] == "ai:deepseek", ctx)
ai._call_openai_compatible = lambda *a, **k: (_ for _ in ()).throw(ai.AIError("API Key 不对"))
store.delete_contexts("get rid of")
r = c.post("/api/session", json={"mode": "all"})
check("AI 出错时用原文兜底并提示", r.json()["cards"] and "API Key 不对" in " ".join(r.json()["warnings"]))

print("AI 逐句分析（模拟）")
# sample.txt 的第 1、2、7、8 句（编号从 1 开始）
AN = {"sentences": [
    {"sid": 1, "zh": "飞机晚点两小时起飞……", "units": [
        {"text": ["took off"], "form": "Take off", "type": "phrasal verb", "meaning_zh": "起飞"},
        {"text": ["given up on"], "form": "give up on", "type": "phrasal verb", "meaning_zh": "对…死心"},
        {"text": ["Smith"], "form": "smith", "type": "word", "meaning_zh": ""}]},
    {"sid": 2, "zh": "她拒绝了这个提议，因为受不了漫长的通勤。", "units": [
        {"text": ["turned", "down"], "form": "turn something down", "type": "phrasal verb", "meaning_zh": "拒绝"},
        {"text": ["put up with"], "form": "put up with", "type": "phrasal verb", "meaning_zh": "忍受"},
        {"text": ["long commute"], "form": "long commute", "type": "collocation", "meaning_zh": "长途通勤"},
        {"text": ["commute"], "form": "commute", "type": "word", "meaning_zh": "通勤"},
        {"text": ["offer"], "form": "offer", "type": "word", "meaning_zh": "工作邀约"},
        {"text": ["gave up"], "form": "give up", "type": "phrasal verb", "meaning_zh": "放弃"}]},
    {"sid": 7, "zh": "科学家正在设法清除无处不在的微塑料……", "units": [
        {"text": ["get rid of"], "form": "get rid of", "type": "phrasal verb", "meaning_zh": "清除"},
        {"text": ["ubiquitous"], "form": "ubiquitous", "type": "word", "meaning_zh": "无处不在的"},
        {"text": ["microplastics"], "form": "microplastic", "type": "word", "meaning_zh": "微塑料"}]},
    {"sid": 8, "zh": "她雄辩的演讲激励了犹豫的选民……", "units": [
        {"text": ["galvanised"], "form": "galvanise", "type": "word", "meaning_zh": "激励"},
        {"text": ["on the fence"], "form": "on the fence", "type": "idiom", "meaning_zh": "犹豫不决"}]},
    {"sid": 99, "zh": "不存在的句子", "units": []}]}
calls = []


def fake_ai(base, key, model, prompt, system=ai.SYSTEM, temperature=0.8, max_tokens=6000):
    calls.append(system)
    if system == ai.ANALYZE_SYSTEM:
        return json.dumps(AN) if "[1]" in prompt else json.dumps({"sentences": []})
    return json.dumps(fake)


ai._call_openai_compatible = fake_ai
store.set_known("ubiquitous", False)
store.save_config({"ai": {"provider": "deepseek", "deepseek": {"key": "sk-test1234"}}, "extract": {"phrases": "ai"}})
d3 = c.post("/api/docs", files={"file": ("ai.txt", SAMPLE.encode(), "text/plain")}).json()
allc = {x["key"]: x for x in store.get_doc(d3["id"])["cands"]}
pk = {k: x for k, x in allc.items() if x["kind"] == "phrase"}
check("AI 分析过的句子用 AI 的词组", "take off" in pk and pk["take off"]["source"] == "ai" and pk["take off"]["ptype"] == "phrasal verb", list(pk))
check("词组规范成词典形式", "turn sth down" in pk, list(pk))
check("词典里没有的搭配也收下", "long commute" in pk and pk["long commute"]["translation"] == "长途通勤")
check("原文里没有的、编号不对的丢掉", "give up" not in pk and "on the fence" not in pk)
check("对不上时有提示", any("对不上" in w for w in d3["meta"]["warnings"]), d3["meta"])
check("AI 没返回的句子仍用词典匹配", "make a decision" in pk and pk["make a decision"]["source"] == "dict")
check("AI 返回了的句子不再用词典匹配", "pension scheme" not in pk or pk["pension scheme"]["count"] == 1)
check("词组里的单词不再单独列出", "commute" not in allc or all("commute" not in ex["sentence"] for ex in allc["commute"]["examples"]),
      allc.get("commute"))
check("单词带上本文里的意思", allc["offer"]["ctx"] == "工作邀约" and allc["offer"]["aiPick"])
check("AI 挑出的常见词不被等级筛掉", any(x["key"] == "offer" for x in d3["cands"]))
check("词典里没有的难词也收下", "microplastic" in allc and allc["microplastic"]["translation"] == "微塑料", list(allc)[:40])
check("英式拼写对上词典原形", "galvanise" in allc or "galvanize" in allc or allc.get("galvanised", {}).get("ctx") == "激励")
check("人名不算", "smith" not in allc)
check("拆开的词组记下片段", pk["turn sth down"]["examples"][0]["pieces"] == ["turned", "down"]
      and pk["turn sth down"]["examples"][0]["surface"] == "turned the offer down")
sents = c.get(f"/api/docs/{d3['id']}/sentences").json()
check("逐句解析有翻译和意群", len(sents) == 4 and sents[1]["zh"].startswith("她拒绝") and
      [u["form"] for u in sents[1]["units"]][:3] == ["turn sth down", "put up with", "long commute"], sents[1] if sents else sents)
check("文档接口不带整份解析", "sents" not in d3["meta"] and d3["hasAnalysis"])
c.post("/api/items", json={"doc_id": d3["id"], "keys": ["long commute", "offer"]})
check("生词本里单词的释义先写本文意思", store.get_item("offer")["translation"].startswith("本文：工作邀约"))
c.post("/api/settings", json={"ai": {"provider": "original"}})
r = c.post("/api/session", json={"keys": ["long commute"]})
check("AI 词组能用原文挖空练", r.json()["cards"] and r.json()["cards"][0]["answer"] == ["long commute"], r.json())

c.post("/api/settings", json={"ai": {"provider": "deepseek"}})
ai._call_openai_compatible = lambda *a, **k: (_ for _ in ()).throw(ai.AIError("账户余额不足"))
d4 = c.post("/api/docs", files={"file": ("x.txt", SAMPLE.encode(), "text/plain")}).json()
check("AI 出错时退回词典并提示", d4["meta"]["phrases"] == "dict" and "余额不足" in " ".join(d4["meta"]["warnings"])
      and any(x["key"] == "put up with" for x in d4["cands"]), d4["meta"])
dc = {x["key"]: x for x in store.get_doc(d4["id"])["cands"]}
check("词典模式下词组里的词也不单独列", "rid" not in dc and all("get rid of" not in ex["sentence"] for ex in dc.get("get", {}).get("examples", [])))
check("词典模式下 sth 位置的生词照常列出", "allegation" in dc)
ai._call_openai_compatible = fake_ai
d5 = c.post(f"/api/docs/{d4['id']}/phrases").json()
check("重新分析", d5["meta"]["phrases"] in ("ai", "mixed") and any(x["key"] == "long commute" for x in d5["cands"]), d5["meta"])
old_max = ai.ANALYZE_MAX_CHARS
ai.ANALYZE_MAX_CHARS = 300
d6 = c.post("/api/docs", files={"file": ("long.txt", SAMPLE.encode(), "text/plain")}).json()
ai.ANALYZE_MAX_CHARS = old_max
check("长文档前半 AI、后半词典", d6["meta"]["phrases"] == "mixed" and any(x["key"] == "get rid of" for x in d6["cands"]), d6["meta"])
c.post("/api/settings", json={"extract": {"phrases": "dict"}})
n = len(calls)
d7 = c.post("/api/docs", files={"file": ("y.txt", SAMPLE.encode(), "text/plain")}).json()
check("设成只用词典时不调用 AI", len(calls) == n and d7["meta"]["phrases"] == "dict" and not d7["meta"]["warnings"])

print("逐句解析里加词")
S7 = "Scientists are trying to get rid of the ubiquitous microplastics, an ephemeral but persistent problem."
S4 = "In fact, the committee came up with a compromise that nobody had anticipated."


def fake_ai2(base, key, model, prompt, system=ai.SYSTEM, temperature=0.8, max_tokens=6000):
    if system == ai.GLOSS_SYSTEM:
        if "persistent problem" in prompt:
            return json.dumps({"form": "persistent problem", "type": "collocation", "meaning_zh": "顽疾"})
        return json.dumps({"form": "anticipate", "type": "word", "meaning_zh": "预料到"})
    return fake_ai(base, key, model, prompt, system, temperature, max_tokens)


ai._call_openai_compatible = fake_ai2
c.post("/api/settings", json={"ai": {"provider": "deepseek"}, "extract": {"phrases": "ai"}})
r = c.post("/api/items/pick", json={"doc_id": d3["id"], "picks": [
    {"sentence": S7, "pieces": ["get rid of"], "form": "get rid of", "type": "phrasal verb", "meaning": "清除"}]}).json()
check("勾选意群加入生词本", "get rid of" in r["keys"] and store.get_item("get rid of") is not None, r)
r = c.post("/api/items/pick", json={"doc_id": d3["id"], "picks": [{"sentence": S4, "text": "anticipated"}]}).json()
it = store.get_item("anticipate")
check("划选单个变形词：还原原形，查本句意思", r["keys"] == ["anticipate"] and it and it["translation"].startswith("本文：预料到"), (r, it))
r = c.post("/api/items/pick", json={"doc_id": d3["id"], "picks": [{"sentence": S7, "text": " persistent problem. "}]}).json()
it = store.get_item("persistent problem")
check("划选词典里没有的多个词：当词组，用 AI 的意思", it and it["kind"] == "phrase" and it["translation"] == "顽疾", (r, it))
ex = store.examples_of("persistent problem")
check("自选词组记下原句和片段", ex and ex[0]["pieces"] == ["persistent problem"] and ex[0]["sentence"] == S7, ex)
r = c.post("/api/items/pick", json={"doc_id": d3["id"], "picks": [{"sentence": S7, "text": "flying saucer"}]}).json()
check("原句里没有的文字不收，并提示", r["added"] == 0 and r["warnings"], r)
sents = c.get(f"/api/docs/{d3['id']}/sentences").json()
grid = next(u for s_ in sents for u in s_["units"] if u["form"] == "get rid of")
check("逐句解析标出已在生词本的意群", grid["inList"] is True)
plain = c.get(f"/api/docs/{d7['id']}/sentences").json()
check("没经过 AI 分析的文档也给原句，方便划选", plain and plain[0]["text"].startswith("The plane") and not plain[0]["zh"])
forms0 = [u["form"] for u in plain[0]["units"]]
check("没经过 AI 分析时逐句解析标出词典词组", "take off" in forms0 and "give up on" in forms0, forms0)
tod = next(u for u in plain[1]["units"] if u["form"] == "turn sb down")
check("词典词组拆开写也标对位置", tod["pieces"] == ["turned", "down"], tod)
check("文档接口给出生词数和词组数", d7["totalWords"] > 0 and d7["totalPhrases"] > 0, (d7.get("totalWords"), d7.get("totalPhrases")))
c.post("/api/settings", json={"ai": {"provider": "original"}})
r = c.post("/api/items/pick", json={"doc_id": d3["id"], "picks": [{"sentence": S4, "text": "compromise"}]}).json()
check("不用 AI 时划选的词用词典释义", store.get_item("compromise") and "妥协" in store.get_item("compromise")["translation"], store.get_item("compromise"))
r = c.post("/api/session", json={"keys": ["persistent problem"]}).json()
check("自选词组能练", r["cards"] and r["cards"][0]["answer"] == ["persistent problem"], r)

print("朗读")
from chuanting import tts as _tts  # noqa: E402
check("纯标点不送去合成", not _tts.speakable(".") and not _tts.speakable(" , ") and _tts.speakable("look into") and _tts.speakable("起飞"))
r = c.get("/api/tts", params={"text": "."})
check("朗读接口拒绝纯标点（不再报 500）", r.status_code == 400 and "朗读" in r.json()["error"], r.text[:100])
segs = ai.make_cloze("They promised to look into the allegations.", ["allegations"])
check("挖空在句末时剩下的“.”会被跳过", [x for x in segs if "t" in x and not _tts.speakable(x["t"])] == [{"t": "."}])

print("关闭川听")
check("没带请求头不能关", c.post("/api/quit").status_code == 403)

print("朗读方式")
check("默认是挖空", store.load_config()["study"]["read_mode"] == "cloze")
r = c.post("/api/settings", json={"study": {"read_mode": "full"}})
check("设置里能改成读全", r.json()["study"]["read_mode"] == "full")
import asyncio as _aio  # noqa: E402
spoken = []


async def _fake_synth(text, voice, rate="+0%"):
    spoken.append(text)
    return Path("/dev/null")


_orig_synth, _orig_pcm = _tts.synth, _tts.to_pcm
_tts.synth, _tts.to_pcm = _fake_synth, (lambda p: b"")
card = {"segments": ai.make_cloze("We got rid of the old sofa.", ["got rid of"]), "answer": ["got rid of"],
        "sentence": "We got rid of the old sofa.", "meaning": "", "spoken_key": "get rid of"}
_aio.run(_tts.card_audio(card, store.load_config(), 100))
check("读全：MP3 开头读完整句，不读挖空片段", spoken[0] == "We got rid of the old sofa." and "We " not in spoken, spoken)
spoken.clear()
c.post("/api/settings", json={"study": {"read_mode": "cloze"}})
_aio.run(_tts.card_audio(card, store.load_config(), 100))
check("挖空：MP3 开头读挖空片段", spoken[0] == "We " and spoken[1] == " the old sofa.", spoken)
_tts.synth, _tts.to_pcm = _orig_synth, _orig_pcm

print("整句和生词本分组")
S2 = "She turned the offer down because she could not put up with the long commute."
c.post("/api/settings", json={"ai": {"provider": "deepseek"}})
n_calls = len(calls)
r = c.post("/api/items/pick", json={"doc_id": d3["id"], "picks": [{"sentence": S2, "whole": True, "meaning": "她拒绝了这个提议。"}]}).json()
it = store.get_item(S2)
check("整句加入生词本", r["keys"] == [S2] and it and it["kind"] == "sentence" and it["translation"] == "她拒绝了这个提议。", (r, it))
r = c.post("/api/session", json={"keys": [S2]}).json()
card = r["cards"][0] if r["cards"] else {}
check("整句的卡片：整句就是答案，不调用 AI", card.get("kind") == "sentence" and card.get("answer") == [S2]
      and card.get("meaning") == "她拒绝了这个提议。" and len(calls) == n_calls, card)
sents = c.get(f"/api/docs/{d3['id']}/sentences").json()
check("逐句解析标出已加入的整句", next(x for x in sents if x["text"] == S2)["inList"] is True)
items = {i["key"]: i for i in c.get("/api/items").json()}
check("生词本带上来源文档", items["long commute"]["docTitle"] == "ai" and items[S2]["docId"] == d3["id"], items["long commute"])
r = c.post("/api/review", json={"key": S2, "ctxId": card["ctxId"], "mistakes": 0}).json()
check("整句也按 FSRS 安排复习", r["state"]["due"] > srs.today())

if "--tts" in sys.argv:
    print("语音（联网）")
    r = c.get("/api/tts", params={"text": "Hello there."})
    check("合成语音", r.status_code == 200 and len(r.content) > 1000)
    c.post("/api/settings", json={"ai": {"provider": "original"}})
    r = c.post("/api/export/mp3", json={"mode": "all", "limit": 2})
    check("导出 MP3", r.status_code == 200 and (r.content[:3] == b"ID3" or r.content[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")), r.content[:20])
    out = Path(os.environ["CHUANTING_HOME"]) / "test.mp3"
    out.write_bytes(r.content)
    print(f"    MP3：{out}（{len(r.content) // 1024} KB）")

print(f"\n{passed} 项通过，{failed} 项失败")
sys.exit(1 if failed else 0)
