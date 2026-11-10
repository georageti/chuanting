"""从英文文本里认出值得学的单词和词组，并记下它们出现在哪一句。

流程：分句 → 分词 → 词组匹配（允许变形、拆开写、sb/sth 占位）→ 单词识别（去停用词、专有名词）
是否“简单”不在这里判断：这里返回全部候选，带上考试标签和词频，由 filter_candidates 按设置筛。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import lexicon as lx

STOPWORDS = set("""
a about above after again against all also am an and any are aren't as at be because been before being below
between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each few
for from further had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself him
himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't my
myself no nor not of off on once only or other ought our ours ourselves out over own same shan't she she'd
she'll she's should shouldn't so some such than that that's the their theirs them themselves then there there's
these they they'd they'll they're they've this those through to too under until up very was wasn't we we'd
we'll we're we've were weren't what what's when when's where where's which while who who's whom why why's will
with won't would wouldn't you you'd you'll you're you've your yours yourself yourselves
mr mrs ms dr st yes oh ok okay
""".split())

PARTICLES = {"up", "down", "off", "out", "in", "on", "away", "back", "over", "through", "around",
             "along", "about", "apart", "aside", "forward", "together", "across", "by"}
POSSESSIVES = {"my", "your", "his", "her", "its", "our", "their", "one's"}
REFLEXIVES = {"myself", "yourself", "himself", "herself", "itself", "ourselves", "yourselves", "themselves", "oneself"}
OBJ_PLACEHOLDERS = {"sb", "sth", "someone", "something", "somebody"}
POSS_PLACEHOLDERS = {"sb's", "sth's", "one's", "someone's", "somebody's"}
DETERMINERS = {"the", "a", "an", "this", "that", "these", "those", "my", "your", "his", "her", "its", "our",
               "their", "some", "any", "every", "each", "no", "another", "such", "what", "which", "whose"}
OBJ_PRONOUNS = {"it", "him", "her", "them", "me", "us", "you", "this", "that", "these", "those", "everything",
                "something", "anything", "nothing", "everyone", "someone", "anyone", "everybody", "somebody",
                "anybody", "nobody", "one", "people", "things"}
ADVERBS = {"already", "just", "still", "also", "never", "ever", "even", "always", "often", "really", "quite",
           "almost", "soon", "now", "then", "again", "too", "very", "rather", "yet", "not"}
# 动词 + 这些小品词，往往是意思变了的短语动词（give up / turn down），不算“基础搭配”
IDIOM_PARTICLES = {"up", "down", "off", "out", "away", "back", "over", "apart", "aside", "through", "around", "along"}
ABBREV = {"mr", "mrs", "ms", "dr", "prof", "st", "vs", "etc", "e.g", "i.e", "inc", "ltd", "co", "jr", "sr", "no", "u.s", "u.k"}

TOKEN_RE = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*|[0-9]+(?:[.,][0-9]+)*|\S")


@dataclass
class Token:
    text: str
    start: int
    end: int

    @property
    def is_word(self) -> bool:
        return self.text[0].isalpha()

    @property
    def low(self) -> str:
        return self.text.lower().replace("’", "'")


@dataclass
class Candidate:
    key: str
    kind: str  # word | phrase
    translation: str = ""
    phonetic: str = ""
    tags: list = field(default_factory=list)
    level: str | None = None
    rank: int = 0
    basic: bool = False  # 词组：两个常见词组成的普通搭配（need to / live in）
    count: int = 0
    examples: list = field(default_factory=list)  # [{sentence, surface, pieces?}]
    source: str = "dict"  # 词组从哪来：dict 词典匹配 / ai
    ptype: str = ""       # AI 给的词组类型：phrasal verb / collocation / idiom / expression
    ai_pick: bool = False  # 单词：AI 分析时认为本文里值得学（难词，或常见词的少见用法）
    ctx: str = ""          # 单词：AI 给的本文里的意思

    def to_dict(self) -> dict:
        return {"key": self.key, "kind": self.kind, "translation": self.translation, "phonetic": self.phonetic,
                "tags": self.tags, "level": self.level, "levelName": lx.LEVEL_NAMES.get(self.level or "", ""),
                "rank": self.rank, "basic": self.basic, "count": self.count, "examples": self.examples,
                "source": self.source, "ptype": self.ptype, "aiPick": self.ai_pick, "ctx": self.ctx}


# ---------- 分句 ----------

WORD_LIST_ENTRY = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*(?:[ \t]+[A-Za-z]+(?:['’-][A-Za-z]+)*){0,5}")


def split_sentences(text: str) -> list[str]:
    out = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for para in re.split(r"\n\s*\n|\n(?=\s*[-*•\d]+[.)]?\s)", text):
        lines = [line.strip() for line in para.splitlines() if line.strip()]
        # 单词行为主的词表逐行保留，避免相邻行误配成词组；文章的普通软换行仍合并。
        if (len(lines) > 1 and all(WORD_LIST_ENTRY.fullmatch(line) for line in lines)
                and sum(len(line.split()) == 1 for line in lines) * 2 >= len(lines)):
            out.extend(re.sub(r"\s+", " ", line) for line in lines)
            continue
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        start = 0
        for m in re.finditer(r"[.!?]+[\"')\]”’]*\s+(?=[\"'(“‘]?[A-Z0-9])", para):
            prev = re.search(r"([A-Za-z.]+)[.!?]+[\"')\]”’]*\s+$", para[start : m.end()])
            if prev and prev.group(1).lower().rstrip(".") in ABBREV:
                continue
            out.append(para[start : m.end()].strip())
            start = m.end()
        if para[start:].strip():
            out.append(para[start:].strip())
    return out


def tokenize(sentence: str) -> list[Token]:
    return [Token(m.group(0), m.start(), m.end()) for m in TOKEN_RE.finditer(sentence)]


# ---------- 词组匹配 ----------

def _tok_matches(p: str, t: Token) -> bool:
    if not t.is_word:
        return False
    low = t.low
    return p == low or p == lx.lemma(low)


def _match_from(pt: list[str], toks: list[Token], i: int, k: int = 0) -> int | None:
    """词组的第 k 个词起，从句子第 i 个词开始匹配；成功返回匹配结束的位置（不含）"""
    if k == len(pt):
        return i
    if i >= len(toks):
        return None
    p = pt[k]
    if p in OBJ_PLACEHOLDERS:
        for span in (1, 2, 3):  # sb / sth 占 1~3 个词
            seg = toks[i : i + span]
            if len(seg) < span or not all(s.is_word for s in seg):
                break
            if not _looks_like_object(seg):
                continue
            end = _match_from(pt, toks, i + span, k + 1)
            if end is not None:
                return end
        return None
    if p in POSS_PLACEHOLDERS:
        t = toks[i]
        if t.low in POSSESSIVES or t.low.endswith("'s"):
            return _match_from(pt, toks, i + 1, k + 1)
        # the company's → 两个词
        if i + 1 < len(toks) and toks[i + 1].low.endswith("'s") and toks[i].is_word:
            return _match_from(pt, toks, i + 2, k + 1)
        return None
    if p == "oneself":
        return _match_from(pt, toks, i + 1, k + 1) if toks[i].low in REFLEXIVES else None
    if _tok_matches(p, toks[i]):
        return _match_from(pt, toks, i + 1, k + 1)
    return None


def _looks_like_object(seg: list[Token]) -> bool:
    """sb / sth 占位能不能是这几个词：代词、名词，或“限定词 + 名词”；副词、动词变形、介词都不行"""
    lows = [t.low for t in seg]
    if lows[-1] in DETERMINERS or lows[-1] in PARTICLES or lows[-1] in {"and", "but", "or", "to"}:
        return False
    if any(w in ADVERBS or w.endswith("ly") for w in lows):
        return False
    if len(seg) == 1:
        w = lows[0]
        if w in OBJ_PRONOUNS:
            return True
        if w in STOPWORDS:
            return False
        base = lx.lemma(w)
        # given / taking 这种动词变形不当宾语；名词复数（plans → plan）可以
        return base == w or (w.endswith("s") and w.rstrip("s").startswith(base[:-1] if len(base) > 3 else base))
    return lows[0] in DETERMINERS or lows[-1].endswith("'s") is False and lows[0] in OBJ_PRONOUNS


def _match_separable(pt: list[str], toks: list[Token], i: int) -> int | None:
    """turn it down / take your coat off：动词和小品词之间夹 1~3 个词"""
    if len(pt) != 2 or pt[1] not in PARTICLES:
        return None
    for gap in (1, 2, 3):
        j = i + 1 + gap
        if j >= len(toks):
            return None
        mid = toks[i + 1 : j]
        if not all(m.is_word and m.low not in PARTICLES and m.low not in {"and", "but", "or", "to", "that", "which"} for m in mid):
            return None
        if _tok_matches(pt[1], toks[j]):
            return j + 1
    return None


def find_phrases(toks: list[Token]) -> list[tuple[int, int, str]]:
    """返回 [(起, 止, 词组)]，长的优先，被更长词组完全覆盖的不要"""
    found = []
    for i, t in enumerate(toks):
        if not t.is_word:
            continue
        heads = {t.low, lx.lemma(t.low)}
        best = None
        for head in heads:
            for p, pt, _tr in lx.phrases_starting_with(head):
                end = _match_from(pt, toks, i, 0)
                if end is None:
                    end = _match_separable(pt, toks, i)
                if end is None:
                    continue
                score = (len(pt), -(end - i))
                if best is None or score > best[0]:
                    best = (score, i, end, p)
        if best:
            found.append(best)
    found.sort(key=lambda b: (-b[0][0], b[1]))
    chosen: list[tuple[int, int, str]] = []
    for _score, s, e, p in found:
        if any(cs <= s and e <= ce for cs, ce, _ in chosen):
            continue
        chosen.append((s, e, p))
    return sorted(chosen)


def _phrase_basic(p: str) -> tuple[bool, int]:
    """返回（是不是普通搭配，组成词里最难那个的词频排名）。
    两个词、第二个词不是 up/down/off 这类会改变意思的小品词时，算“普通搭配”的候选，
    再由 filter_candidates 按词频门槛决定藏不藏。"""
    toks = [t for t in p.split() if t not in OBJ_PLACEHOLDERS | POSS_PLACEHOLDERS | {"oneself"}]
    content = [t for t in toks if t not in STOPWORDS]
    ranks = []
    for t in content:
        info = lx.word(lx.lemma(t))
        ranks.append(info["rank"] if info and info["rank"] else 10**6)
    hardest = max(ranks, default=0)
    words = p.split()
    preps = {"to", "in", "on", "at", "for", "of", "with", "from", "into", "about", "by", "as"}
    # need to / live in / refuse to：常见动词 + 介词；he is：两个虚词
    plain = len(words) == 2 and (all(w in STOPWORDS for w in words)
                                 or (words[0] not in STOPWORDS and words[1] in preps))
    return plain, hardest


# ---------- 主流程 ----------

def _surface(sentence: str, toks: list[Token], s: int, e: int) -> str:
    return sentence[toks[s].start : toks[e - 1].end]


def usable_sentences(text: str) -> list[str]:
    """长段按单词边界分段，不丢弃原文；AI 识别和 extract 使用同一份编号。"""
    out = []
    for sentence in split_sentences(text):
        while len(sentence) > 1200:
            cut = sentence.rfind(" ", 0, 1201)
            if cut <= 0:
                cut = sentence.find(" ", 1200)
                if cut < 0:
                    break  # 单个超长词保持原样，不切出并不存在的词。
            out.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        out.append(sentence)
    return out


def extract(text: str, max_examples: int = 3, analysis: dict | None = None) -> list[Candidate]:
    """单词用词典识别；词组在 AI 分析过的句子里用 AI 的结果，其余句子用词典匹配。
    落在词组里的单词，在那一处不再单独算作单词（词组已经解释了它）。
    analysis：ai.analyze 的结果，没有就全部用词典。"""
    cands: dict[str, Candidate] = {}
    analysis = analysis or {}
    covered: set[int] = set(analysis.get("covered") or ())
    ai_phrases: dict[int, list[dict]] = {}
    for f in analysis.get("phrases", []):
        ai_phrases.setdefault(f["sid"], []).append(f)
    ai_words: dict[tuple[int, int], dict] = {(w["sid"], w["start"]): w for w in analysis.get("words", [])}

    def add(key: str, kind: str, sentence: str, surface: str, **extra) -> Candidate | None:
        c = cands.get(key)
        if c is None:
            if kind == "phrase":
                if extra.get("source") == "ai":
                    c = Candidate(key, kind, translation=extra.get("meaning") or "", source="ai", ptype=extra.get("ptype", ""))
                    if not c.translation and lx.phrase(key):
                        c.translation = lx.short_tr(lx.phrase(key)["translation"])
                else:
                    info = lx.phrase(key)
                    if not info:
                        return None
                    c = Candidate(key, kind, translation=lx.short_tr(info["translation"]))
                    c.basic, c.rank = _phrase_basic(key)
            else:
                info = lx.word(key)
                if info:
                    c = Candidate(key, kind, translation=lx.short_tr(info["translation"]), phonetic=info["phonetic"],
                                  tags=info["tags"], level=info["level"], rank=info["rank"])
                elif extra.get("meaning"):  # 词典里没有，但 AI 认为是难词
                    c = Candidate(key, kind, translation=extra["meaning"], source="ai")
                else:
                    return None
            cands[key] = c
        elif kind == "phrase" and extra.get("source") == "ai" and c.source != "ai":
            # 同一个词组，后半部分词典匹配、前半部分 AI：用 AI 的释义和类型
            c.source, c.ptype, c.basic = "ai", extra.get("ptype", ""), False
            c.translation = extra.get("meaning") or c.translation
        if extra.get("meaning") and kind == "word":
            c.ai_pick = True
            c.ctx = c.ctx or extra["meaning"]
        c.count += 1
        if len(c.examples) < max_examples and all(ex["sentence"] != sentence for ex in c.examples):
            ex = {"sentence": sentence, "surface": surface}
            if extra.get("pieces"):
                ex["pieces"] = extra["pieces"]
            if extra.get("meaning"):
                ex["meaning"] = extra["meaning"]
            c.examples.append(ex)
        return c

    for sid, sentence in enumerate(usable_sentences(text)):
        toks = tokenize(sentence)
        taken: list[tuple[int, int]] = []  # 被词组占掉的字符范围
        if sid in covered:
            for f in ai_phrases.get(sid, []):
                add(f["phrase"], "phrase", sentence, f["surface"], source="ai", pieces=f["pieces"],
                    meaning=f["meaning"], ptype=f["kind"])
                taken += [tuple(x) for x in f["spans"]]
        else:
            for s, e, p in find_phrases(toks):
                c = add(p, "phrase", sentence, _surface(sentence, toks, s, e))
                if c is not None and not c.basic:
                    # 只占词组本身的词；sb / sth 位置上的宾语照常算单词
                    literal = {w for w in p.split() if w not in OBJ_PLACEHOLDERS | POSS_PLACEHOLDERS | {"oneself"}}
                    taken += [(t.start, t.end) for t in toks[s:e] if t.low in literal or lx.lemma(t.low) in literal]
        first_word = next((i for i, t in enumerate(toks) if t.is_word), -1)
        for i, t in enumerate(toks):
            if not t.is_word:
                continue
            if any(a <= t.start and t.end <= b for a, b in taken):
                continue  # 词组里的词
            aw = ai_words.get((sid, t.start))
            if aw and not aw["meaning"]:
                aw = None
            low = t.low
            if t.text[0].isupper() and i != first_word:
                continue  # 句中大写，多半是人名地名
            if t.text.isupper() and len(t.text) > 1:
                continue  # 缩写
            if not aw and (low in STOPWORDS or len(low) <= 2):
                continue
            base = lx.lemma(low)
            if aw and not lx.word(base):
                base = aw["lemma"]  # 词典里没有：用 AI 给的原形（microplastics → microplastic）
            if base in STOPWORDS and not aw:
                continue
            add(base, "word", sentence, t.text, meaning=aw["meaning"] if aw else "")
    return list(cands.values())


def filter_candidates(cands: list[dict], hide_level: str | None = "gk", hide_rank: int = 2000,
                      known: set[str] | None = None, include_phrases: bool = True) -> list[dict]:
    """hide_level：这一级及更简单的考试词不显示；hide_rank：词频排名在前 N 的不显示"""
    known = known or set()
    cut = lx.LEVELS.index(hide_level) if hide_level in lx.LEVELS else -1
    out = []
    for c in cands:
        if c["key"] in known:
            continue
        if c["kind"] == "phrase":
            if not include_phrases:
                continue
            if c.get("basic") and c.get("rank", 0) <= max(hide_rank, 1500):
                continue
            out.append(c)
            continue
        if c.get("aiPick"):
            out.append(c)
            continue
        if c["level"] in lx.LEVELS and lx.LEVELS.index(c["level"]) <= cut:
            continue
        if 0 < c["rank"] <= hide_rank:
            continue
        out.append(c)
    return out
