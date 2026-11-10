// 从英文文本里认出值得学的单词和词组，并记下它们出现在哪一句。对应 Python 版 chuanting/extract.py，规则逐条照搬。
import S from './shared.js';
import * as lx from './lexicon.js';

const set = (a) => new Set(a);
export const STOPWORDS = set(S.STOPWORDS);
export const PARTICLES = set(S.PARTICLES);
const POSSESSIVES = set(S.POSSESSIVES);
const REFLEXIVES = set(S.REFLEXIVES);
export const OBJ_PLACEHOLDERS = set(S.OBJ_PLACEHOLDERS);
export const POSS_PLACEHOLDERS = set(S.POSS_PLACEHOLDERS);
const DETERMINERS = set(S.DETERMINERS);
const OBJ_PRONOUNS = set(S.OBJ_PRONOUNS);
const ADVERBS = set(S.ADVERBS);
const ABBREV = set(S.ABBREV);
const SLOT_ALL = new Set([...S.OBJ_PLACEHOLDERS, ...S.POSS_PLACEHOLDERS, 'oneself']);

const TOKEN_RE = /[A-Za-z]+(?:['’-][A-Za-z]+)*|[0-9]+(?:[.,][0-9]+)*|\S/g;
const isAlpha = (ch) => /\p{L}/u.test(ch);
const isUpper = (s) => s === s.toUpperCase() && s !== s.toLowerCase();

export class Token {
  constructor(text, start, end) { this.text = text; this.start = start; this.end = end; }
  get isWord() { return isAlpha(this.text[0]); }
  get low() { return this.text.toLowerCase().replace(/’/g, "'"); }
}

// ---------- 分句 ----------

const WORD_LIST_ENTRY = /^[A-Za-z]+(?:['’-][A-Za-z]+)*(?:[ \t]+[A-Za-z]+(?:['’-][A-Za-z]+)*){0,5}$/;

export function splitSentences(text) {
  const out = [];
  text = text.replace(/\r\n?/g, '\n');
  for (let para of text.split(/\n\s*\n|\n(?=\s*[-*•\d]+[.)]?\s)/)) {
    const lines = para.split('\n').map((line) => line.trim()).filter(Boolean);
    // 单词行为主的词表逐行保留；文章的普通软换行仍按原规则合并。
    if (lines.length > 1 && lines.every((line) => WORD_LIST_ENTRY.test(line))
        && lines.filter((line) => line.split(/\s+/).length === 1).length * 2 >= lines.length) {
      for (const line of lines) out.push(line.replace(/\s+/g, ' '));
      continue;
    }
    para = para.replace(/\s+/g, ' ').trim();
    if (!para) continue;
    let start = 0;
    const re = /[.!?]+["')\]”’]*\s+(?=["'(“‘]?[A-Z0-9])/g;
    let m;
    while ((m = re.exec(para))) {
      const end = m.index + m[0].length;
      const prev = /([A-Za-z.]+)[.!?]+["')\]”’]*\s+$/.exec(para.slice(start, end));
      if (prev && ABBREV.has(prev[1].toLowerCase().replace(/\.+$/, ''))) continue;
      out.push(para.slice(start, end).trim());
      start = end;
    }
    if (para.slice(start).trim()) out.push(para.slice(start).trim());
  }
  return out;
}

export function tokenize(sentence) {
  return [...sentence.matchAll(TOKEN_RE)].map((m) => new Token(m[0], m.index, m.index + m[0].length));
}

/** 长段按单词边界分段，不丢弃原文；AI 分析和 extract 用同一份编号。 */
export function usableSentences(text) {
  const out = [];
  for (let sentence of splitSentences(text)) {
    while (sentence.length > 1200) {
      let cut = sentence.lastIndexOf(' ', 1200);
      if (cut <= 0) {
        cut = sentence.indexOf(' ', 1200);
        if (cut < 0) break;
      }
      out.push(sentence.slice(0, cut));
      sentence = sentence.slice(cut).trimStart();
    }
    out.push(sentence);
  }
  return out;
}

// ---------- 词组匹配 ----------

function tokMatches(p, t) {
  if (!t.isWord) return false;
  const low = t.low;
  return p === low || p === lx.lemma(low);
}

function looksLikeObject(seg) {
  const lows = seg.map((t) => t.low);
  const last = lows.at(-1);
  if (DETERMINERS.has(last) || PARTICLES.has(last) || ['and', 'but', 'or', 'to'].includes(last)) return false;
  if (lows.some((w) => ADVERBS.has(w) || w.endsWith('ly'))) return false;
  if (seg.length === 1) {
    const w = lows[0];
    if (OBJ_PRONOUNS.has(w)) return true;
    if (STOPWORDS.has(w)) return false;
    const base = lx.lemma(w);
    // given / taking 这种动词变形不当宾语；名词复数（plans → plan）可以
    return base === w || (w.endsWith('s') && w.replace(/s+$/, '').startsWith(base.length > 3 ? base.slice(0, -1) : base));
  }
  return DETERMINERS.has(lows[0]) || (!last.endsWith("'s") && OBJ_PRONOUNS.has(lows[0]));
}

function matchFrom(pt, toks, i, k = 0) {
  if (k === pt.length) return i;
  if (i >= toks.length) return null;
  const p = pt[k];
  if (OBJ_PLACEHOLDERS.has(p)) {
    for (const span of [1, 2, 3]) { // sb / sth 占 1~3 个词
      const seg = toks.slice(i, i + span);
      if (seg.length < span || !seg.every((s) => s.isWord)) break;
      if (!looksLikeObject(seg)) continue;
      const end = matchFrom(pt, toks, i + span, k + 1);
      if (end !== null) return end;
    }
    return null;
  }
  if (POSS_PLACEHOLDERS.has(p)) {
    const t = toks[i];
    if (POSSESSIVES.has(t.low) || t.low.endsWith("'s")) return matchFrom(pt, toks, i + 1, k + 1);
    if (i + 1 < toks.length && toks[i + 1].low.endsWith("'s") && toks[i].isWord) return matchFrom(pt, toks, i + 2, k + 1);
    return null;
  }
  if (p === 'oneself') return REFLEXIVES.has(toks[i].low) ? matchFrom(pt, toks, i + 1, k + 1) : null;
  if (tokMatches(p, toks[i])) return matchFrom(pt, toks, i + 1, k + 1);
  return null;
}

/** turn it down / take your coat off：动词和小品词之间夹 1~3 个词 */
function matchSeparable(pt, toks, i) {
  if (pt.length !== 2 || !PARTICLES.has(pt[1])) return null;
  for (const gap of [1, 2, 3]) {
    const j = i + 1 + gap;
    if (j >= toks.length) return null;
    const mid = toks.slice(i + 1, j);
    if (!mid.every((m) => m.isWord && !PARTICLES.has(m.low) && !['and', 'but', 'or', 'to', 'that', 'which'].includes(m.low))) return null;
    if (tokMatches(pt[1], toks[j])) return j + 1;
  }
  return null;
}

/** 返回 [[起, 止, 词组]]，长的优先，被更长词组完全覆盖的不要 */
export function findPhrases(toks) {
  const found = [];
  toks.forEach((t, i) => {
    if (!t.isWord) return;
    const heads = new Set([t.low, lx.lemma(t.low)]);
    let best = null;
    for (const head of heads) {
      for (const [p, pt] of lx.phrasesStartingWith(head)) {
        let end = matchFrom(pt, toks, i, 0);
        if (end === null) end = matchSeparable(pt, toks, i);
        if (end === null) continue;
        const score = [pt.length, -(end - i)];
        if (!best || score[0] > best.score[0] || (score[0] === best.score[0] && score[1] > best.score[1])) {
          best = { score, s: i, e: end, p };
        }
      }
    }
    if (best) found.push(best);
  });
  found.sort((a, b) => b.score[0] - a.score[0] || a.s - b.s);
  const chosen = [];
  for (const { s, e, p } of found) {
    if (chosen.some(([cs, ce]) => cs <= s && e <= ce)) continue;
    chosen.push([s, e, p]);
  }
  return chosen.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
}

/** [是不是普通搭配, 组成词里最难那个的词频排名] */
function phraseBasic(p) {
  const toks = p.split(' ').filter((t) => !SLOT_ALL.has(t));
  const content = toks.filter((t) => !STOPWORDS.has(t));
  const ranks = content.map((t) => { const info = lx.word(lx.lemma(t)); return info && info.rank ? info.rank : 1e6; });
  const hardest = ranks.length ? Math.max(...ranks) : 0;
  const words = p.split(' ');
  const preps = new Set(['to', 'in', 'on', 'at', 'for', 'of', 'with', 'from', 'into', 'about', 'by', 'as']);
  const plain = words.length === 2 && (words.every((w) => STOPWORDS.has(w)) || (!STOPWORDS.has(words[0]) && preps.has(words[1])));
  return [plain, hardest];
}

// ---------- 主流程 ----------

function candidate(key, kind, extra = {}) {
  return { key, kind, translation: '', phonetic: '', tags: [], level: null, levelName: '', rank: 0, basic: false,
    count: 0, examples: [], source: 'dict', ptype: '', aiPick: false, ctx: '', ...extra };
}

/** 单词用词典识别；词组在 AI 分析过的句子里用 AI 的结果，其余句子用词典匹配。
 *  analysis：ai.analyze 的结果，没有就全部用词典。返回候选数组（和 Python 版 to_dict 的字段一致） */
export function extract(text, analysis = null, maxExamples = 3) {
  const cands = new Map();
  analysis = analysis || {};
  const covered = new Set(analysis.covered || []);
  const aiPhrases = new Map();
  for (const f of analysis.phrases || []) {
    if (!aiPhrases.has(f.sid)) aiPhrases.set(f.sid, []);
    aiPhrases.get(f.sid).push(f);
  }
  const aiWords = new Map((analysis.words || []).map((w) => [w.sid + ':' + w.start, w]));

  function add(key, kind, sentence, surface, extra = {}) {
    let c = cands.get(key);
    if (!c) {
      if (kind === 'phrase') {
        if (extra.source === 'ai') {
          c = candidate(key, kind, { translation: extra.meaning || '', source: 'ai', ptype: extra.ptype || '' });
          if (!c.translation && lx.phrase(key)) c.translation = lx.shortTr(lx.phrase(key).translation);
        } else {
          const info = lx.phrase(key);
          if (!info) return null;
          const [basic, rank] = phraseBasic(key);
          c = candidate(key, kind, { translation: lx.shortTr(info.translation), basic, rank });
        }
      } else {
        const info = lx.word(key);
        if (info) {
          c = candidate(key, kind, { translation: lx.shortTr(info.translation), phonetic: info.phonetic, tags: info.tags,
            level: info.level, levelName: lx.LEVEL_NAMES[info.level] || '', rank: info.rank });
        } else if (extra.meaning) { // 词典里没有，但 AI 认为是难词
          c = candidate(key, kind, { translation: extra.meaning, source: 'ai' });
        } else return null;
      }
      cands.set(key, c);
    } else if (kind === 'phrase' && extra.source === 'ai' && c.source !== 'ai') {
      Object.assign(c, { source: 'ai', ptype: extra.ptype || '', basic: false, translation: extra.meaning || c.translation });
    }
    if (extra.meaning && kind === 'word') { c.aiPick = true; c.ctx = c.ctx || extra.meaning; }
    c.count += 1;
    if (c.examples.length < maxExamples && c.examples.every((ex) => ex.sentence !== sentence)) {
      const ex = { sentence, surface };
      if (extra.pieces) ex.pieces = extra.pieces;
      if (extra.meaning) ex.meaning = extra.meaning;
      c.examples.push(ex);
    }
    return c;
  }

  usableSentences(text).forEach((sentence, sid) => {
    const toks = tokenize(sentence);
    const taken = [];
    if (covered.has(sid)) {
      for (const f of aiPhrases.get(sid) || []) {
        add(f.phrase, 'phrase', sentence, f.surface, { source: 'ai', pieces: f.pieces, meaning: f.meaning, ptype: f.kind });
        taken.push(...f.spans);
      }
    } else {
      for (const [s, e, p] of findPhrases(toks)) {
        const c = add(p, 'phrase', sentence, sentence.slice(toks[s].start, toks[e - 1].end));
        if (c && !c.basic) {
          // 只占词组本身的词；sb / sth 位置上的宾语照常算单词
          const literal = new Set(p.split(' ').filter((w) => !SLOT_ALL.has(w)));
          for (const t of toks.slice(s, e)) if (literal.has(t.low) || literal.has(lx.lemma(t.low))) taken.push([t.start, t.end]);
        }
      }
    }
    const firstWord = toks.findIndex((t) => t.isWord);
    toks.forEach((t, i) => {
      if (!t.isWord) return;
      if (taken.some(([a, b]) => a <= t.start && t.end <= b)) return; // 词组里的词
      let aw = aiWords.get(sid + ':' + t.start);
      if (aw && !aw.meaning) aw = null;
      const low = t.low;
      if (isUpper(t.text[0]) && i !== firstWord) return; // 句中大写，多半是人名地名
      if (isUpper(t.text) && t.text.length > 1) return; // 缩写
      if (!aw && (STOPWORDS.has(low) || low.length <= 2)) return;
      let base = lx.lemma(low);
      if (aw && !lx.word(base)) base = aw.lemma; // 词典里没有：用 AI 给的原形
      if (STOPWORDS.has(base) && !aw) return;
      add(base, 'word', sentence, t.text, { meaning: aw ? aw.meaning : '' });
    });
  });
  return [...cands.values()];
}

/** hideLevel：这一级及更简单的考试词不显示；hideRank：词频排名在前 N 的不显示 */
export function filterCandidates(cands, hideLevel = 'gk', hideRank = 2000, known = new Set(), includePhrases = true) {
  const cut = lx.LEVELS.indexOf(hideLevel);
  const out = [];
  for (const c of cands) {
    if (known.has(c.key)) continue;
    if (c.kind === 'phrase') {
      if (!includePhrases) continue;
      if (c.basic && (c.rank || 0) <= Math.max(hideRank, 1500)) continue;
      out.push(c);
      continue;
    }
    if (c.aiPick) { out.push(c); continue; }
    if (lx.LEVELS.includes(c.level) && lx.LEVELS.indexOf(c.level) <= cut) continue;
    if (c.rank > 0 && c.rank <= hideRank) continue;
    out.push(c);
  }
  return out;
}
