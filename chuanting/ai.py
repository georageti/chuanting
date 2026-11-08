"""给每个词或词组编新的语境句，并挖空。

支持三种来源：
  deepseek / openai：OpenAI 兼容的 /chat/completions 接口（DeepSeek 国内直连；也能接 Kimi、通义等）
  anthropic：Claude，用官方 anthropic SDK，JSON Schema 结构化输出
  original：不用 AI，直接把原文里那一句挖空（没有 Key 时的兜底）
"""
from __future__ import annotations

import json
import re

import httpx

from . import lexicon as lx
from .extract import OBJ_PLACEHOLDERS, POSS_PLACEHOLDERS, tokenize

BATCH = 8

SYSTEM = """You write example sentences for a Chinese learner who is reviewing English vocabulary by listening.
The learner will HEAR each sentence with the target blanked out (a beep), then type the missing word(s).

For every target item, write the requested number of NEW sentences:
- 8 to 22 words, natural modern English (everyday life, work, news, study). Vary the topics.
- Show the item's typical usage and collocations. If a source sentence is given, use the same sense.
- The context must make the meaning guessable from the rest of the sentence.
- Contain the target exactly once. Inflected forms are fine (took off, decisions, galvanised).
- Never copy or lightly edit the source sentence.
- Placeholders in a target (sb, sth, one's, oneself) stand for any suitable words.

Fields:
- sentence: the full sentence.
- answer: the exact text of the target as it appears in the sentence, as an array of pieces in order.
  Usually one piece, e.g. ["took off"]. If other words split the phrase, give each piece:
  "She turned the offer down." -> ["turned", "down"]. Never include the placeholder words.
- hint_zh: a natural Simplified Chinese translation of the whole sentence.
- note_zh: one short Simplified Chinese note on usage (collocation, register, or a common mistake), max 40 characters.

Reply with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "contexts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sentence": {"type": "string"},
                                "answer": {"type": "array", "items": {"type": "string"}},
                                "hint_zh": {"type": "string"},
                                "note_zh": {"type": "string"},
                            },
                            "required": ["sentence", "answer", "hint_zh", "note_zh"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["key", "contexts"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

EXAMPLE = {"items": [{"key": "take off", "contexts": [{
    "sentence": "Our flight finally took off after a two-hour delay caused by fog.",
    "answer": ["took off"], "hint_zh": "因为大雾延误了两个小时，我们的航班终于起飞了。",
    "note_zh": "take off 指飞机起飞；也可指衣服“脱下”"}]}]}


class AIError(RuntimeError):
    pass


# ---------- 挖空 ----------

def make_cloze(sentence: str, pieces: list[str]) -> list[dict] | None:
    """把句子按答案切开：[{t: '文字'}, {blank: '答案'}, ...]；答案找不到就返回 None"""
    segs, pos = [], 0
    pieces = [p.strip() for p in pieces if p and p.strip()]
    if not pieces:
        return None
    for p in pieces:
        m = re.compile(r"(?<![A-Za-z])" + re.escape(p).replace(r"\ ", r"\s+") + r"(?![A-Za-z])", re.I).search(sentence, pos)
        if not m:
            return None
        if m.start() > pos:
            segs.append({"t": sentence[pos : m.start()]})
        segs.append({"blank": m.group(0)})
        pos = m.end()
    if pos < len(sentence):
        segs.append({"t": sentence[pos:]})
    return segs


def original_pieces(key: str, kind: str, surface: str) -> list[str]:
    """原文兜底：单词就是原文那个词；词组只挖掉词组本身的词，夹在中间的宾语留着"""
    if kind == "word":
        return [surface]
    pattern = [p for p in key.split()]
    toks = tokenize(surface)
    marks = []
    k = 0
    for t in toks:
        while k < len(pattern) and (pattern[k] in OBJ_PLACEHOLDERS | POSS_PLACEHOLDERS | {"oneself"}):
            k += 1
        if k < len(pattern) and t.is_word and pattern[k] in (t.low, lx.lemma(t.low)):
            marks.append(True)
            k += 1
        else:
            marks.append(False)
    pieces, cur = [], None
    for t, m in zip(toks, marks):
        if m:
            cur = (cur[0], t.end) if cur else (t.start, t.end)
        elif cur:
            pieces.append(surface[cur[0] : cur[1]])
            cur = None
    if cur:
        pieces.append(surface[cur[0] : cur[1]])
    return pieces or [surface]


def from_original(item: dict, examples: list[dict]) -> list[dict]:
    out = []
    for ex in examples:
        pieces = ex.get("pieces") or original_pieces(item["key"], item["kind"], ex["surface"])
        if make_cloze(ex["sentence"], pieces):
            out.append({"sentence": ex["sentence"], "answer": pieces, "hint": "", "note": "原文例句"})
    return out


# ---------- 调 AI ----------

def _user_prompt(batch: list[dict], per_item: int) -> str:
    lines = [f"Write {per_item} sentences for each target below. Return JSON shaped like this example:",
             json.dumps(EXAMPLE, ensure_ascii=False), "", "Targets:"]
    for it in batch:
        src = it.get("source") or ""
        meaning = it.get("translation") or ""
        lines.append(json.dumps({"key": it["key"], "kind": it["kind"], "meaning_zh": meaning,
                                 "source_sentence": src}, ensure_ascii=False))
    return "\n".join(lines)


def _call_openai_compatible(base: str, key: str, model: str, prompt: str, system: str = SYSTEM,
                            temperature: float = 0.8, max_tokens: int = 6000) -> str:
    # 接口地址填到 /v1 为止（DeepSeek 可以不带 /v1）
    url = base.rstrip("/") + "/chat/completions"
    try:
        r = httpx.post(url, timeout=120, headers={"Authorization": f"Bearer {key}"}, json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
    except httpx.HTTPError as e:
        raise AIError(f"连不上 {base}：{e.__class__.__name__}") from e
    if r.status_code == 401:
        raise AIError("API Key 不对，或者已失效")
    if r.status_code == 402:
        raise AIError("账户余额不足")
    if r.status_code >= 400:
        raise AIError(f"AI 接口报错 {r.status_code}：{r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


def _call_anthropic(key: str, model: str, prompt: str, system: str = SYSTEM, schema: dict | None = None) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=key, timeout=180, max_retries=2)
    try:
        resp = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema or SCHEMA}},
            # 被安全分类器误拦时，由服务端自动换模型重试
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        raise AIError("Claude API Key 不对，或者已失效") from e
    except anthropic.PermissionDeniedError as e:
        raise AIError("这个 Key 没有权限调用该模型") from e
    except anthropic.NotFoundError as e:
        raise AIError(f"找不到模型 {model}") from e
    except anthropic.RateLimitError as e:
        raise AIError("请求太频繁或额度用完了，稍后再试") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"Claude 接口报错 {e.status_code}：{e.message[:200]}") from e
    except anthropic.APIConnectionError as e:
        raise AIError("连不上 Claude（国内需要开代理）") from e
    if resp.stop_reason == "refusal":
        raise AIError("Claude 拒绝了这次请求")
    return next((b.text for b in resp.content if b.type == "text"), "")


def unavailable_reason(cfg: dict) -> str | None:
    """AI 现在能不能用；不能用时返回原因"""
    ai = cfg["ai"]
    provider = ai["provider"]
    if provider == "original":
        return "设置里选的是“不用 AI”"
    if provider == "anthropic":
        return None if ai["anthropic"]["key"] else "还没有填 Claude 的 API Key"
    if provider in ("deepseek", "openai"):
        p = ai[provider]
        if not p["key"]:
            return f"还没有填 {'DeepSeek' if provider == 'deepseek' else '接口'} 的 API Key"
        if not p.get("base") or not p.get("model"):
            return "请在设置里填写接口地址和模型名"
        return None
    return f"未知的 AI 来源：{provider}"


def _call(cfg: dict, prompt: str, system: str = SYSTEM, schema: dict | None = None, temperature: float = 0.8,
          max_tokens: int = 6000) -> str:
    reason = unavailable_reason(cfg)
    if reason:
        raise AIError(reason)
    ai = cfg["ai"]
    if ai["provider"] == "anthropic":
        p = ai["anthropic"]
        return _call_anthropic(p["key"], p["model"] or "claude-opus-5-5", prompt, system, schema)
    p = ai[ai["provider"]]
    return _call_openai_compatible(p["base"], p["key"], p["model"], prompt, system, temperature, max_tokens)


def _parse(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError("AI 返回的不是合法 JSON") from e


def generate(items: list[dict], cfg: dict) -> dict[str, list[dict]]:
    """items: [{key, kind, translation, source}] → {key: [{sentence, answer, hint, note}]}"""
    ai = cfg["ai"]
    per_item = max(1, min(5, int(ai.get("per_item", 2))))
    result: dict[str, list[dict]] = {}
    for i in range(0, len(items), BATCH):
        batch = items[i : i + BATCH]
        prompt = _user_prompt(batch, per_item)
        data = _parse(_call(cfg, prompt))
        wanted = {it["key"].lower(): it["key"] for it in batch}
        for entry in data.get("items", []):
            key = wanted.get(str(entry.get("key", "")).lower())
            if not key:
                continue
            for c in entry.get("contexts", []):
                sentence = str(c.get("sentence", "")).strip()
                answer = c.get("answer") or []
                if isinstance(answer, str):
                    answer = [a.strip() for a in answer.split("...")]
                answer = [str(a) for a in answer if str(a).strip()]
                segs = make_cloze(sentence, answer)
                if not segs:
                    continue
                result.setdefault(key, []).append({
                    "sentence": sentence,
                    "answer": [s["blank"] for s in segs if "blank" in s],
                    "hint": str(c.get("hint_zh", "")).strip(),
                    "note": str(c.get("note_zh", "")).strip(),
                })
    return result


def test_connection(cfg: dict) -> str:
    out = generate([{"key": "take off", "kind": "phrase", "translation": "起飞", "source": ""}],
                   {**cfg, "ai": {**cfg["ai"], "per_item": 1}})
    if not out.get("take off"):
        raise AIError("AI 有回应，但没给出能用的句子")
    return out["take off"][0]["sentence"]


# ---------- AI 逐句分析文档 ----------
# 不是机械扫描：AI 读懂每一句，给出中文翻译，切出意群（词典里没有的短语、习语、搭配也算），
# 难词和多义词按这句里的意思给释义。结果逐条核对：句号要对、片段要按顺序出现在原句里。

ANALYZE_SYSTEM = """You help a Chinese learner (between CET-4 and IELTS level) understand an English text, sentence by sentence.

