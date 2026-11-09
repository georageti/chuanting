// AI：逐句分析、编挖空语境句、划选查词义。对应 Python 版 chuanting/ai.py，提示词和 JSON 结构从 shared.js 来（两边同一份）。
// 浏览器直接调接口（DeepSeek 和 Claude 都允许网页跨域调用），API Key 只存在这台手机上。
// Claude 用官方 JavaScript SDK（打包在 vendor/anthropic-sdk.js，用到时才加载）。
import S from './shared.js';
import * as lx from './lexicon.js';
import { tokenize, OBJ_PLACEHOLDERS, POSS_PLACEHOLDERS } from './extract.js';

export class AIError extends Error {}

const PHRASE_TYPES = new Set(S.PHRASE_TYPES);
const SLOTS = new Set(S.SLOTS);
const SLOT_ALL = new Set([...OBJ_PLACEHOLDERS, ...POSS_PLACEHOLDERS, 'oneself']);
const PHRASE_RE = /^[a-z]+(?:['’-][a-z]+)*(?: [a-z]+(?:['’-][a-z]+)*){1,5}$/;
const WORD_RE = /^[a-z]+(?:['’-][a-z]+)*$/;
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// ---------- 挖空 ----------

/** 把句子按答案切开：[{t}, {blank}, ...]；答案找不到就返回 null */
export function makeCloze(sentence, pieces) {
  pieces = (pieces || []).map((p) => String(p || '').trim()).filter(Boolean);
  if (!pieces.length) return null;
  const segs = [];
  let pos = 0;
  for (const p of pieces) {
    const re = new RegExp('(?<![A-Za-z])' + escapeRe(p).replace(/ /g, '\\s+') + '(?![A-Za-z])', 'ig');
    re.lastIndex = pos;
    const m = re.exec(sentence);
    if (!m) return null;
    if (m.index > pos) segs.push({ t: sentence.slice(pos, m.index) });
    segs.push({ blank: m[0] });
    pos = m.index + m[0].length;
  }
  if (pos < sentence.length) segs.push({ t: sentence.slice(pos) });
  return segs;
}

export function spans(segs) {
  let pos = 0;
  const out = [];
  for (const x of segs) {
    const text = x.blank ?? x.t ?? '';
    if ('blank' in x) out.push([pos, pos + text.length]);
    pos += text.length;
  }
  return out;
}

/** 原文兜底：单词就是原文那个词；词组只挖掉词组本身的词，夹在中间的宾语留着 */
export function originalPieces(key, kind, surface) {
  if (kind === 'word') return [surface];
  const pattern = key.split(' ');
  const toks = tokenize(surface);
  const marks = [];
  let k = 0;
  for (const t of toks) {
    while (k < pattern.length && SLOT_ALL.has(pattern[k])) k++;
    if (k < pattern.length && t.isWord && (pattern[k] === t.low || pattern[k] === lx.lemma(t.low))) { marks.push(true); k++; }
    else marks.push(false);
  }
  const pieces = [];
  let cur = null;
  toks.forEach((t, i) => {
    if (marks[i]) cur = cur ? [cur[0], t.end] : [t.start, t.end];
    else if (cur) { pieces.push(surface.slice(cur[0], cur[1])); cur = null; }
  });
  if (cur) pieces.push(surface.slice(cur[0], cur[1]));
  return pieces.length ? pieces : [surface];
}

export function fromOriginal(item, examples) {
  const out = [];
  for (const ex of examples) {
    const pieces = ex.pieces || originalPieces(item.key, item.kind, ex.surface);
    if (makeCloze(ex.sentence, pieces)) out.push({ sentence: ex.sentence, answer: pieces, hint: '', note: '原文例句' });
  }
  return out;
}

export function normalizePhrase(p) {
  p = String(p || '').trim().toLowerCase().replace(/’/g, "'").replace(/\s+/g, ' ');
  p = p.replace(/\b(somebody|someone)\b/g, 'sb').replace(/\bsomething\b/g, 'sth');
  if (!PHRASE_RE.test(p)) return null;
  const content = p.split(' ').filter((w) => !SLOTS.has(w));
  return content.length && p.split(' ').length >= 2 ? p : null;
}

// ---------- 调接口 ----------

export function unavailableReason(cfg) {
  const ai = cfg.ai;
  if (ai.provider === 'original') return '设置里选的是“不用 AI”';
  if (ai.provider === 'anthropic') return ai.anthropic.key ? null : '还没有填 Claude 的 API Key';
  if (ai.provider === 'deepseek' || ai.provider === 'openai') {
    const p = ai[ai.provider];
    if (!p.key) return `还没有填 ${ai.provider === 'deepseek' ? 'DeepSeek' : '接口'} 的 API Key`;
    if (!p.base || !p.model) return '请在设置里填写接口地址和模型名';
    return null;
  }
  return `未知的 AI 来源：${ai.provider}`;
}

async function callOpenAICompatible(p, prompt, system, temperature, maxTokens) {
  const url = p.base.replace(/\/+$/, '') + '/chat/completions';
  let r;
  try {
    r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${p.key}` },
      body: JSON.stringify({ model: p.model, messages: [{ role: 'system', content: system }, { role: 'user', content: prompt }],
        response_format: { type: 'json_object' }, temperature, max_tokens: maxTokens }),
      signal: AbortSignal.timeout(120000),
    });
  } catch (e) {
    throw new AIError(`连不上 ${p.base}：${e.name === 'TimeoutError' ? '等了两分钟没有回应' : '网络不通'}`);
  }
  if (r.status === 401) throw new AIError('API Key 不对，或者已失效');
  if (r.status === 402) throw new AIError('账户余额不足');
  if (r.status >= 400) throw new AIError(`AI 接口报错 ${r.status}：${(await r.text()).slice(0, 200)}`);
  const data = await r.json();
  return data.choices?.[0]?.message?.content || '';
}

let Anthropic = null;
async function callAnthropic(p, prompt, system, schema) {
  if (!Anthropic) Anthropic = (await import('../vendor/anthropic-sdk.js')).default;
  const client = new Anthropic({ apiKey: p.key, dangerouslyAllowBrowser: true, timeout: 180000, maxRetries: 2 });
  let resp;
  try {
    resp = await client.beta.messages.create({
      model: p.model || 'claude-opus-5-5',
      max_tokens: 16000,
      system,
      messages: [{ role: 'user', content: prompt }],
      output_config: { effort: 'low', format: { type: 'json_schema', schema: schema || S.SCHEMA } },
      // 被安全分类器误拦时，由服务端自动换模型重试
      betas: ['server-side-fallback-2026-07-01'],
      fallbacks: 'default',
    });
  } catch (e) {
    if (e instanceof Anthropic.AuthenticationError) throw new AIError('Claude API Key 不对，或者已失效');
    if (e instanceof Anthropic.PermissionDeniedError) throw new AIError('这个 Key 没有权限调用该模型');
    if (e instanceof Anthropic.NotFoundError) throw new AIError(`找不到模型 ${p.model}`);
    if (e instanceof Anthropic.RateLimitError) throw new AIError('请求太频繁或额度用完了，稍后再试');
    if (e instanceof Anthropic.APIConnectionError) throw new AIError('连不上 Claude（国内需要开代理）');
    if (e instanceof Anthropic.APIError) throw new AIError(`Claude 接口报错 ${e.status ?? ''}：${String(e.message).slice(0, 200)}`);
    throw e;
  }
  if (resp.stop_reason === 'refusal') throw new AIError('Claude 拒绝了这次请求');
  return resp.content.find((b) => b.type === 'text')?.text || '';
}

async function call(cfg, prompt, { system = S.SYSTEM, schema = null, temperature = 0.8, maxTokens = 6000 } = {}) {
  const reason = unavailableReason(cfg);
  if (reason) throw new AIError(reason);
  const ai = cfg.ai;
  if (ai.provider === 'anthropic') return callAnthropic(ai.anthropic, prompt, system, schema);
  return callOpenAICompatible(ai[ai.provider], prompt, system, temperature, maxTokens);
}

function parse(text) {
  text = String(text || '').trim().replace(/^```(?:json)?|```$/gm, '').trim();
  try { return JSON.parse(text); } catch { throw new AIError('AI 返回的不是合法 JSON'); }
}

async function pool(tasks, n) {
  const out = new Array(tasks.length);
  let i = 0;
  await Promise.all(Array.from({ length: Math.min(n, tasks.length) }, async () => {
    while (i < tasks.length) { const k = i++; out[k] = await tasks[k](); }
  }));
  return out;
}

// ---------- 编语境句 ----------

function userPrompt(batch, perItem) {
  const lines = [`Write ${perItem} sentences for each target below. Return JSON shaped like this example:`,
    JSON.stringify(S.EXAMPLE), '', 'Targets:'];
  for (const it of batch) {
    lines.push(JSON.stringify({ key: it.key, kind: it.kind, meaning_zh: it.translation || '', source_sentence: it.source || '' }));
  }
  return lines.join('\n');
}

/** items: [{key, kind, translation, source}] → {key: [{sentence, answer, hint, note}]} */
export async function generate(items, cfg) {
  const perItem = Math.max(1, Math.min(5, parseInt(cfg.ai.per_item ?? 2, 10)));
  const result = {};
  for (let i = 0; i < items.length; i += S.BATCH) {
    const batch = items.slice(i, i + S.BATCH);
    const data = parse(await call(cfg, userPrompt(batch, perItem)));
    const wanted = new Map(batch.map((it) => [it.key.toLowerCase(), it.key]));
    for (const entry of data.items || []) {
      const key = wanted.get(String(entry.key || '').toLowerCase());
      if (!key) continue;
      for (const c of entry.contexts || []) {
        const sentence = String(c.sentence || '').trim();
        let answer = c.answer || [];
        if (typeof answer === 'string') answer = answer.split('...').map((a) => a.trim());
        answer = answer.map(String).filter((a) => a.trim());
        const segs = makeCloze(sentence, answer);
        if (!segs) continue;
        (result[key] ||= []).push({ sentence, answer: segs.filter((s) => 'blank' in s).map((s) => s.blank),
          hint: String(c.hint_zh || '').trim(), note: String(c.note_zh || '').trim() });
      }
    }
  }
  return result;
}

export async function testConnection(cfg) {
  const out = await generate([{ key: 'take off', kind: 'phrase', translation: '起飞', source: '' }],
    { ...cfg, ai: { ...cfg.ai, per_item: 1 } });
  if (!out['take off']) throw new AIError('AI 有回应，但没给出能用的句子');
  return out['take off'][0].sentence;
}

// ---------- 逐句分析 ----------

function chunks(sentences, limit) {
  const out = [];
  let cur = [], size = 0;
  sentences.forEach((s, i) => {
    if (cur.length && size + s.length > limit) { out.push(cur); cur = []; size = 0; }
    cur.push(i);
    size += s.length;
  });
  if (cur.length) out.push(cur);
  return out;
}

function analyzePrompt(sentences, ids) {
  return ['Analyse these sentences. Return JSON shaped like this example:', JSON.stringify(S.ANALYZE_EXAMPLE), '', 'Sentences:',
    ...ids.map((i) => `[${i + 1}] ${sentences[i]}`)].join('\n');
}

/** 和 Python 版 ai.analyze 返回同样的结构：{phrases, words, sents, covered, warnings} */
export async function analyze(sentences, cfg, onProgress = () => {}) {
  const warnings = [];
  let budget = 0;
  const ids = [];
  for (let i = 0; i < sentences.length; i++) {
    if (budget + sentences[i].length > S.ANALYZE_MAX_CHARS) {
      warnings.push(`文档较长，只分析了前 ${S.ANALYZE_MAX_CHARS / 10000} 万字符，其余部分用词典识别`);
      break;
    }
    budget += sentences[i].length;
    ids.push(i);
  }
  const groups = chunks(ids.map((i) => sentences[i]), S.ANALYZE_CHUNK_CHARS).map((c) => c.map((j) => ids[j]));
  let done = 0;
  const results = await pool(groups.map((g) => async () => {
    const data = parse(await call(cfg, analyzePrompt(sentences, g),
      { system: S.ANALYZE_SYSTEM, schema: S.ANALYZE_SCHEMA, temperature: 0.2, maxTokens: 8000 }));
    onProgress(++done, groups.length);
    return data && Array.isArray(data.sentences) ? data.sentences : [];
  }), 4);

  const phrases = [], words = [], sents = new Map();
  let dropped = 0;
  const idset = new Set(ids);
  for (const entries of results) {
    for (const e of entries) {
      const sid = parseInt(e.sid, 10) - 1;
      if (!idset.has(sid)) continue;
      const sentence = sentences[sid];
      if (!sents.has(sid)) sents.set(sid, { sid, text: sentence, zh: '', units: [] });
      const rec = sents.get(sid);
      rec.zh = String(e.zh || '').trim() || rec.zh;
      const taken = [];
      const units = [...(e.units || [])].sort((a, b) => (PHRASE_TYPES.has(String(a.type)) ? 0 : 1) - (PHRASE_TYPES.has(String(b.type)) ? 0 : 1));
      for (const u of units) {
        let pieces = u.text || [];
        if (typeof pieces === 'string') pieces = pieces.split('...').map((x) => x.trim());
        pieces = pieces.map((x) => String(x).trim()).filter(Boolean);
        const segs = pieces.length ? makeCloze(sentence, pieces) : null;
        if (!segs) { dropped++; continue; }
        const sp = spans(segs);
        pieces = segs.filter((x) => 'blank' in x).map((x) => x.blank);
        let kind = String(u.type || 'word');
        const meaning = String(u.meaning_zh || '').trim().slice(0, 30);
        const formRaw = String(u.form || '').trim().toLowerCase();
        const isPhrase = PHRASE_TYPES.has(kind) || pieces.join(' ').split(' ').length > 1;
        let form;
        if (isPhrase) {
          form = normalizePhrase(formRaw) || normalizePhrase(pieces.join(' '));
          if (!form) { dropped++; continue; }
          if (!PHRASE_TYPES.has(kind)) kind = 'expression';
        } else {
          if (sp.some(([s0, e0]) => taken.some(([a, b]) => a <= s0 && e0 <= b))) continue; // 落在词组里的单词
          const w = WORD_RE.test(formRaw) ? formRaw : pieces[0].toLowerCase();
          if (!WORD_RE.test(w)) { dropped++; continue; }
          form = w;
          kind = 'word';
        }
        taken.push(...sp);
        rec.units.push({ pieces, form, type: kind, meaning, spans: sp });
        if (kind === 'word') words.push({ lemma: form, sid, surface: pieces[0], start: sp[0][0], end: sp[0][1], meaning });
        else phrases.push({ phrase: form, sid, sentence, pieces, surface: sentence.slice(sp[0][0], sp.at(-1)[1]), spans: sp, meaning, kind });
      }
    }
  }
  const missing = [...idset].filter((i) => !sents.has(i)).length;
  if (missing) warnings.push(`有 ${missing} 句 AI 没有返回分析`);
  if (dropped) warnings.push(`AI 给出的 ${dropped} 处在原文里对不上，已丢弃`);
  return { phrases, words, sents: [...sents.keys()].sort((a, b) => a - b).map((k) => sents.get(k)), covered: [...sents.keys()], warnings };
}

// ---------- 划选查词义 ----------

export async function gloss(selection, sentence, cfg) {
  const prompt = 'Return JSON like {"form": "turn sth down", "type": "phrasal verb", "meaning_zh": "拒绝"}.\n\n'
    + `Sentence: ${sentence}\nSelection: ${selection}`;
  const data = parse(await call(cfg, prompt, { system: S.GLOSS_SYSTEM, schema: S.GLOSS_SCHEMA, temperature: 0.2, maxTokens: 300 }));
  return data && typeof data === 'object' ? data : {};
}
