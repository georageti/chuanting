// 本地接口：在浏览器里实现 Mac 版 Python 服务（chuanting/server.py + store.py）的 /api/*，
// 网页界面（web/app.js）不用改，照样调 api('/api/...')，只是不经过网络。
import S from './shared.js';
import * as lx from './lexicon.js';
import * as ex from './extract.js';
import * as ai from './ai.js';
import * as srs from './srs.js';
import { toText, UnsupportedFile } from './docs.js';

export class HTTPError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
const err = (msg, status = 400) => { throw new HTTPError(status, msg); };
const now = () => Date.now() / 1000;
const clone = (o) => JSON.parse(JSON.stringify(o));
const shuffle = (a) => { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const SPOKEN = S.SPOKEN;

function meaningOf(tr) {
  let first = tr.split('；')[0];
  first = first.replace(/^\s*([a-z]+\.\s*)+/, '').replace(/\[[^\]]*\]/g, '');
  return first.split(/[,，;；]/).map((s) => s.trim()).filter(Boolean).slice(0, 2).join('，');
}

function translationOf(c) {
  const tr = c.translation || '', ctx = c.ctx || '';
  if (ctx && !tr.includes(ctx)) return tr ? `本文：${ctx}；${tr}` : ctx;
  return tr;
}

function merge(base, over) {
  const out = { ...base };
  for (const [k, v] of Object.entries(over || {})) {
    out[k] = v && typeof v === 'object' && !Array.isArray(v) && base[k] && typeof base[k] === 'object' ? merge(base[k], v) : v;
  }
  return out;
}

