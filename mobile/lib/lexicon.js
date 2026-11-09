// 词典查询：单词释义、词形还原、按开头词找词组。对应 Python 版 chuanting/lexicon.py。
// 数据来自 scripts/build_mobile_dict.py 生成的精简词典（gzip JSON），由 init() 装入。
import S from './shared.js';

export const LEVELS = S.LEVELS;
export const LEVEL_NAMES = S.LEVEL_NAMES;

let WORDS = {};    // 词 → [音标, 释义, 标签, 词频排名]
let FORMS = {};    // 变形 → 原形
let HEADS = new Map(); // 开头词原形 → [[词组, 词组拆成的词, 释义], ...]
let PHRASES = new Map(); // 词组 → 释义
const lemmaCache = new Map();

export function init({ words, forms, phrases }) {
  WORDS = words;
  FORMS = forms;
  HEADS = new Map();
  PHRASES = new Map();
  for (const [p, head, tr] of phrases) {
    PHRASES.set(p, tr);
    if (!HEADS.has(head)) HEADS.set(head, []);
    HEADS.get(head).push([p, p.split(' '), tr]);
  }
  lemmaCache.clear();
}

export function levelOf(tags) {
  const idx = tags.map((t) => LEVELS.indexOf(t)).filter((i) => i >= 0);
  return idx.length ? LEVELS[Math.min(...idx)] : null;
}

export function word(w) {
  const row = Object.prototype.hasOwnProperty.call(WORDS, w) ? WORDS[w] : null;
  if (!row) return null;
  const tags = row[2] ? row[2].split(' ') : [];
  return { w, phonetic: row[0], translation: row[1], tags, rank: row[3] || 0, level: levelOf(tags) };
}

const form = (f) => (Object.prototype.hasOwnProperty.call(FORMS, f) ? FORMS[f] : null);

const SUFFIXES = [['ies', 'y'], ['ied', 'y'], ['ves', 'f'], ['es', ''], ['s', ''],
  ['ed', ''], ['ed', 'e'], ['d', ''], ['ing', ''], ['ing', 'e'], ['ly', '']];

/** 还原成原形；词典里查不到的，用常见词尾规则再试一次；都不行就原样返回 */
export function lemma(t0) {
  if (lemmaCache.has(t0)) return lemmaCache.get(t0);
  let t = t0.toLowerCase().replace(/’/g, "'");
  if (t.endsWith("'s")) t = t.slice(0, -2);
  let out = form(t);
  if (!out && word(t)) out = t;
  if (!out) {
    for (const [suffix, repl] of SUFFIXES) {
      if (t.endsWith(suffix) && t.length - suffix.length >= 3) {
        const cand = t.slice(0, t.length - suffix.length) + repl;
        // stopped → stopp → stop
        if ((suffix === 'ed' || suffix === 'ing') && !repl && cand.length > 3 && cand.at(-1) === cand.at(-2)) {
          if (word(cand.slice(0, -1))) { out = form(cand.slice(0, -1)) || cand.slice(0, -1); break; }
        }
        if (word(cand)) { out = form(cand) || cand; break; }
      }
    }
  }
  out = out || t;
  lemmaCache.set(t0, out);
  return out;
}

export function phrasesStartingWith(head) {
  return HEADS.get(head) || [];
}

export function phrase(p) {
  return PHRASES.has(p) ? { w: p, translation: PHRASES.get(p) } : null;
}

export function shortTr(tr, lines = 2, chars = 80) {
  const out = tr.split('\n').slice(0, lines).join('；');
  return out.length <= chars ? out : out.slice(0, chars - 1) + '…';
}
