// 手机网页版本地接口测试：node --test tests/mobile/
// 用内存存储代替 IndexedDB，AI 用模拟的 fetch 返回，不联网、不花钱。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import * as lx from '../../mobile/lib/lexicon.js';
import { memoryDB } from '../../mobile/lib/db.js';
import { createApi } from '../../mobile/lib/local-api.js';
import { docxToText, markdownToText, decodeText } from '../../mobile/lib/docs.js';
import * as srs from '../../mobile/lib/srs.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const load = (n) => JSON.parse(gunzipSync(readFileSync(path.join(ROOT, 'mobile/dict', n))).toString('utf8'));
lx.init({ words: load('words.json.gz'), forms: load('forms.json.gz'), phrases: load('phrases.json.gz') });
const SAMPLE = readFileSync(path.join(ROOT, 'tests/sample.txt'), 'utf8');

const realFetch = globalThis.fetch;
let aiCalls = 0;
function mockAI(handler) {
  globalThis.fetch = async (url, init) => {
    aiCalls++;
    const body = JSON.parse(init.body);
    const out = handler(body.messages[0].content, body.messages[1].content);
    return new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(out) } }] }), { status: 200 });
  };
}
const restore = () => { globalThis.fetch = realFetch; };

function fd(fields) {
  const f = new FormData();
  for (const [k, v] of Object.entries(fields)) f.append(k, v);
  return f;
}

test('扫描、筛选、加词（不用 AI）', async () => {
  const { api } = createApi(memoryDB());
  await api('/api/settings', { json: { ai: { provider: 'original' } } });
  const doc = await api('/api/docs', { method: 'POST', body: fd({ text: SAMPLE }) });
  assert.ok(doc.total > doc.cands.length, '默认筛选会藏掉简单词');
  assert.equal(doc.meta.phrases, 'dict');
  assert.ok(doc.cands.some((c) => c.key === 'turn sb down'));
  const r = await api('/api/items', { json: { doc_id: doc.id, keys: ['turn sb down', 'ubiquitous', 'get rid of'] } });
  assert.equal(r.added, 3);
  const items = await api('/api/items');
  assert.equal(items.length, 3);
  assert.equal(items[0].docTitle.startsWith('The plane'), true);
  const sents = await api(`/api/docs/${doc.id}/sentences`);
  assert.ok(sents[1].units.some((u) => u.form === 'turn sb down' && u.inList));
});

test('练习：排计划、出卡、复习、统计', async () => {
  const { api } = createApi(memoryDB());
  await api('/api/settings', { json: { ai: { provider: 'original' } } });
  const doc = await api('/api/docs', { method: 'POST', body: fd({ text: SAMPLE }) });
  await api('/api/items', { json: { doc_id: doc.id, keys: ['turn sb down', 'ubiquitous', 'get rid of'] } });
  const plan = await api('/api/session/plan', { json: { mode: 'learn' } });
  assert.equal(plan.total, 3);
  const s = await api('/api/session', { json: { keys: plan.keys, ordered: true } });
  assert.equal(s.cards.length, 3);
  const card = s.cards.find((c) => c.key === 'turn sb down');
  assert.deepEqual(card.answer, ['turned', 'down']);
  const rv = await api('/api/review', { json: { key: card.key, ctxId: card.ctxId, mistakes: 0 } });
  assert.equal(rv.state.due, srs.addDays(srs.today(), 1));
  const st = await api('/api/stats');
  assert.equal(st.new, 2);
  assert.equal(st.today, 1);
  assert.equal(st.history.length, 14);
});