export function createApi(db, { onScanProgress = () => {} } = {}) {
  // ---------- 设置 ----------
  async function loadConfig() {
    const row = await db.get('kv', 'config');
    return merge(clone(S.DEFAULT_CONFIG), row ? row.v : {});
  }
  async function saveConfig(patch) {
    const row = await db.get('kv', 'config');
    await db.put('kv', { k: 'config', v: merge(row ? row.v : {}, patch) });
  }
  async function publicConfig() {
    const cfg = await loadConfig();
    for (const p of ['deepseek', 'anthropic', 'openai']) {
      const k = cfg.ai[p].key;
      cfg.ai[p].key = k ? '••••' + k.slice(-4) : '';
      cfg.ai[p].hasKey = !!k;
    }
    cfg.levels = lx.LEVELS.map((id) => ({ id, name: lx.LEVEL_NAMES[id] }));
    return cfg;
  }

  // ---------- 生词本 ----------
  const getItem = (key) => db.get('items', key);
  const allItems = async () => (await db.all('items')).sort((a, b) => b.added - a.added);
  const knownSet = async () => new Set((await db.all('known')).map((k) => k.key));
  async function nextCtxId() {
    const row = await db.get('kv', 'ctxSeq');
    const n = (row ? row.v : 0) + 1;
    await db.put('kv', { k: 'ctxSeq', v: n });
    return n;
  }

  async function addItems(cands, docId) {
    let added = 0;
    for (const c of cands) {
      let it = await getItem(c.key);
      if (!it) {
        it = { key: c.key, kind: c.kind, translation: translationOf(c), phonetic: c.phonetic || '', tags: c.tags || [],
          level: c.level ?? null, rank: c.rank || 0, added: now(), state: null, examples: [], contexts: [] };
        added++;
      }
      for (const e of c.examples || []) {
        if (!it.examples.some((x) => x.sentence === e.sentence)) {
          it.examples.push({ sentence: e.sentence, surface: e.surface, pieces: e.pieces || null, docId: docId ?? null });
        }
      }
      await db.put('items', it);
    }
    return added;
  }

  async function addContexts(key, ctxs, source) {
    const it = await getItem(key);
    if (!it) return;
    for (const c of ctxs) {
      it.contexts.push({ id: await nextCtxId(), sentence: c.sentence, answer: c.answer, hint: c.hint || '', note: c.note || '',
        source, used: 0, created: now() });
    }
    await db.put('items', it);
  }
  const contextsOf = (it) => [...(it.contexts || [])].sort((a, b) => a.used - b.used || a.id - b.id);

  async function listItems() {
    const docs = new Map((await db.all('docs')).map((d) => [d.id, d.title]));
    return (await allItems()).map((it) => {
      const docId = it.examples[0]?.docId ?? null;
      const { examples, contexts, ...rest } = it;
      return { ...rest, contexts: (contexts || []).length, docId, docTitle: docs.get(docId) || '' };
    });
  }

  async function learnedToday() {
    const day = srs.today();
    return (await db.all('items')).filter((i) => i.state && i.state.learned === day).length;
  }

  async function stats() {
    const items = await db.all('items');
    const day = srs.today();
    const learned = items.filter((i) => i.state);
    const log = await db.all('log');
    const from = srs.addDays(day, -13);
    const counts = {};
    for (const l of log) if (l.day >= from) counts[l.day] = (counts[l.day] || 0) + 1;
    const cfg = await loadConfig();
    return {
      items: items.length, new: items.length - learned.length,
      due: learned.filter((i) => i.state.due <= day).length,
      mastered: learned.filter((i) => i.state.s >= srs.MASTERED_DAYS).length,
      known: (await db.all('known')).length,
      today: log.filter((l) => l.day === day).length,
      learnedToday: learned.filter((i) => i.state.learned === day).length,
      history: Array.from({ length: 14 }, (_, k) => { const d = srs.addDays(day, k - 13); return { day: d, n: counts[d] || 0 }; }),
      dailyNew: cfg.study.daily_new,
    };
  }

  // ---------- 卡片 ----------
  function makeCard(item, ctx) {
    const segs = ai.makeCloze(ctx.sentence, ctx.answer);
    if (!segs) return null;
    return {
      key: item.key, kind: item.kind, translation: item.translation, phonetic: item.phonetic || '',
      levelName: lx.LEVEL_NAMES[item.level] || '', isNew: item.state == null,
      ctxId: ctx.id, sentence: ctx.sentence, answer: ctx.answer, segments: segs,
      hint: ctx.hint || '', note: ctx.note || '', source: ctx.source || '',
      meaning: item.kind === 'sentence' ? item.translation : meaningOf(item.translation),
      spoken_key: item.key.split(' ').map((w) => SPOKEN[w] || w).join(' '),
    };
  }

  async function ensureContexts(items, cfg) {
    const warnings = [];
    let missing = items.filter((it) => !(it.contexts || []).some((c) => ai.makeCloze(c.sentence, c.answer)));
    for (const it of missing.filter((m) => m.kind === 'sentence')) {
      await addContexts(it.key, [{ sentence: it.key, answer: [it.key], hint: it.translation, note: '整句听写' }], 'sentence');
    }
    missing = missing.filter((m) => m.kind !== 'sentence');
    if (!missing.length) return warnings;
    let got = {};
    const provider = cfg.ai.provider;
    if (provider !== 'original') {
      const reqs = missing.map((it) => ({ key: it.key, kind: it.kind, translation: it.translation, source: it.examples[0]?.sentence || '' }));
      try { got = await ai.generate(reqs, cfg); }
      catch (e) { warnings.push(e instanceof ai.AIError ? `AI 生成失败，先用原文句子：${e.message}` : `AI 生成出错，先用原文句子：${e.name}`); }
    }
    for (const it of missing) {
      if (got[it.key]?.length) await addContexts(it.key, got[it.key], `ai:${provider}`);
      else {
        const orig = ai.fromOriginal(it, it.examples);
        if (orig.length) await addContexts(it.key, orig, 'original');
        else warnings.push(`“${it.key}”没有能挖空的句子，跳过`);
      }
    }
    return warnings;
  }

  // ---------- 扫描 ----------
  async function scanText(content, cfg) {
    const sentences = ex.usableSentences(content);
    const warnings = [];
    let analysis = null, source = 'dict';
    if ((cfg.extract?.phrases ?? 'ai') === 'ai') {
      const reason = ai.unavailableReason(cfg);
      if (reason && cfg.ai.provider !== 'original') warnings.push(`没有用 AI 分析，词组改用词典匹配：${reason}`);
      else if (!reason) {
        try {
          analysis = await ai.analyze(sentences, cfg, onScanProgress);
          warnings.push(...analysis.warnings);
          source = analysis.covered.length >= sentences.length ? 'ai' : 'mixed';
        } catch (e) {
          warnings.push(e instanceof ai.AIError ? `AI 分析失败，词组改用词典匹配：${e.message}` : `AI 分析出错，词组改用词典匹配：${e.name}`);
          analysis = null;
        }
      }
    }
    const cands = ex.extract(content, analysis);
    return [cands, { phrases: source, aiPhrases: cands.filter((c) => c.source === 'ai' && c.kind === 'phrase').length,
      warnings, sents: analysis ? analysis.sents : [] }];
  }

  async function docView(id, q) {
    const d = await db.get('docs', id);
    if (!d) err('文档不存在', 404);
    const cfg = (await loadConfig()).filter;
    const known = await knownSet();
    const inList = new Set((await db.all('items')).map((i) => i.key));
    let cands = clone(d.cands);
    if (q.get('show_all') === '1' || q.get('show_all') === 'true') cands = cands.filter((c) => !known.has(c.key));
    else {
      const rank = q.has('hide_rank') ? parseInt(q.get('hide_rank'), 10) : cfg.hide_rank;
      cands = ex.filterCandidates(cands, q.get('hide_level') || cfg.hide_level, rank, known);
    }
    for (const c of cands) c.inList = inList.has(c.key);
    const order = { phrase: 0, word: 1 };
    cands.sort((a, b) => order[a.kind] - order[b.kind] || b.count - a.count || (b.rank || 1e6) - (a.rank || 1e6));
    const { sents, ...meta } = d.meta || {};
    return { id: d.id, title: d.title, filename: d.filename, chars: d.chars, total: d.cands.length, cands,
      totalWords: d.cands.filter((c) => c.kind === 'word').length, totalPhrases: d.cands.filter((c) => c.kind === 'phrase').length,
      meta, hasAnalysis: !!(sents && sents.length) };
  }

  function dictSentences(d) {
    const texts = ex.usableSentences(d.text);
    const index = new Map(texts.map((t, i) => [t, i]));
    const sents = texts.map((t, i) => ({ sid: i, text: t, zh: '', units: [] }));
    for (const c of d.cands) {
      if (c.kind !== 'phrase' || c.basic) continue;
      for (const e of c.examples) {
        const sid = index.get(e.sentence);
        if (sid === undefined) continue;
        const pieces = e.pieces || ai.originalPieces(c.key, 'phrase', e.surface);
        const segs = ai.makeCloze(e.sentence, pieces);
        if (!segs) continue;
        sents[sid].units.push({ pieces: segs.filter((x) => 'blank' in x).map((x) => x.blank), form: c.key,
          type: c.ptype || 'phrase', meaning: c.translation.split('；')[0].slice(0, 30), spans: ai.spans(segs) });
      }
    }
    for (const s of sents) s.units.sort((a, b) => a.spans[0][0] - b.spans[0][0]);
    return sents;
  }

  async function addDoc(body) {
    let content, filename = '', title = '';
    const file = body instanceof FormData ? body.get('file') : null;
    if (file && file.name) {
      if (file.size > 20 * 1024 * 1024) err('文件太大了（超过 20MB）');
      try { content = await toText(file.name, await file.arrayBuffer()); }
      catch (e) { if (e instanceof UnsupportedFile) err(e.message); throw e; }
      filename = file.name;
      title = (body.get('title') || '') || file.name.replace(/\.[^.]+$/, '');
    } else {
      const text = body instanceof FormData ? String(body.get('text') || '') : String(body?.text || '');
      content = text;
      title = body instanceof FormData ? String(body.get('title') || '') : '';
      if (!title && text.trim()) {
        const first = text.trim().split('\n')[0];
        title = first.length <= 40 ? first : first.slice(0, 40).replace(/\s+\S*$/, '') + '…';
      }
    }
    if (!content.trim()) err('文档里没有文字');
    if (!/[A-Za-z]{3,}/.test(content)) err('没找到英文内容');
    const [cands, meta] = await scanText(content, await loadConfig());
    const id = await db.put('docs', { title: title || '未命名', filename, chars: content.length, created: now(), text: content, cands, meta });
    return docView(id, new URLSearchParams());
  }

  // ---------- 练习 ----------
  async function dailyNewQuota(cfg, limit) {
    const daily = cfg.study.daily_new;
    if (daily === 0) return limit;
    const room = Math.max(0, daily - await learnedToday());
    return limit == null ? room : Math.min(limit, room);
  }
  async function dueItems(limit) {
    const day = srs.today();
    const due = (await db.all('items')).filter((r) => r.state && r.state.due <= day)
      .sort((a, b) => (a.state.due < b.state.due ? -1 : a.state.due > b.state.due ? 1 : a.state.s - b.state.s));
    return limit == null ? due : due.slice(0, limit);
  }
  async function newItems(limit) {
    const items = (await db.all('items')).filter((r) => !r.state).sort((a, b) => a.added - b.added);
    return limit == null ? items : items.slice(0, limit);
  }
  async function pickItems(mode, limit, cfg) {
    if (mode === 'review') return dueItems(limit);
    if (mode === 'learn') return newItems(await dailyNewQuota(cfg, limit));
    if (mode === 'all') { const all = await allItems(); return limit == null ? all : all.slice(0, limit); }
    const due = await dueItems(limit);
    const remaining = limit == null ? null : Math.max(0, limit - due.length);
    return due.concat(await newItems(await dailyNewQuota(cfg, remaining)));
  }
  function sessionSize(v) {
    if (typeof v !== 'number' || !Number.isInteger(v) || v < 0 || v > 1000) err('每轮词数须为 0（全部）或 1–1000 的整数');
    return v;
  }
  async function sessionItems(body, cfg) {
    const mode = body.mode || 'mix';
    if (!['mix', 'review', 'learn', 'all'].includes(mode)) err('未知的练习方式');
    let items;
    if (body.keys != null) {
      if (!Array.isArray(body.keys) || body.keys.some((k) => typeof k !== 'string')) err('词条列表格式不正确');
      items = [];
      for (const k of [...new Set(body.keys)]) { const it = await getItem(k); if (it) items.push(it); }
    } else {
      const size = sessionSize(body.limit ?? cfg.study.session_size);
      items = await pickItems(mode, mode === 'all' || size === 0 ? null : size, cfg);
    }
    if (body.keys == null || !body.ordered) shuffle(items);
    return items;
  }

  async function record(key, mistakes, peeked, hinted) {
    const it = await getItem(key);
    if (!it) return null;
    const g = srs.grade(mistakes, peeked, hinted);
    it.state = it.state ? srs.review(it.state, g) : srs.learn(g);
    await db.put('items', it);
    await db.put('log', { day: srs.today(), key, grade: g, mistakes, at: now() });
    return { grade: g, state: it.state };
  }

  // ---------- 导入导出 ----------
  async function exportBackup() {
    const items = (await allItems()).map(({ examples, contexts, ...rest }) => ({ ...rest,
      examples: examples.map(({ sentence, surface, pieces }) => ({ sentence, surface, pieces })),
      contexts: contexts.map(({ sentence, answer, hint, note, source, used }) => ({ sentence, answer, hint, note, source, used })) }));
    return { format: 'chuanting-backup/1', exportedAt: new Date().toISOString(), app: 'mobile', items,
      known: await db.all('known'), log: (await db.all('log')).map(({ day, key, grade, mistakes, at }) => ({ day, key, grade, mistakes, at })) };
  }

  async function importBackup(data) {
    if (!data || data.format !== 'chuanting-backup/1') err('不是川听的学习记录文件');
    let added = 0, updated = 0;
    for (const src of data.items || []) {
      if (!src.key) continue;
      let it = await getItem(src.key);
      if (!it) {
        it = { key: src.key, kind: src.kind || 'word', translation: src.translation || '', phonetic: src.phonetic || '', tags: src.tags || [],
          level: src.level ?? null, rank: src.rank || 0, added: src.added || now(), state: src.state || null, examples: [], contexts: [] };
        added++;
      } else {
        // 同一个词：保留练得更多的那份进度
        const reps = (s) => (s ? s.reps || 0 : -1);
        if (reps(src.state) > reps(it.state) || (reps(src.state) === reps(it.state) && (src.state?.last || '') > (it.state?.last || ''))) {
          it.state = src.state; updated++;
        }
      }
      for (const e of src.examples || []) if (!it.examples.some((x) => x.sentence === e.sentence)) it.examples.push({ ...e, docId: null });
      for (const c of src.contexts || []) {
        if (!it.contexts.some((x) => x.sentence === c.sentence)) it.contexts.push({ ...c, id: await nextCtxId(), used: c.used || 0, created: now() });
      }
      await db.put('items', it);
    }
    const known = await knownSet();
    for (const k of data.known || []) if (k.key && !known.has(k.key)) await db.put('known', { key: k.key, added: k.added || now() });
    const seen = new Set((await db.all('log')).map((l) => `${l.day}|${l.key}|${l.at}`));
    let logs = 0;
    for (const l of data.log || []) if (!seen.has(`${l.day}|${l.key}|${l.at}`)) { await db.put('log', { ...l }); logs++; }
    return { added, updated, logs };
  }

  async function exportWordbook() {
    const entries = (await allItems()).map((it) => ({ word: it.key,
      note: [...it.examples.slice(0, 2).map((e) => e.sentence), ...it.contexts.slice(0, 2).map((c) => c.sentence)].join('\n\n'),
      tags: ['川听'], customTrans: it.translation, groups: [] }));
    return { format: 'wordbook/1', name: '川听生词', desc: '从文档里扫出的单词和词组', exportedAt: new Date().toISOString(),
      appVersion: '1.0', languageCode: 'en', entries };
  }

  // ---------- 路由 ----------
  const routes = [
    ['GET', /^\/api\/stats$/, () => stats()],
    ['GET', /^\/api\/settings$/, () => publicConfig()],
    ['POST', /^\/api\/settings$/, async (m, q, body) => {
      const patch = clone(body || {});
      if (patch.study && 'session_size' in patch.study) patch.study.session_size = sessionSize(patch.study.session_size);
      if (patch.study && 'daily_new' in patch.study) {
        const d = patch.study.daily_new;
        if (!Number.isInteger(d) || d < 0 || d > 1000) err('每天新词须为 0（不限）或 1–1000 的整数');
      }
      for (const p of ['deepseek', 'anthropic', 'openai']) {
        const k = patch.ai?.[p]?.key;
        if (typeof k === 'string' && k.startsWith('••••')) delete patch.ai[p].key;
      }
      await saveConfig(patch);
      return publicConfig();
    }],
    ['POST', /^\/api\/settings\/test$/, async () => {
      const cfg = await loadConfig();
      if (cfg.ai.provider === 'original') return { ok: true, sample: '当前是“原文句子”模式，不调用 AI' };
      try { return { ok: true, sample: await ai.testConnection(cfg) }; }
      catch (e) { if (e instanceof ai.AIError) return { ok: false, error: e.message }; throw e; }
    }],
    ['POST', /^\/api\/docs$/, (m, q, body) => addDoc(body)],
    ['GET', /^\/api\/docs$/, async () => (await db.all('docs')).sort((a, b) => b.created - a.created)
      .map(({ id, title, filename, chars, created }) => ({ id, title, filename, chars, created }))],
    ['GET', /^\/api\/docs\/(\d+)$/, (m, q) => docView(+m[1], q)],
    ['DELETE', /^\/api\/docs\/(\d+)$/, async (m) => { await db.del('docs', +m[1]); return { ok: true }; }],
    ['POST', /^\/api\/docs\/(\d+)\/phrases$/, async (m) => {
      const d = await db.get('docs', +m[1]);
      if (!d) err('文档不存在', 404);
      const [cands, meta] = await scanText(d.text, await loadConfig());
      await db.put('docs', { ...d, cands, meta });
      return docView(d.id, new URLSearchParams());
    }],
    ['GET', /^\/api\/docs\/(\d+)\/sentences$/, async (m) => {
      const d = await db.get('docs', +m[1]);
      if (!d) err('文档不存在', 404);
      const sents = d.meta?.sents?.length ? clone(d.meta.sents) : dictSentences(d);
      const inList = new Set((await db.all('items')).map((i) => i.key));
      for (const s of sents) {
        s.inList = inList.has(s.text.replace(/\s+/g, ' ').trim());
        for (const u of s.units) u.inList = inList.has(u.form) || inList.has(lx.lemma(u.form));
      }
      return sents;
    }],
    ['POST', /^\/api\/items\/pick$/, async (m, q, body) => {
      const d = await db.get('docs', +body.doc_id);
      if (!d) err('文档不存在', 404);
      const cfg = await loadConfig();
      const cands = [], keys = [], warnings = [];
      for (const pk of (body.picks || []).slice(0, 200)) {
        const sentence = String(pk.sentence || '');
        if (pk.whole) {
          const key = sentence.replace(/\s+/g, ' ').trim();
          if (!key || key.length > 400) { warnings.push('句子为空或太长（超过 400 个字符）'); continue; }
          cands.push({ key, kind: 'sentence', translation: String(pk.meaning || '').trim(), examples: [{ sentence, surface: sentence, pieces: [sentence] }] });
          keys.push(key);
          continue;
        }
        let pieces = (pk.pieces || [String(pk.text || '').trim()]).filter((p) => p && p.trim())
          .map((p) => p.replace(/\s+/g, ' ').replace(/^[ ,.;:!?"'“”‘’()]+|[ ,.;:!?"'“”‘’()]+$/g, ''));
        const segs = pieces.length && pieces.every(Boolean) ? ai.makeCloze(sentence, pieces) : null;
        if (!segs || pieces.join(' ').length > 80) { warnings.push(`“${pieces.join(' … ').slice(0, 30)}”在这句里找不到，或者太长了`); continue; }
        pieces = segs.filter((x) => 'blank' in x).map((x) => x.blank);
        const sp = ai.spans(segs);
        const surface = sentence.slice(sp[0][0], sp.at(-1)[1]);
        const words = pieces.join(' ').split(/\s+/);
        let form = String(pk.form || '').trim().toLowerCase();
        let meaning = String(pk.meaning || '').trim();
        if (!form) {
          form = words.length === 1 ? lx.lemma(words[0]) : words.map((w) => w.toLowerCase().replace(/’/g, "'")).join(' ');
          if (!meaning && !ai.unavailableReason(cfg)) {
            try {
              const g = await ai.gloss(pieces.join(' … '), sentence, cfg);
              meaning = String(g.meaning_zh || '').trim().slice(0, 30);
              if (words.length > 1 && ai.normalizePhrase(g.form)) form = ai.normalizePhrase(g.form);
              else if (words.length === 1 && /^[A-Za-z]+$/.test(String(g.form || '')) && !lx.word(form)) form = g.form.toLowerCase();
            } catch (e) { if (e instanceof ai.AIError) warnings.push(`没查到“${form}”在这句里的意思：${e.message}`); else throw e; }
          }
        }
        const kind = form.split(' ').length === 1 ? 'word' : 'phrase';
        const c = { key: form, kind, translation: '', ctx: '', examples: [{ sentence, surface, pieces, meaning }] };
        if (kind === 'word') {
          const info = lx.word(form);
          if (info) Object.assign(c, { translation: lx.shortTr(info.translation), phonetic: info.phonetic, tags: info.tags, level: info.level, rank: info.rank });
          c.ctx = meaning;
          if (!c.translation) { c.translation = meaning; c.ctx = ''; }
        } else {
          const info = lx.phrase(form);
          c.translation = meaning || (info ? lx.shortTr(info.translation) : '');
        }
        if (!c.translation) c.translation = '（自选）';
        cands.push(c);
        keys.push(form);
      }
      return { added: cands.length ? await addItems(cands, d.id) : 0, keys, warnings };
    }],
    ['POST', /^\/api\/items$/, async (m, q, body) => {
      const d = await db.get('docs', +body.doc_id);
      if (!d) err('文档不存在', 404);
      const keys = new Set(body.keys || []);
      return { added: await addItems(d.cands.filter((c) => keys.has(c.key)), d.id) };
    }],
    ['GET', /^\/api\/items$/, () => listItems()],
    ['GET', /^\/api\/item$/, async (m, q) => {
      const it = await getItem(q.get('key'));
      if (!it) err('不在生词本里', 404);
      return { ...it, examples: it.examples, contextList: contextsOf(it) };
    }],
    ['DELETE', /^\/api\/item$/, async (m, q) => { await db.del('items', q.get('key')); return { ok: true }; }],
    ['POST', /^\/api\/item\/regenerate$/, async (m, q, body) => {
      const it = await getItem(body.key);
      if (!it) err('不在生词本里', 404);
      it.contexts = [];
      await db.put('items', it);
      const warnings = await ensureContexts([it], await loadConfig());
      return { warnings, contextList: contextsOf(await getItem(body.key)) };
    }],
    ['POST', /^\/api\/known$/, async (m, q, body) => {
      if (body.on ?? true) { if (!(await db.get('known', body.key))) await db.put('known', { key: body.key, added: now() }); await db.del('items', body.key); }
      else await db.del('known', body.key);
      return { ok: true };
    }],
    ['GET', /^\/api\/known$/, async () => (await db.all('known')).sort((a, b) => b.added - a.added)],
    ['POST', /^\/api\/contexts\/generate$/, async (m, q, body) => {
      const items = body?.keys ? (await Promise.all(body.keys.map(getItem))).filter(Boolean) : await allItems();
      return { warnings: await ensureContexts(items, await loadConfig()) };
    }],
    ['POST', /^\/api\/session\/plan$/, async (m, q, body) => {
      const cfg = await loadConfig();
      const keys = (await sessionItems(body || {}, cfg)).map((it) => it.key);
      return { keys, total: keys.length, readMode: cfg.study.read_mode };
    }],
    ['POST', /^\/api\/session$/, async (m, q, body) => {
      body = body || {};
      const cfg = await loadConfig();
      const items = await sessionItems(body, cfg);
      const warnings = await ensureContexts(items, cfg);
      const cards = [];
      for (const it0 of items) {
        const it = await getItem(it0.key);
        for (const ctx of contextsOf(it)) { const card = makeCard(it, ctx); if (card) { cards.push(card); break; } }
      }
      const prepared = new Set(cards.map((c) => c.key));
      const requested = body.keys != null ? body.keys : items.map((it) => it.key);
      const skipped = [...new Set(requested)].filter((k) => !prepared.has(k));
      if (skipped.length) warnings.push(`有 ${skipped.length} 个词暂时无法出题，结束时会列出，未计入已练词数`);
      return { cards, warnings, skipped };
    }],
    ['POST', /^\/api\/review$/, async (m, q, body) => {
      if (body.ctxId) {
        const it = await getItem(body.key);
        const c = it?.contexts.find((x) => x.id === +body.ctxId);
        if (c) { c.used += 1; await db.put('items', it); }
      }
      const res = await record(body.key, parseInt(body.mistakes || 0, 10), !!body.peeked, !!body.hinted);
      if (!res) err('不在生词本里', 404);
      return res;
    }],
    ['GET', /^\/api\/export\/backup$/, () => exportBackup()],
    ['POST', /^\/api\/import\/backup$/, (m, q, body) => importBackup(body)],
    ['GET', /^\/api\/export\/wordbook$/, () => exportWordbook()],
  ];

  async function api(path, opts = {}) {
    const url = new URL(path, 'http://local');
    const method = (opts.method || (opts.json !== undefined || opts.body ? 'POST' : 'GET')).toUpperCase();
    const body = opts.json !== undefined ? opts.json : opts.body;
    for (const [m, re, fn] of routes) {
      const match = method === m && url.pathname.match(re);
      if (match) return fn(match, url.searchParams, body);
    }
    err(`手机版没有这个功能：${method} ${url.pathname}`, 404);
  }

  return { api, loadConfig, db };
}