For every numbered sentence return:
- zh: a natural, faithful Simplified Chinese translation of the whole sentence.
- units: the parts a learner would get stuck on, in the order they appear:
  1. Every multi-word expression whose meaning is more than its words or that must be learned as a unit, whether or
     not dictionaries list it: phrasal verbs (turn sth down), verbs + prepositions with a special meaning (account for),
     collocations (take a toll on, heavy traffic), idioms and figurative phrases (on the fence, pull strings),
     fixed and linking expressions (in fact, by and large), compound nouns with a non-obvious meaning (red tape).
  2. Single words above CET-4 level, and common words used in an unusual sense here (bank = river side, address = deal with).
  Leave out: names of people, places, organisations and products; words a CET-4 learner knows in their usual sense;
  free combinations that mean exactly the sum of their words; grammar patterns (there is, need to).

Each unit:
- text: the exact text in the sentence as an array of pieces in order. When other words come between the parts of an
  expression, split it: "She turned the offer down." -> ["turned", "down"]. Copy the characters exactly; never
  include the words that fill an object slot.
- form: the dictionary form in lowercase. Single words: the base form (took -> take, analyses -> analysis).
  Expressions: write object slots as sb / sth (turn sth down, take sth into account).
- type: "word", "phrasal verb", "collocation", "idiom" or "expression".
- meaning_zh: the meaning in THIS sentence, short Simplified Chinese, at most 12 characters.

