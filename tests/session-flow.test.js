// node --test tests/session-flow.test.js
// Execute the real app script; only browser DOM, audio and HTTP are replaced.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness(count = 143, skipped = []) {
  const elements = new Map();
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, {
      value: selector === '#session-size' ? '100' : '', hidden: true, disabled: false,
      textContent: '', innerHTML: '', style: {}, dataset: {}, handlers: {}, scrollHeight: 20,
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener(type, fn) { this.handlers[type] = fn; },
      setAttribute() {}, toggleAttribute() {}, focus() {}, select() {},
      querySelector: element, querySelectorAll: () => [],
    });
    return elements.get(selector);
  };
  const keys = Array.from({ length: count }, (_, i) => `term${i}`);
  const requests = [], reviews = [];
  const faults = { batch: null, review: false, pendingReview: null, pendingBatch: null };
  const cfg = { study: { session_size: 100, read_mode: 'show' } };
  const json = (data, ok = true) => ({ ok, status: ok ? 200 : 503,
    headers: { get: () => 'application/json' }, json: async () => data });
  const fetch = async (url, opts = {}) => {
    const body = opts.body ? JSON.parse(opts.body) : {};
    if (url === '/api/settings') return json(cfg);
    if (url === '/api/stats') return json({ due: count, new: 0, items: count, learnedToday: 0,
      dailyNew: 20, mastered: 0, today: 0, history: Array.from({ length: 14 }, () => ({ day: '2026-11-09', n: 0 })) });
    if (url === '/api/session/plan') {
      const selected = body.keys || keys;
      return json({ keys: selected, total: selected.length, readMode: 'show' });
    }
    if (url === '/api/session') {
      requests.push(body.keys);
      if (faults.pendingBatch) await faults.pendingBatch;
      if (faults.batch === body.keys[0]) { faults.batch = null; return json({ error: 'test load failure' }, false); }
      return json({ cards: body.keys.filter(k => !skipped.includes(k)).map(key => ({ key, kind: 'word',
        answer: [key], sentence: `Use ${key} today.`, segments: [{ t: 'Use ' }, { blank: key }, { t: ' today.' }],
        translation: '测试', ctxId: 1 })), warnings: [], skipped: body.keys.filter(k => skipped.includes(k)) });
    }
    if (url === '/api/review') {
      if (faults.pendingReview) await faults.pendingReview;
      if (faults.review) { faults.review = false; return json({ error: 'test save failure' }, false); }
      reviews.push(body.key);
      return json({ grade: body.peeked ? 1 : 3, state: { due: '2026-11-10' } });
    }
    if (url === '/api/known') return json({ ok: true });
    throw new Error(`Unexpected HTTP request: ${url}`);
  };
  const context = vm.createContext({ document: { querySelector: element, querySelectorAll: () => [],
    body: element('body'), addEventListener() {} }, fetch,
    matchMedia: () => ({ matches: true }), location: { hash: '' }, history: { replaceState() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {}, console });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8') +
    '\nglobalThis.testAPI = { P, startSession, nextCard, finish, Audio_ };', context);
  const app = context.testAPI;
  app.Audio_.playCloze = app.Audio_.prefetch = app.Audio_.say = app.Audio_.stop = () => {};
  return { ...app, keys, element, requests, reviews, faults };
}

test('full session crosses every batch and completes all 143 unique words', async () => {
  const h = harness();
  await h.startSession('all');
  assert.equal(h.element('#count').textContent, '1 / 143');
  assert.equal(h.P.cards.length, 20);
  for (const key of h.keys) {
    assert.equal(h.P.cur.card.key, key);
    await h.finish(true);
    await h.nextCard();
  }
  assert.deepEqual(h.reviews, h.keys);
  assert.equal(h.P.results.length, 143);
  assert.equal(h.requests.length, 8);
  assert.equal(h.element('#practice-done').hidden, false);
  assert.match(h.element('#done-summary').innerHTML, /实际练了 <b>143<\/b>/);
});

test('batch failure retries the same keys without advancing or dropping words', async () => {
  const h = harness(41);
  await h.startSession('all');
  for (let i = 0; i < 19; i++) { await h.finish(true); await h.nextCard(); }
  await h.finish(true);
  h.faults.batch = 'term20';
  await h.nextCard();
  assert.equal(h.P.i, 19);
  assert.equal(h.P.offset, 20);
  assert.equal(h.element('#next').textContent, '重试加载下一批');
  await h.nextCard();
  assert.equal(h.P.cur.card.key, 'term20');
  assert.deepEqual(h.requests[1], h.requests[2]);
  for (let i = 20; i < 41; i++) { await h.finish(true); await h.nextCard(); }
  assert.deepEqual(h.reviews, h.keys);
});

test('save failure keeps the current card and retries before proceeding', async () => {
  const h = harness(2);
  await h.startSession('all');
  h.faults.review = true;
  await h.finish(true);
  assert.equal(h.P.results.length, 0);
  assert.equal(h.element('#next').textContent, '重试保存');
  assert.equal(h.element('#known-btn').disabled, true);
  await h.nextCard();
  assert.equal(h.P.i, 0);
  assert.equal(h.P.results.length, 1);
  await h.nextCard();
  assert.equal(h.P.cur.card.key, 'term1');
  assert.deepEqual(h.reviews, ['term0']);
});

test('double next and pending saves cannot skip cards or finish prematurely', async () => {
  const h = harness(21);
  await h.startSession('all');
  let release;
  h.faults.pendingReview = new Promise(r => { release = r; });
  const pending = h.finish(true);
  await h.nextCard();
  assert.equal(h.P.i, 0);
  release();
  await pending;
  h.faults.pendingReview = null;
  await Promise.all([h.nextCard(), h.nextCard()]);
  assert.equal(h.P.i, 1);
  assert.equal(h.P.results.length, 1);
});

test('unusable full batch and final word are explicitly reported, not counted as practiced', async () => {
  const skipped = Array.from({ length: 20 }, (_, i) => `term${i}`).concat('term42');
  const h = harness(43, skipped);
  await h.startSession('all');
  assert.equal(h.P.cur.card.key, 'term20');
  for (let i = 20; i < 42; i++) { await h.finish(true); await h.nextCard(); }
  assert.equal(h.P.results.length, 22);
  assert.equal(h.P.skipped.length, 21);
  assert.equal(h.element('#practice-done h1').textContent, '本轮练习已结束');
  assert.match(h.element('#done-summary').innerHTML, /未练到 21 个/);
  assert.match(h.element('#done-summary').innerHTML, /term42/);
});

test('a group larger than thirty reaches the plan intact', async () => {
  const h = harness(55);
  await h.startSession('all', h.keys);
  assert.equal(h.P.total, 55);
  assert.equal(h.P.keys.length, 55);
});

test('quitting during a batch discards the late response', async () => {
  const h = harness(21);
  await h.startSession('all');
  for (let i = 0; i < 19; i++) { await h.finish(true); await h.nextCard(); }
  await h.finish(true);
  let release;
  h.faults.pendingBatch = new Promise(r => { release = r; });
  const pending = h.nextCard();
  h.element('#quit').handlers.click();
  release();
  await pending;
  assert.equal(h.element('#practice-home').hidden, false);
  assert.equal(h.element('#practice-run').hidden, true);
  assert.equal(h.P.cur, null);
  assert.equal(h.P.cards.length, 20);
});