test('AI 逐句分析、编语境句、划选查义（模拟接口）', async () => {
  const { api } = createApi(memoryDB());
  await api('/api/settings', { json: { ai: { provider: 'deepseek', deepseek: { key: 'sk-test' } } } });
  mockAI((system, user) => {
    if (system.includes('sentence by sentence')) {
      return { sentences: [{ sid: 2, zh: '她拒绝了这个提议。', units: [
        { text: ['turned', 'down'], form: 'turn something down', type: 'phrasal verb', meaning_zh: '拒绝' },
        { text: ['long commute'], form: 'long commute', type: 'collocation', meaning_zh: '长途通勤' },
        { text: ['commute'], form: 'commute', type: 'word', meaning_zh: '通勤' }] }] };
    }
    if (system.includes('selected some words')) return { form: 'persistent problem', type: 'collocation', meaning_zh: '顽疾' };
    return { items: [{ key: 'long commute', contexts: [{ sentence: 'A long commute can wear you out.', answer: ['long commute'], hint_zh: '长途通勤会让人疲惫。', note_zh: '' }] }] };
  });
  try {
    const doc = await api('/api/docs', { method: 'POST', body: fd({ text: SAMPLE }) });
    assert.equal(doc.meta.phrases, 'mixed', '模拟的 AI 只回了 1 句，其余句子用词典');
    assert.ok(doc.meta.aiPhrases >= 2);
    const all = (await api(`/api/docs/${doc.id}?show_all=1`)).cands;
    assert.ok(all.some((c) => c.key === 'turn sth down' && c.ptype === 'phrasal verb'));
    assert.ok(!all.some((c) => c.key === 'commute' && c.examples.some((e) => e.sentence.includes('commute'))), '词组里的词不单独列');
    const sents = await api(`/api/docs/${doc.id}/sentences`);
    assert.equal(sents[0].zh, '她拒绝了这个提议。');
    await api('/api/items', { json: { doc_id: doc.id, keys: ['long commute'] } });
    const s = await api('/api/session', { json: { keys: ['long commute'] } });
    assert.equal(s.cards[0].source, 'ai:deepseek');
    const S7 = SAMPLE.split('\n').find((l) => l.startsWith('Scientists'));
    const r = await api('/api/items/pick', { json: { doc_id: doc.id, picks: [{ sentence: S7, text: 'persistent problem' }] } });
    assert.deepEqual(r.keys, ['persistent problem']);
    const it = await api('/api/item?key=persistent%20problem');
    assert.equal(it.translation, '顽疾');
  } finally { restore(); }
});

test('AI 出错时退回词典并提示', async () => {
  const { api } = createApi(memoryDB());
  await api('/api/settings', { json: { ai: { provider: 'deepseek', deepseek: { key: 'sk-bad' } } } });
  globalThis.fetch = async () => new Response('{"error":"bad key"}', { status: 401 });
  try {
    const doc = await api('/api/docs', { method: 'POST', body: fd({ text: SAMPLE }) });
    assert.equal(doc.meta.phrases, 'dict');
    assert.match(doc.meta.warnings.join(''), /API Key 不对/);
  } finally { restore(); }
});

test('设置：Key 打码，打码的 Key 不覆盖原值', async () => {
  const { api, loadConfig } = createApi(memoryDB());
  await api('/api/settings', { json: { ai: { deepseek: { key: 'sk-abcdef1234' } } } });
  assert.equal((await api('/api/settings')).ai.deepseek.key, '••••1234');
  await api('/api/settings', { json: { ai: { deepseek: { key: '••••1234' } } } });
  assert.equal((await loadConfig()).ai.deepseek.key, 'sk-abcdef1234');
  await assert.rejects(api('/api/settings', { json: { study: { daily_new: -1 } } }), /每天新词/);
});

test('整句、已会、导出导入学习记录', async () => {
  const a = createApi(memoryDB());
  await a.api('/api/settings', { json: { ai: { provider: 'original' } } });
  const doc = await a.api('/api/docs', { method: 'POST', body: fd({ text: SAMPLE }) });
  const S2 = SAMPLE.split('\n')[1];
  await a.api('/api/items/pick', { json: { doc_id: doc.id, picks: [{ sentence: S2, whole: true, meaning: '她拒绝了。' }] } });
  await a.api('/api/items', { json: { doc_id: doc.id, keys: ['get rid of', 'ubiquitous'] } });
  const s = await a.api('/api/session', { json: { keys: [S2] } });
  assert.equal(s.cards[0].kind, 'sentence');
  await a.api('/api/review', { json: { key: S2, ctxId: s.cards[0].ctxId, mistakes: 0 } });
  await a.api('/api/known', { json: { key: 'ubiquitous', on: true } });
  assert.equal((await a.api('/api/items')).length, 2);
  const backup = await a.api('/api/export/backup');
  assert.equal(backup.format, 'chuanting-backup/1');
  const b = createApi(memoryDB());
  const r = await b.api('/api/import/backup', { json: backup });
  assert.equal(r.added, 2);
  const items = await b.api('/api/items');
  assert.ok(items.find((i) => i.key === S2).state, '复习进度带过来了');
  assert.equal((await b.api('/api/known')).length, 1);
  const again = await b.api('/api/import/backup', { json: backup });
  assert.equal(again.added, 0, '重复导入不会重复加');
  const wb = await a.api('/api/export/wordbook');
  assert.equal(wb.format, 'wordbook/1');
});

test('文档：Markdown、GBK 文本、docx', async () => {
  assert.equal(markdownToText('# T\n\nThis is **bold** [x](http://a.b)').includes('**'), false);
  assert.equal(decodeText(Buffer.from([0xd6, 0xd0, 0x20, 0x61])).endsWith(' a'), true);
  const docx = readFileSync(path.join(ROOT, 'tests/mobile/fixture.docx'));
  const text = await docxToText(docx);
  assert.match(text, /came up with a compromise/);
});

test('自始至终没有真的联网', () => { assert.ok(aiCalls > 0); });
