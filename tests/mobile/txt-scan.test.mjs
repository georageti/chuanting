import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import * as lx from '../../mobile/lib/lexicon.js';
import { usableSentences, extract } from '../../mobile/lib/extract.js';
import { memoryDB } from '../../mobile/lib/db.js';
import { createApi } from '../../mobile/lib/local-api.js';

const load = (name) => JSON.parse(gunzipSync(readFileSync(new URL(`../../mobile/dict/${name}`, import.meta.url))));
lx.init({ words: load('words.json.gz'), forms: load('forms.json.gz'), phrases: load('phrases.json.gz') });
const words = ['painstaking', 'prefix', 'affix', 'suffix', 'subconscious', 'ubiquitous', 'ephemeral', 'in fact'];
const entries = Array.from({ length: 722 }, (_, i) => words[i % words.length]).concat('eloquent');

test('723 行 TXT 词表逐行保留，兼容三种换行', () => {
  for (const sep of ['\n', '\r\n', '\r']) assert.deepEqual(usableSentences(entries.join(sep)), entries);
  const keys = new Set(extract(entries.join('\n')).map(c => c.key));
  for (const key of ['painstaking', 'ubiquitous', 'eloquent', 'in fact']) assert.ok(keys.has(key));
});

test('词表行不跨行拼词组，文章软换行仍保留上下文', () => {
  assert.ok(!extract('carry\nout\nubiquitous\n').some(c => c.key === 'carry out'));
  assert.deepEqual(usableSentences('She turned\nthe offer down at once.\nDr. Smith arrived later.'),
    ['She turned the offer down at once.', 'Dr. Smith arrived later.']);
});

test('超长段落不丢字、不切开单词', () => {
  const text = 'painstaking ' + 'ubiquitous ephemeral reluctant '.repeat(180) + 'eloquent';
  const parts = usableSentences(text);
  assert.ok(parts.length > 1);
  assert.ok(parts.every(s => s.length <= 1200));
  assert.equal(parts.join(' '), text);
  const token = 'x'.repeat(1500);
  assert.deepEqual(usableSentences(token), [token]);
});

test('上传 TXT → 显示识别结果 → 加入生词本 → 重新分析', async () => {
  const { api } = createApi(memoryDB());
  await api('/api/settings', { json: { ai: { provider: 'original' }, filter: { hide_level: 'none', hide_rank: 0 } } });
  const form = new FormData();
  form.append('file', new File([entries.join('\n')], 'wordlist.txt', { type: 'text/plain' }));
  const doc = await api('/api/docs', { method: 'POST', body: form });
  assert.ok(doc.totalWords > 0);
  assert.ok(doc.cands.some(c => c.key === 'eloquent'));
  const sents = await api(`/api/docs/${doc.id}/sentences`);
  assert.deepEqual(sents.map(s => s.text), entries);
  const result = await api('/api/items', { json: { doc_id: doc.id, keys: ['painstaking'] } });
  assert.equal(result.added, 1);
  assert.equal((await api('/api/items')).length, 1);
  const updated = await api(`/api/docs/${doc.id}/phrases`, { method: 'POST' });
  assert.equal(updated.totalWords, doc.totalWords);
  assert.equal((await api('/api/docs')).length, 1);
});