Never let a single-word unit overlap a multi-word unit: a word inside an expression is explained by the expression.
Reply with JSON only."""

ANALYZE_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sid": {"type": "integer"},
                    "zh": {"type": "string"},
                    "units": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "array", "items": {"type": "string"}},
                                "form": {"type": "string"},
                                "type": {"type": "string", "enum": ["word", "phrasal verb", "collocation", "idiom", "expression"]},
                                "meaning_zh": {"type": "string"},
                            },
                            "required": ["text", "form", "type", "meaning_zh"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["sid", "zh", "units"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sentences"],
    "additionalProperties": False,
}

ANALYZE_EXAMPLE = {"sentences": [{"sid": 3, "zh": "她拒绝了这份工作，因为她受不了漫长的通勤。", "units": [
    {"text": ["turned", "down"], "form": "turn sth down", "type": "phrasal verb", "meaning_zh": "拒绝"},
    {"text": ["put up with"], "form": "put up with", "type": "phrasal verb", "meaning_zh": "忍受"},
    {"text": ["commute"], "form": "commute", "type": "word", "meaning_zh": "通勤"}]}]}
ANALYZE_CHUNK_CHARS = 2000     # 每次发给 AI 的句子总长度（还要输出整段翻译，所以比只找词组时小）
ANALYZE_MAX_CHARS = 60000      # 文档太长时，只有前这么多字符交给 AI，其余用词典
PHRASE_TYPES = {"phrasal verb", "collocation", "idiom", "expression"}
PHRASE_RE = re.compile(r"[a-z]+(?:['’-][a-z]+)*(?: [a-z]+(?:['’-][a-z]+)*){1,5}")
WORD_RE = re.compile(r"[a-z]+(?:['’-][a-z]+)*")
SLOTS = {"sb", "sth", "sb's", "sth's", "one's", "oneself", "somebody", "something", "someone"}


def _chunks(sentences: list[str], limit: int) -> list[list[int]]:
    out, cur, size = [], [], 0
    for i, s in enumerate(sentences):
        if cur and size + len(s) > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(s)
    if cur:
        out.append(cur)
    return out


def _analyze_prompt(sentences: list[str], ids: list[int]) -> str:
    lines = ["Analyse these sentences. Return JSON shaped like this example:",
             json.dumps(ANALYZE_EXAMPLE, ensure_ascii=False), "", "Sentences:"]
    lines += [f"[{i + 1}] {sentences[i]}" for i in ids]
    return "\n".join(lines)


def normalize_phrase(p: str) -> str | None:
    p = re.sub(r"\s+", " ", str(p).strip().lower().replace("’", "'"))
    p = re.sub(r"\b(somebody|someone)\b", "sb", p)
    p = re.sub(r"\bsomething\b", "sth", p)
    if not PHRASE_RE.fullmatch(p):
        return None
    content = [w for w in p.split() if w not in SLOTS]
    return p if content and len(p.split()) >= 2 else None


def _spans(segs: list[dict]) -> list[tuple[int, int]]:
    pos, spans = 0, []
    for x in segs:
        text = x.get("blank", x.get("t", ""))
        if "blank" in x:
            spans.append((pos, pos + len(text)))
        pos += len(text)
    return spans


def analyze(sentences: list[str], cfg: dict) -> dict:
    """让 AI 逐句分析。返回：
      phrases  词组出现 [{phrase, sid, sentence, pieces, surface, spans, meaning, kind}]
      words    单词出现 [{lemma, sid, surface, start, end, meaning}]（本句里的意思）
      sents    [{sid, text, zh, units:[{pieces, form, type, meaning, spans}]}]，给“逐句解析”用
      covered  AI 处理过的句子编号
      warnings 提示
    任何一批出错都会抛出 AIError，由调用方退回词典。"""
    from concurrent.futures import ThreadPoolExecutor

    warnings: list[str] = []
    budget, ids = 0, []
    for i, s in enumerate(sentences):
        if budget + len(s) > ANALYZE_MAX_CHARS:
            warnings.append(f"文档较长，只分析了前 {ANALYZE_MAX_CHARS // 10000} 万字符，其余部分用词典识别")
            break
        budget += len(s)
        ids.append(i)
    chunks = [[ids[j] for j in c] for c in _chunks([sentences[i] for i in ids], ANALYZE_CHUNK_CHARS)]

    def run(chunk: list[int]) -> list[dict]:
        data = _parse(_call(cfg, _analyze_prompt(sentences, chunk), ANALYZE_SYSTEM, ANALYZE_SCHEMA,
                            temperature=0.2, max_tokens=8000))
        return data.get("sentences", []) if isinstance(data, dict) else []

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, chunks))

    phrases, words, sents = [], [], {}
    dropped = 0
    idset = set(ids)
    for entries in results:
        for e in entries:
            try:
                sid = int(e.get("sid")) - 1
            except (TypeError, ValueError):
                continue
            if sid not in idset:
                continue
            sentence = sentences[sid]
            rec = sents.setdefault(sid, {"sid": sid, "text": sentence, "zh": "", "units": []})
            rec["zh"] = str(e.get("zh", "")).strip() or rec["zh"]
            taken: list[tuple[int, int]] = []
            units = e.get("units") or []
            # 先收词组，再收单词：落在词组里的单词不要
            units = sorted(units, key=lambda u: 0 if str(u.get("type")) in PHRASE_TYPES else 1)
            for u in units:
                pieces = u.get("text") or []
                if isinstance(pieces, str):
                    pieces = [x.strip() for x in pieces.split("...")]
                pieces = [str(x).strip() for x in pieces if str(x).strip()]
                segs = make_cloze(sentence, pieces) if pieces else None
                if not segs:
                    dropped += 1
                    continue
                spans = _spans(segs)
                pieces = [x["blank"] for x in segs if "blank" in x]
                kind = str(u.get("type", "word"))
                meaning = str(u.get("meaning_zh", "")).strip()[:30]
                form_raw = str(u.get("form", "")).strip().lower()
                is_phrase = kind in PHRASE_TYPES or len(" ".join(pieces).split()) > 1
                if is_phrase:
                    form = normalize_phrase(form_raw) or normalize_phrase(" ".join(pieces))
                    if not form:
                        dropped += 1
                        continue
                    kind = kind if kind in PHRASE_TYPES else "expression"
                else:
                    if any(a <= s0 and e0 <= b for s0, e0 in spans for a, b in taken):
                        continue  # 落在词组里的单词
                    w = form_raw if WORD_RE.fullmatch(form_raw or "") else pieces[0].lower()
                    if not WORD_RE.fullmatch(w):
                        dropped += 1
                        continue
                    form, kind = w, "word"
                taken += spans
                rec["units"].append({"pieces": pieces, "form": form, "type": kind, "meaning": meaning, "spans": spans})
                if kind == "word":
                    words.append({"lemma": form, "sid": sid, "surface": pieces[0], "start": spans[0][0],
                                  "end": spans[0][1], "meaning": meaning})
                else:
                    phrases.append({"phrase": form, "sid": sid, "sentence": sentence, "pieces": pieces,
                                    "surface": sentence[spans[0][0] : spans[-1][1]], "spans": spans,
                                    "meaning": meaning, "kind": kind})
    missing = len(idset - set(sents))
    if missing:
        warnings.append(f"有 {missing} 句 AI 没有返回分析")
    if dropped:
        warnings.append(f"AI 给出的 {dropped} 处在原文里对不上，已丢弃")
    return {"phrases": phrases, "words": words, "sents": [sents[k] for k in sorted(sents)],
            "covered": set(sents), "warnings": warnings}


# ---------- 自选文字：查它在这句里的意思 ----------

GLOSS_SYSTEM = """A Chinese learner selected some words in an English sentence. Explain the selection as used in that sentence.
Return:
- form: the dictionary form in lowercase (single word: base form; expression: object slots as sb / sth).
  If the selection is part of a larger expression, still describe only what was selected.
- type: "word", "phrasal verb", "collocation", "idiom" or "expression".
- meaning_zh: the meaning in THIS sentence, short Simplified Chinese, at most 12 characters.
Reply with JSON only."""

GLOSS_SCHEMA = {
    "type": "object",
    "properties": {
        "form": {"type": "string"},
        "type": {"type": "string", "enum": ["word", "phrasal verb", "collocation", "idiom", "expression"]},
        "meaning_zh": {"type": "string"},
    },
    "required": ["form", "type", "meaning_zh"],
    "additionalProperties": False,
}


def gloss(selection: str, sentence: str, cfg: dict) -> dict:
    prompt = ("Return JSON like {\"form\": \"turn sth down\", \"type\": \"phrasal verb\", \"meaning_zh\": \"拒绝\"}.\n\n"
              f"Sentence: {sentence}\nSelection: {selection}")
    data = _parse(_call(cfg, prompt, GLOSS_SYSTEM, GLOSS_SCHEMA, temperature=0.2, max_tokens=300))
    return data if isinstance(data, dict) else {}
