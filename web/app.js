// 川听网页：练习（听挖空句 → 拼写）、文档扫描、生词本、设置。和本地服务 /api/* 通信。
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || 'POST';
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(opts.json);
  }
  const res = await fetch(path, init);
  const type = res.headers.get('content-type') || '';
  const data = type.includes('json') ? await res.json() : await res.blob();
  if (!res.ok) throw new Error(data?.error || data?.detail || `出错了（${res.status}）`);
  return data;
}

let toastTimer;
function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), ms);
}
function loading(on, text = '处理中…') {
  $('#loading').hidden = !on;
  $('#loading-text').textContent = text;
}

// ---------- 图标：taste-skill icons.mjs 下载的 Lucide 线性图标，放在 icons/ui-*.svg，内联后跟随文字颜色 ----------
const ICONS = {};
async function icons(root = document) {
  const els = $$('[data-i]:not([data-i-done])', root);
  await Promise.all([...new Set(els.map((e) => e.dataset.i))].filter((n) => !ICONS[n]).map(async (n) => {
    try { ICONS[n] = (await (await fetch(`icons/ui-${n}.svg`)).text()).replace(/ role="img" aria-label="[^"]*"/, ' aria-hidden="true"'); } catch { ICONS[n] = ''; }
  }));
  for (const e of els) { e.innerHTML = ICONS[e.dataset.i] || ''; e.dataset.iDone = '1'; }
}

// ---------- CountUp（react-bits 的数字滚动，改写成原生 JS）：每一位一列 0-9，竖着滚到目标 ----------
function roll(el, value) {
  const str = String(value);
  if (reduced) { el.textContent = str; return; }
  if (el.dataset.v === str) return;
  el.dataset.v = str;
  el.setAttribute('aria-label', str);
  el.innerHTML = [...str].map((ch) => /\d/.test(ch)
    ? `<span class="roll__col" aria-hidden="true"><span class="roll__strip">${'0123456789'.split('').map((d) => `<span>${d}</span>`).join('')}</span></span>`
    : `<span aria-hidden="true">${esc(ch)}</span>`).join('');
  // 用定时器而不是 requestAnimationFrame：页面在后台时 rAF 不触发，数字会一直停在 0
  setTimeout(() => {
    $$('.roll__strip', el).forEach((s, i) => {
      const d = Number(str.replace(/\D/g, '')[i]);
      s.style.transitionDelay = `${i * 70}ms`;
      s.style.transform = `translateY(-${d}em)`;
    });
  }, 30);
}

// ---------- DecryptedText（react-bits，改写成原生 JS）：字母先乱跳，再从左到右一个个落定 ----------
function decrypt(el, text, { speed = 38 } = {}) {
  if (reduced) { el.textContent = text; return; }
  const pool = 'abcdefghijklmnopqrstuvwxyz';
  const chars = [...text];
  let settled = 0, tick = 0;
  el.classList.add('decrypt');
  clearInterval(el._dt);
  el._dt = setInterval(() => {
    tick++;
    if (tick % 2 === 0) settled++;
    el.innerHTML = chars.map((c, i) => {
      if (i < settled || /\s|[^A-Za-z]/.test(c)) return esc(c);
      return `<span class="enc">${pool[(Math.random() * 26) | 0]}</span>`;
    }).join('');
    if (settled >= chars.length) { clearInterval(el._dt); el.textContent = text; el.classList.remove('decrypt'); }
  }, speed);
}

// ---------- 标签页 ----------
const pages = { practice: loadHome, docs: loadDocs, words: loadWords, settings: loadSettings };
function showTab(name) {
  $$('#tabs > button').forEach((b) => {
    b.classList.toggle('active', b.dataset.tab === name);
    b.toggleAttribute('aria-current', b.dataset.tab === name);
  });
  $$('.page').forEach((p) => p.classList.toggle('active', p.id === name));
  document.body.classList.toggle('is-focus', name === 'practice' && !$('#practice-run').hidden);
  pages[name]?.();
  history.replaceState(null, '', '#' + name);
}
$$('#tabs > button').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));

// ---------- 声音 ----------
// 波形条的高度取自 taste-skill voice 片段，静止时也是一段真实的波形
$('#wave').innerHTML = [4, 7, 12, 9, 16, 11, 20, 14, 8, 17, 12, 6, 10, 18, 13, 7, 15, 9, 5, 11, 7, 14, 19, 10, 6, 12, 8, 4]
  .map((h) => `<i style="--h:${h + 6}"></i>`).join('');

const Audio_ = {
  ctx: null,
  token: 0,
  current: null,
  playing(on) { $('#player').classList.toggle('is-playing', on); },
  stop() {
    this.token++;
    if (this.current) { this.current.pause(); this.current = null; }
    this.playing(false);
  },
  url(text, { lang = 'en', slow = false } = {}) {
    return `/api/tts?lang=${lang}&slow=${slow ? 1 : 0}&text=${encodeURIComponent(text)}`;
  },
  prefetch(card) {
    for (const s of card.segments) if (s.t && /[A-Za-z0-9]/.test(s.t)) fetch(this.url(s.t)).catch(() => {});
    fetch(this.url(card.sentence)).catch(() => {});
  },
  playUrl(url, token, onStart) {
    return new Promise((resolve, reject) => {
      if (token !== this.token) return resolve();
      const a = new Audio(url);
      this.current = a;
      a.onplaying = () => onStart?.();
      a.onended = resolve;
      // 被重听或换卡打断不算错误
      const fail = (e) => (token !== this.token || e?.name === 'AbortError' ? resolve() : reject(new Error('语音加载失败')));
      a.onerror = fail;
      a.play().catch(fail);
    });
  },
  beep(token) {
    if (token !== this.token) return Promise.resolve();
    this.ctx = this.ctx || new (window.AudioContext || window.webkitAudioContext)();
    const ctx = this.ctx, o = ctx.createOscillator(), g = ctx.createGain(), t = ctx.currentTime;
    o.frequency.value = 880;
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(0.25, t + 0.02);
    g.gain.setValueAtTime(0.25, t + 0.26);
    g.gain.linearRampToValueAtTime(0, t + 0.3);
    o.connect(g).connect(ctx.destination);
    o.start(t);
    o.stop(t + 0.32);
    $('#player').classList.add('is-beep');
    return new Promise((r) => setTimeout(() => { $('#player').classList.remove('is-beep'); r(); }, 380));
  },
  async playCloze(card, slow = false) {
    this.stop();
    const token = this.token;
    this.playing(true);
    try {
      if (P.mode === 'full' || card.kind === 'sentence') { await this.playUrl(this.url(card.sentence, { slow }), token); return; }
      for (const s of card.segments) {
        if (token !== this.token) return;
        if (s.blank !== undefined) await this.beep(token);
        else if (/[A-Za-z0-9]/.test(s.t)) await this.playUrl(this.url(s.t, { slow }), token);
      }
    } catch (e) {
      toast(e.message);
    } finally {
      if (token === this.token) this.playing(false);
    }
  },
  async say(text, opts) {
    this.stop();
    const token = this.token;
    this.playing(true);
    try { await this.playUrl(this.url(text, opts), token, opts?.onStart); } catch (e) { toast(e.message); } finally { if (token === this.token) this.playing(false); }
  },
};

// ---------- 练习首页 ----------
function drawChart(history) {
  const max = Math.max(4, ...history.map((h) => h.n));
  const w = 400 / history.length, bw = w * 0.72;
  $('#chart').innerHTML = '<path class="grid" d="M0 30.5H400M0 60.5H400M0 90.5H400"/>' + history.map((h, i) => {
    const bh = Math.max(3, (h.n / max) * 116);
    return `<rect rx="3" x="${(i * w + (w - bw) / 2).toFixed(2)}" y="${(120 - bh).toFixed(2)}" width="${bw.toFixed(2)}" height="${bh.toFixed(2)}"
      ${i === history.length - 1 ? 'class="today"' : ''} style="animation-delay:${i * 25}ms"><title>${h.day}：${h.n} 次</title></rect>`;
  }).join('');
  const fmt = (d) => `${Number(d.slice(5, 7))}月${Number(d.slice(8))}日`;
  $('#chart-axis').innerHTML = `<span>${fmt(history[0].day)}</span><span>${fmt(history[7].day)}</span><span>今天</span>`;
  $('#chart-total').textContent = `共 ${history.reduce((a, h) => a + h.n, 0)} 次`;
}

async function loadHome() {
  const [s, cfg] = await Promise.all([api('/api/stats'), api('/api/settings')]);
  $('#session-size').value = String(cfg.study.session_size ?? 100);
  const room = s.dailyNew === 0 ? s.new : Math.max(0, (s.dailyNew ?? 20) - s.learnedToday);
  const newToday = Math.min(s.new, room);
  roll($('#today-num'), s.due + newToday);
  $('#today-split').textContent = s.items ? `复习 ${s.due} 个 · 新词 ${newToday} 个 · 今天已练 ${s.today} 次` : '生词本还是空的，先去“文档”扫描一篇文章。';
  $('#home-sub').textContent = s.items ? `生词本 ${s.items} 个，已掌握 ${s.mastered} 个。` : '';
  roll($('#n-due'), s.due);
  roll($('#n-new'), s.new);
  roll($('#n-all'), s.items);
  drawChart(s.history);
  $('#side-foot').textContent = `今天练了 ${s.today} 次`;
}

$$('#practice-home [data-mode]').forEach((b) => b.addEventListener('click', () => startSession(b.dataset.mode)));
$('#session-size').addEventListener('change', async (e) => {
  try {
    await api('/api/settings', { json: { study: { session_size: Number(e.target.value) } } });
    toast('已保存每轮词数', 1200);
  } catch (err) { toast(err.message); }
});

$('#export-mp3').addEventListener('click', async () => {
  loading(true, '正在生成语境并合成语音，词多的话要等一两分钟…');
  try {
    const res = await fetch('/api/export/mp3', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: 'mix', limit: 30 }) });
    if (!res.ok) throw new Error((await res.json()).error || '导出失败');
    const blob = await res.blob();
    const cd = res.headers.get('content-disposition') || '';
    const m = cd.match(/filename\*=UTF-8''([^;]+)/) || cd.match(/filename="?([^";]+)/);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = m ? decodeURIComponent(m[1]) : '川听.mp3';
    a.click();
    toast('已导出，在“下载”文件夹里');
  } catch (e) {
    toast(e.message);
  } finally {
    loading(false);
  }
});

// ---------- 练习 ----------
const P = { cards: [], i: 0, cur: null, results: [], keys: [], offset: 0, total: 0, skipped: [], token: 0, busy: false };

async function prepareThrough(index, token) {
  while (index >= P.cards.length && P.offset < P.keys.length) {
    const keys = P.keys.slice(P.offset, P.offset + 20);
    loading(true, `正在准备第 ${P.offset + 1}–${P.offset + keys.length} 个词的语境句（共 ${P.total} 个）…`);
    const data = await api('/api/session', { json: { keys, ordered: true } });
    if (token !== P.token) return;
    // 只有成功后才前移；加载失败点“下一个”会重试同一批，不会漏词。
    P.offset += keys.length;
    P.cards.push(...data.cards);
    P.skipped.push(...(data.skipped || []));
    if (data.warnings?.length) toast(data.warnings.join('；'), 6000);
  }
}

async function startSession(mode, keys) {
  if (P.busy) return;
  P.busy = true;
  const token = ++P.token;
  loading(true, '正在安排本轮练习…');
  try {
    const plan = await api('/api/session/plan', { json: { mode, limit: Number($('#session-size').value), keys } });
    if (token !== P.token) return;
    if (!plan.keys.length) {
      return toast(mode === 'review' ? '今天没有要复习的词；想多练可以选“全部过一遍”' : mode === 'learn' ? '没有新词了，或者今天的新词额度已用完' : '没有可练的词，可以去生词本选词练习');
    }
    Object.assign(P, { cards: [], i: 0, cur: null, results: [], keys: plan.keys, offset: 0,
      total: plan.total, skipped: [], mode: plan.readMode || 'cloze' });
    await prepareThrough(0, token);
    if (token !== P.token) return;
    showTab('practice');
    $('#practice-home').hidden = true;
    $('#practice-done').hidden = true;
    $('#practice-run').hidden = false;
    document.body.classList.add('is-focus');
    if (P.cards.length) showCard(); else showDone();
  } catch (e) {
    toast(e.message);
  } finally {
    if (token === P.token) { P.busy = false; loading(false); }
  }
}

function showCard() {
  const c = P.cards[P.i];
  // 换卡片时的运动模糊：重新触发动画
  const card = $('#card');
  card.classList.remove('swap');
  void card.offsetWidth;
  card.classList.add('swap');
  P.cur = { card: c, mistakes: 0, peeked: false, hinted: false, done: false };
  $('#bar').style.width = `${(P.results.length / P.total) * 100}%`;
  $('#count').textContent = `${P.i + 1} / ${P.total}${P.skipped.length ? ` · ${P.skipped.length} 个暂无法出题` : ''}`;
  $('#next').disabled = false;
  $('#known-btn').disabled = false;
  $('#next').textContent = '下一个';
  const isSentence = c.kind === 'sentence';
  $('#c-kind').textContent = isSentence ? '句子' : c.kind === 'phrase' ? '词组' : '单词';
  $('#c-level').textContent = c.levelName || '';
  $('#c-new').hidden = !c.isNew;
  $('#c-text').innerHTML = c.segments.map((s) => (s.blank !== undefined ? '<span class="blank"></span>' : esc(s.t))).join('');
  $('#c-zh').textContent = c.hint || c.meaning || '';
  const first = c.answer.map((a) => a.split(/\s+/).map((w) => w[0] + '·'.repeat(Math.max(0, w.length - 1))).join(' ')).join(' … ');
  $('#c-first').textContent = first;
  for (const id of ['c-text', 'c-zh', 'c-first']) $('#' + id).hidden = true;
  $$('.tools .tool').forEach((b) => { b.classList.remove('on'); b.setAttribute('aria-pressed', 'false'); });
  $('#show-text').hidden = isSentence; // 整句听写时“显示句子”等于看答案，不提供
  if (!isSentence && (P.mode === 'show' || P.mode === 'full')) {
    $('#c-text').hidden = false;
    $('#show-text').classList.add('on');
    $('#show-text').setAttribute('aria-pressed', 'true');
  }
  $('#answer').value = '';
  $('#answer').className = isSentence ? 'is-sentence' : '';
  const nWords = c.answer.join(' ').split(/\s+/).length;
  $('#answer').placeholder = isSentence ? '把听到的整句打出来（标点、大小写不用管）'
    : c.answer.length > 1 ? `要填 ${c.answer.length} 处，用空格隔开`
    : nWords > 1 ? `要填 ${nWords} 个词` : '输入听到的词';
  $('#keys').innerHTML = '<kbd>↵</kbd> 提交　<kbd>⇧</kbd><kbd>↵</kbd> 不会　<kbd>Esc</kbd> 重听';
  growAnswer();
  $('#answer').disabled = false;
  $('#check').disabled = false;
  $('#feedback').innerHTML = '';
  $('#reveal').hidden = true;
  $('#give-up-row').hidden = false;
  $('#answer').focus();
  Audio_.playCloze(c);
  const next = P.cards[P.i + 1];
  if (next) Audio_.prefetch(next);
}

function growAnswer() {
  const a = $('#answer');
  a.style.height = 'auto';
  a.style.height = a.scrollHeight + 2 + 'px';
}
$('#answer').addEventListener('input', growAnswer);
$('#answer').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || e.isComposing) return;
  e.preventDefault();
  // 不再往外传：否则答案刚揭晓，页面上“回车 = 下一个”的处理就会立刻跳到下一张
  e.stopPropagation();
  if (e.shiftKey) giveUp(); else checkAnswer();
});
function giveUp() {
  if (P.cur && !P.cur.done) { P.cur.peeked = true; finish(false); }
}

const norm = (s) => s.toLowerCase().replace(/[’‘]/g, "'").replace(/[^a-z0-9'\- ]+/g, ' ').replace(/\s+/g, ' ').trim();

function diffHtml(got, want) {
  let out = '';
  for (let i = 0; i < want.length; i++) {
    const ok = got[i] && got[i].toLowerCase() === want[i].toLowerCase();
    out += `<span class="${ok ? '' : 'x'}">${esc(got[i] ?? '_')}</span>`;
  }
  if (got.length > want.length) out += `<span class="x">${esc(got.slice(want.length))}</span>`;
  return out;
}

// 整句按词对齐（最长公共子序列）：只标真正错的词，漏掉的位置标 ＿，后面的词不会因为错位全部变红
function wordDiff(got, want) {
  const n = got.length, m = want.length;
  const L = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = got[i] === want[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let i = 0, j = 0, extra = 0, missing = 0;
  while (i < n || j < m) {
    if (i < n && j < m && got[i] === want[j]) { out.push(esc(got[i])); i++; j++; }
    else if (j < m && (i >= n || L[i][j + 1] >= L[i + 1][j])) { out.push('<span class="x">＿</span>'); j++; missing++; }
    else { out.push(`<span class="x">${esc(got[i])}</span>`); i++; extra++; }
  }
  return { html: out.join(' '), bad: Math.max(extra, missing) };
}

function checkAnswer() {
  const st = P.cur;
  if (!st || st.done) return nextCard();
  const got = norm($('#answer').value);
  if (!got) return Audio_.playCloze(st.card);
  const want = norm(st.card.answer.join(' '));
  if (got === want) return finish(true);
  st.mistakes++;
  const inp = $('#answer');
  inp.classList.remove('wrong');
  void inp.offsetWidth;
  inp.classList.add('wrong');
  if (st.mistakes >= 3) return finish(false);
  let detail = '';
  if (st.card.kind === 'sentence') {
    const { html, bad } = wordDiff(got.split(' '), want.split(' '));
    detail = `<div class="diff">${html}</div><div class="muted">有 ${bad} 处不对，＿ 是漏掉的词</div>`;
  } else if (got.length && want.split(' ').length === got.split(' ').length) {
    detail = `<div class="diff">${diffHtml(got, want)}</div>`;
  }
  $('#feedback').innerHTML = `<span class="bad">不对，再听一遍（还能试 ${3 - st.mistakes} 次）</span>` + detail;
  Audio_.playCloze(st.card);
  inp.select();
}

async function finish(correct) {
  const st = P.cur;
  if (!st || st.saving || st.saved) return;
  const token = P.token;
  st.saving = true;
  st.correct = correct;
  st.done = true;
  if (!correct) st.peeked = st.peeked || st.mistakes < 3;
  const c = st.card;
  $('#answer').disabled = true;
  $('#check').disabled = true;
  $('#answer').classList.remove('right', 'wrong');
  $('#answer').classList.add(correct ? 'right' : 'wrong');
  if (!correct) { $('#answer').value = c.answer.join(' '); growAnswer(); }
  $('#feedback').innerHTML = correct
    ? `<span class="ok">${c.kind === 'sentence' ? '听写对了' : '拼对了'}${st.mistakes ? `，错了 ${st.mistakes} 次` : ''}</span>`
    : `<span class="bad">答案是 <b>${esc(c.answer.join(' … '))}</b></span>`;
  $('#r-sentence').innerHTML = c.segments.map((s) => (s.blank !== undefined ? `<mark>${esc(s.blank)}</mark>` : esc(s.t))).join('');
  $$('#r-sentence mark').forEach((m) => decrypt(m, m.textContent));
  $('#r-zh').textContent = c.hint || '';
  $('#r-key').textContent = c.kind === 'sentence' ? '' : c.key;
  $('#r-ph').textContent = c.phonetic ? `/${c.phonetic}/` : '';
  if (c.kind === 'sentence') $('#r-sentence').textContent = c.sentence;
  $('#r-tr').textContent = c.translation;
  $('#r-note').textContent = c.source === 'original' ? '原文例句' : c.note;
  $('#reveal').hidden = false;
  $('#give-up-row').hidden = true;
  $('#keys').innerHTML = '<kbd>↵</kbd> 下一个　<kbd>Esc</kbd> 重听';
  for (const id of ['c-text', 'c-zh', 'c-first']) $('#' + id).hidden = true;
  $('#next').disabled = true;
  $('#known-btn').disabled = true;
  $('#next').textContent = '正在保存…';
  try {
    const r = await api('/api/review', { json: { key: c.key, ctxId: c.ctxId, mistakes: st.mistakes, peeked: st.peeked, hinted: st.hinted } });
    if (token !== P.token) return;
    P.results.push({ key: c.key, correct, grade: r.grade, due: r.state.due });
    st.saved = true;
    $('#next').textContent = '下一个';
  } catch (e) {
    if (token !== P.token) return;
    $('#next').textContent = '重试保存';
    toast(`练习记录尚未保存：${e.message}。请重试。`, 5000);
  } finally {
    st.saving = false;
    if (token === P.token) { $('#next').disabled = false; $('#known-btn').disabled = !st.saved; $('#next').focus(); }
  }
  if (token === P.token) Audio_.say(c.sentence);
}

async function nextCard() {
  if (P.busy || !P.cur?.done || P.cur.saving) return;
  if (!P.cur.saved) return finish(P.cur.correct);
  const token = P.token;
  P.busy = true;
  $('#next').disabled = true;
  Audio_.stop();
  try {
    const next = P.i + 1;
    await prepareThrough(next, token);
    if (token !== P.token) return;
    P.i = next;
    if (P.i >= P.cards.length) showDone(); else showCard();
  } catch (e) {
    if (token === P.token) { $('#next').textContent = '重试加载下一批'; toast(e.message); }
  } finally {
    if (token === P.token) { P.busy = false; loading(false); $('#next').disabled = false; }
  }
}

function showDone() {
  $('#practice-run').hidden = true;
  $('#practice-done').hidden = false;
  $('#practice-done h1').textContent = P.skipped.length ? '本轮练习已结束' : '这一轮练完了';
  document.body.classList.remove('is-focus');
  const right = P.results.filter((r) => r.grade === 3).length;
  const wrong = P.results.filter((r) => r.grade === 1);
  $('#done-summary').innerHTML = `<p>本轮计划 <b>${P.total}</b> 个，实际练了 <b>${P.results.length}</b> 个，一次拼对 <b>${right}</b> 个。</p>` +
    (wrong.length ? `<p class="muted">没拼出来的：${wrong.map((r) => esc(r.key)).join('、')}。明天会再出现。</p>` : '') +
    (P.skipped.length ? `<p class="muted">未练到 ${P.skipped.length} 个（词条已移除或没有可用语境句）：${P.skipped.map(esc).join('、')}。可在生词本重新生成语境句后再练。</p>` : '');
}

$('#answer-form').addEventListener('submit', (e) => { e.preventDefault(); checkAnswer(); });
$('#next').addEventListener('click', nextCard);
$('#play').addEventListener('click', () => P.cur && Audio_.playCloze(P.cur.card));
$('#slow').addEventListener('click', () => P.cur && Audio_.playCloze(P.cur.card, true));
$('#give-up').addEventListener('click', giveUp);
$('#quit').addEventListener('click', () => { P.token++; P.busy = false; P.cur = null; loading(false); Audio_.stop(); document.body.classList.remove('is-focus'); $('#practice-run').hidden = true; $('#practice-home').hidden = false; loadHome(); });
$('#back-home').addEventListener('click', () => { $('#practice-done').hidden = true; $('#practice-home').hidden = false; loadHome(); });
for (const [btn, el] of [['show-text', 'c-text'], ['show-zh', 'c-zh'], ['show-first', 'c-first']]) {
  $('#' + btn).addEventListener('click', () => {
    const show = $('#' + el).hidden;
    $('#' + el).hidden = !show;
    $('#' + btn).classList.toggle('on', show);
    $('#' + btn).setAttribute('aria-pressed', String(show));
    // “显示句子 / 读全”模式下句子本来就显示，再打开不算看提示
    const free = btn === 'show-text' && P.mode !== 'cloze';
    if (show && !free && P.cur && !P.cur.done) P.cur.hinted = true;
    $('#answer').focus();
  });
}
$('#known-btn').addEventListener('click', async () => {
  const c = P.cur?.card;
  if (!c || P.busy || !P.cur.saved) return;
  const token = P.token;
  P.busy = true;
  try {
    await api('/api/known', { json: { key: c.key, on: true } });
    if (token !== P.token) return;
    toast(`“${c.key}”标为已会，以后不再出现`);
    P.busy = false;
    await nextCard();
  } catch (e) { toast(e.message); }
  finally { if (token === P.token) P.busy = false; }
});
document.addEventListener('keydown', (e) => {
  if ($('#practice-run').hidden) return;
  if (e.key === 'Escape') { e.preventDefault(); P.cur && Audio_.playCloze(P.cur.card); }
  else if (e.key === 'Enter' && e.shiftKey && P.cur && !P.cur.done && document.activeElement !== $('#answer')) { e.preventDefault(); giveUp(); }
  else if (e.key === 'Enter' && P.cur?.done && document.activeElement !== $('#answer') && !e.target.closest('button')) { e.preventDefault(); nextCard(); }
});

// ---------- 文档 ----------
const D = { doc: null, selected: new Set(), levels: [] };
const fileIcon = (name) => /\.docx$/i.test(name) ? 'docx' : /\.(md|markdown)$/i.test(name) ? 'md' : 'txt';

async function loadDocs() {
  $('#doc-view').hidden = true;
  $('#doc-home').hidden = false;
  const list = await api('/api/docs');
  $('#doc-list').innerHTML = list.length
    ? list.map((d) => `<div class="row doc-row" data-id="${d.id}"><img src="icons/${fileIcon(d.filename)}.svg" alt="" width="24" height="24">
        <span class="t">${esc(d.title)}</span>
        <span class="muted">${new Date(d.created * 1000).toLocaleDateString()} · ${d.chars.toLocaleString()} 字符</span>
        <button class="text-btn open">查看</button><button class="text-btn danger del">删除</button></div>`).join('')
    : '<div class="empty-row">还没有扫描过文档。</div>';
  $$('#doc-list .open').forEach((b) => b.addEventListener('click', () => openDoc(+b.closest('[data-id]').dataset.id)));
  $$('#doc-list .del').forEach((b) => b.addEventListener('click', async () => {
    const id = +b.closest('[data-id]').dataset.id;
    if (!confirm('删除这篇文档的扫描记录？生词本里的词不受影响。')) return;
    await api(`/api/docs/${id}`, { method: 'DELETE' });
    loadDocs();
  }));
}

async function scan(form) {
  loading(true, 'AI 正在逐句分析，长文章要半分钟左右…');
  try {
    const doc = await api('/api/docs', { method: 'POST', body: form });
    $('#paste').value = '';
    showDoc(doc, true);
  } catch (e) {
    toast(e.message, 4000);
  } finally {
    loading(false);
  }
}

$('#pick').addEventListener('click', () => $('#file').click());
$('#file').addEventListener('change', () => {
  const f = $('#file').files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  scan(fd);
  $('#file').value = '';
});
$('#scan').addEventListener('click', () => {
  const text = $('#paste').value.trim();
  if (!text) return toast('先粘贴一段英文，或者选择文件');
  const fd = new FormData();
  fd.append('text', text);
  scan(fd);
});
const drop = $('#drop');
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => {
  e.preventDefault();
  drop.classList.remove('over');
  const f = e.dataTransfer.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  scan(fd);
});

async function openDoc(id) {
  const qs = new URLSearchParams();
  if (D.doc?.id === id) {
    qs.set('hide_level', $('#f-level').value);
    qs.set('hide_rank', $('#f-rank').value);
    if ($('#f-all').checked) qs.set('show_all', '1');
  }
  showDoc(await api(`/api/docs/${id}?${qs}`), D.doc?.id !== id);
}

async function showDoc(doc, fresh) {
  if (fresh) {
    const cfg = await api('/api/settings');
    D.levels = cfg.levels;
    $('#f-level').innerHTML = '<option value="none">不按等级</option>' + cfg.levels.map((l) => `<option value="${l.id}">${l.name}</option>`).join('');
    $('#f-level').value = cfg.filter.hide_level || 'none';
    $('#f-rank').value = String(cfg.filter.hide_rank);
    $('#f-all').checked = false;
  }
  doc.cands = doc.cands.filter((c) => c.kind === 'word'); // 词组在“逐句解析”里按句子勾选
  D.doc = doc;
  D.selected = new Set(doc.cands.filter((c) => !c.inList).map((c) => c.key));
  $('#doc-home').hidden = true;
  $('#doc-view').hidden = false;
  $('#doc-title').textContent = doc.title;

  const src = { ai: '已由 AI 逐句分析', mixed: '前半部分由 AI 逐句分析，其余用词典识别', dict: '没有经过 AI 分析，词组只用了词典匹配' }[doc.meta?.phrases || 'dict'];
  $('#doc-summary').textContent = `共识别 ${doc.totalWords} 个生词，按当前筛选显示 ${doc.cands.length} 个；另有 ${doc.totalPhrases} 个词组，在“逐句解析”里勾选。${src}。`;
  $('#rescan').hidden = doc.meta?.phrases === 'ai';
  if (fresh && doc.meta?.warnings?.length) toast(doc.meta.warnings.join('；'), 6000);
  D.hasAnalysis = doc.hasAnalysis;
  if (fresh) setDocView('cands');
  renderCands();
}

const PTYPE = { 'phrasal verb': '短语动词', collocation: '搭配', idiom: '习语', expression: '固定表达' };

function setDocView(v) {
  $$('.doc-tabs button').forEach((b) => { b.classList.toggle('on', b.dataset.view === v); b.setAttribute('aria-selected', String(b.dataset.view === v)); });
  $('#view-cands').hidden = v !== 'cands';
  $('#view-sents').hidden = v !== 'sents';
  hidePop();
  if (v === 'sents') renderSents();
}
$$('.doc-tabs button').forEach((b) => b.addEventListener('click', () => setDocView(b.dataset.view)));

// 把意群在句子里标出来：词组下划实线，单词下划点线；鼠标停留显示意思
function markUnits(text, units) {
  const spans = units.flatMap((u) => u.spans.map(([a, b]) => ({ a, b, u })))
    .sort((x, y) => x.a - y.a).filter((x, i, arr) => i === 0 || x.a >= arr[i - 1].b);
  let out = '', pos = 0;
  for (const { a, b, u } of spans) {
    const cls = u.type === 'word' ? 'u--word' : 'u--phrase';
    out += esc(text.slice(pos, a)) + '<mark class="u ' + cls + '" title="' + esc(u.form) + '：' + esc(u.meaning) + '">' + esc(text.slice(a, b)) + '</mark>';
    pos = b;
  }
  return out + esc(text.slice(pos));
}

// 逐句朗读的预取队列：打开逐句解析就按顺序把每句语音拉进浏览器缓存（2 路并发）；
// 点了哪句，就把它后面的句子排到队首。服务端扫描时已在后台合成，这里多半直接命中缓存。
const Prefetch = {
  queue: [], busy: 0, done: new Set(), gen: 0,
  start(texts) { this.gen++; this.queue = texts.slice(); this.busy = 0; this.pump(); },
  focus(i, texts) { this.queue = texts.slice(i + 1, i + 6).concat(this.queue.filter((t) => !texts.slice(i + 1, i + 6).includes(t))); this.pump(); },
  pump() {
    const gen = this.gen;
    while (this.busy < 2 && this.queue.length) {
      const t = this.queue.shift();
      if (this.done.has(t)) continue;
      this.busy++;
      fetch(Audio_.url(t)).then((r) => r.blob()).then(() => this.done.add(t)).catch(() => {}).finally(() => {
        if (gen !== this.gen) return;
        this.busy--;
        this.pump();
      });
    }
  },
};

async function renderSents() {
  const box = $('#sents');
  const token = (D.renderToken = (D.renderToken || 0) + 1);
  box.innerHTML = '<div class="empty">正在载入…</div>';
  const sents = await api('/api/docs/' + D.doc.id + '/sentences');
  if (token !== D.renderToken) return; // 期间又渲染了一次，这次的结果作废
  D.sents = sents;
  Prefetch.start(sents.map((s) => s.text));
  D.picked = new Set();
  $('#pick-hint').textContent = D.hasAnalysis
    ? '点下面的意群勾选，或者在句子里用鼠标划选任意文字，都能加入生词本。'
    : '这篇文档没有经过 AI 分析，下面标出的是词典认出的词组，可以勾选；也可以在句子里划选任意文字加入生词本。填好 AI 的 Key 后点“用 AI 重新分析”，能看到翻译和更全的意群。';
  const unitHtml = (u, si, ui) => '<button class="unit ' + (u.type === 'word' ? '' : 'unit--phrase') + '" data-pick="' + si + ':' + ui + '"'
    + (u.inList ? ' disabled' : ' aria-pressed="false"') + '><b>' + esc(u.form) + '</b><span>'
    + esc(u.meaning) + (u.type !== 'word' ? ' · ' + (PTYPE[u.type] || '词组') : '') + '</span></button>';
  box.innerHTML = '<div class="list">' + sents.map((s, i) => '<div class="sent" data-sid="' + i + '">'
    + '<div class="sent__en"><p>' + markUnits(s.text, s.units) + '</p>'
    + '<button class="text-btn" data-whole="' + i + '"' + (s.inList ? ' disabled>整句已加入' : '>加入整句') + '</button>'
    + '<button class="icon-btn" data-say="' + i + '" aria-label="朗读这一句"><i data-i="volume-2"></i></button></div>'
    + (s.zh ? '<div class="sent__zh">' + esc(s.zh) + '</div>' : '')
    + (s.units.length ? '<div class="sent__units">' + s.units.map((u, ui) => unitHtml(u, i, ui)).join('') + '</div>' : '')
    + '</div>').join('') + '</div>';
  icons(box);
  $$('[data-whole]', box).forEach((b) => b.addEventListener('click', async () => {
    const s = sents[+b.dataset.whole];
    try {
      const r = await addPicks([{ sentence: s.text, whole: true, meaning: s.zh }]);
      if (r.keys.length) { b.disabled = true; b.textContent = '整句已加入'; s.inList = true; }
    } catch (e) { toast(e.message); }
  }));
  $$('[data-say]', box).forEach((b) => b.addEventListener('click', async () => {
    const i = +b.dataset.say;
    Prefetch.focus(i, sents.map((s) => s.text));
    b.classList.add('is-loading');
    await Audio_.say(sents[i].text, { onStart: () => b.classList.remove('is-loading') });
    b.classList.remove('is-loading');
  }));
  $$('[data-pick]', box).forEach((b) => b.addEventListener('click', () => {
    const on = !D.picked.has(b.dataset.pick);
    on ? D.picked.add(b.dataset.pick) : D.picked.delete(b.dataset.pick);
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', String(on));
    updatePickBar();
  }));
  updatePickBar();
}

function updatePickBar() {
  $('#pick-bar').hidden = !D.sents?.some((s) => s.units.length);
  $('#pick-count').textContent = D.picked.size ? '已选 ' + D.picked.size + ' 个意群' : '还没有勾选意群';
  $('#pick-add').disabled = !D.picked.size;
}

async function addPicks(picks) {
  const r = await api('/api/items/pick', { json: { doc_id: D.doc.id, picks } });
  toast((r.added ? '加入了 ' + r.added + ' 个' : '这些已经在生词本里了') + (r.warnings.length ? '；' + r.warnings.join('；') : ''), r.warnings.length ? 5000 : 2600);
  return r;
}

function markAdded(pickKeys) {
  for (const k of pickKeys) {
    const b = document.querySelector('[data-pick="' + k + '"]');
    if (b) { b.classList.remove('on'); b.disabled = true; b.removeAttribute('aria-pressed'); }
    const [si, ui] = k.split(':').map(Number);
    if (D.sents[si]?.units[ui]) D.sents[si].units[ui].inList = true;
    D.picked.delete(k);
  }
  updatePickBar();
}

$('#pick-add').addEventListener('click', async () => {
  const picks = [...D.picked].map((k) => {
    const [si, ui] = k.split(':').map(Number);
    const s = D.sents[si], u = s.units[ui];
    return { sentence: s.text, pieces: u.pieces, form: u.form, type: u.type, meaning: u.meaning };
  });
  try {
    await addPicks(picks);
    markAdded([...D.picked]);
  } catch (e) { toast(e.message); }
});

// 划选任意文字：松开鼠标后在选区上方出现“加入生词本”
const pop = $('#pick-pop');
let popPick = null;
function hidePop() { pop.hidden = true; popPick = null; }
document.addEventListener('mouseup', (e) => {
  if (e.target.closest('#pick-pop')) return;
  setTimeout(() => {
    const sel = getSelection();
    const text = sel && !sel.isCollapsed ? sel.toString().replace(/\s+/g, ' ').trim() : '';
    const p = sel && sel.anchorNode ? sel.anchorNode.parentElement?.closest('.sent__en p') : null;
    const sameSentence = p && sel.focusNode && p.contains(sel.focusNode);
    if (!text || !sameSentence || text.length > 80 || !/[A-Za-z]/.test(text)) return hidePop();
    const sid = +p.closest('.sent').dataset.sid;
    popPick = { sentence: D.sents[sid].text, text };
    $('#pick-pop-text').textContent = '加入生词本：' + text;
    const r = sel.getRangeAt(0).getBoundingClientRect();
    pop.style.left = (r.left + r.width / 2 + scrollX) + 'px';
    pop.style.top = (r.top + scrollY - 8) + 'px';
    pop.hidden = false;
  }, 0);
});
document.addEventListener('scroll', hidePop, { passive: true });
pop.addEventListener('click', async () => {
  if (!popPick) return;
  const pick = popPick;
  hidePop();
  getSelection().removeAllRanges();
  loading(true, '正在加入，并查它在这句里的意思…');
  try {
    const r = await addPicks([pick]);
    // 划选的正好是某个意群：把它也标成已加入
    const si = D.sents.findIndex((s) => s.text === pick.sentence);
    if (si >= 0) markAdded(D.sents[si].units.map((u, ui) => (r.keys.includes(u.form) ? si + ':' + ui : null)).filter(Boolean));
  } catch (e) { toast(e.message); } finally { loading(false); }
});

$('#rescan').addEventListener('click', async () => {
  loading(true, 'AI 正在逐句分析，长文章要半分钟左右…');
  try {
    const doc = await api(`/api/docs/${D.doc.id}/phrases`, { method: 'POST' });
    D.doc = null;
    await showDoc(doc, true);
    if (!doc.meta?.warnings?.length) toast(`分析完成，AI 认出 ${doc.meta.aiPhrases} 个词组`);
  } catch (e) {
    toast(e.message);
  } finally {
    loading(false);
  }
});

function highlight(sentence, surface) {
  const i = sentence.indexOf(surface);
  if (i < 0) return esc(sentence);
  return esc(sentence.slice(0, i)) + '<mark>' + esc(surface) + '</mark>' + esc(sentence.slice(i + surface.length));
}

function renderCands() {
  const keepY = scrollY;
  requestAnimationFrame(() => scrollTo(0, keepY));
  const cands = D.doc.cands;
  const block = (title, list) => !list.length ? '' : `<div class="section-label">${title} ${list.length}</div><div class="list">` + list.map((c) => `
    <div class="cand ${c.inList ? 'in' : ''}" data-key="${esc(c.key)}">
      <input type="checkbox" aria-label="选择 ${esc(c.key)}" ${D.selected.has(c.key) ? 'checked' : ''} ${c.inList ? 'disabled' : ''}>
      <div class="body">
        <div class="head"><b>${esc(c.key)}</b>${c.phonetic ? `<span class="muted mono">/${esc(c.phonetic)}/</span>` : ''}
          ${c.ptype ? `<span class="chip chip--plain">${PTYPE[c.ptype] || '词组'}</span>` : ''}
          ${c.levelName ? `<span class="chip chip--plain">${esc(c.levelName)}</span>` : ''}
          ${c.count > 1 ? `<span class="chip chip--plain">出现 ${c.count} 次</span>` : ''}
          ${c.inList ? '<span class="chip chip--accent">已在生词本</span>' : ''}</div>
        <div class="tr">${c.ctx ? `<span class="ctx">本文：${esc(c.ctx)}</span> · ` : ''}${esc(c.translation)}</div>
        <div class="ex">${highlight(c.examples[0]?.sentence || '', c.examples[0]?.surface || '')}</div>
      </div>
      <div class="side-btns"><button class="icon-btn say" aria-label="朗读 ${esc(c.key)}"><i data-i="volume-2"></i></button>${c.inList ? '' : '<button class="text-btn known">会了</button>'}</div>
    </div>`).join('') + '</div>';
  $('#cands').innerHTML = block('生词', cands)
    || '<div class="empty">按当前筛选没有生词了。可以降低隐藏等级，或者勾选“显示全部”。</div>';
  icons($('#cands'));
  $$('#cands .cand').forEach((row) => {
    const key = row.dataset.key;
    row.querySelector('input').addEventListener('change', (e) => { e.target.checked ? D.selected.add(key) : D.selected.delete(key); updateSel(); });
    row.querySelector('.say').addEventListener('click', () => Audio_.say(key.replace(/\bsb\b/g, 'somebody').replace(/\bsth\b/g, 'something')));
    row.querySelector('.known')?.addEventListener('click', async () => {
      await api('/api/known', { json: { key, on: true } });
      D.doc.cands = D.doc.cands.filter((c) => c.key !== key);
      D.selected.delete(key);
      renderCands();
      toast(`“${key}”以后不再出现`);
    });
  });
  updateSel();
}

function updateSel() {
  const avail = D.doc.cands.filter((c) => !c.inList).length;
  $('#sel-count').textContent = `已选 ${D.selected.size} / ${avail}`;
  $('#sel-all').checked = D.selected.size && D.selected.size === avail;
  $('#add-items').disabled = !D.selected.size;
}

$('#sel-all').addEventListener('change', (e) => {
  D.selected = e.target.checked ? new Set(D.doc.cands.filter((c) => !c.inList).map((c) => c.key)) : new Set();
  renderCands();
});
for (const id of ['#f-level', '#f-rank', '#f-all']) $(id).addEventListener('change', () => openDoc(D.doc.id));
$('#doc-back').addEventListener('click', () => { D.doc = null; loadDocs(); });
$('#add-items').addEventListener('click', async () => {
  const keys = [...D.selected];
  const r = await api('/api/items', { json: { doc_id: D.doc.id, keys } });
  toast(`加入了 ${r.added} 个`);
  if (confirm(`已加入 ${r.added} 个到生词本。现在就开始练这些词吗？`)) return startSession('all', keys);
  openDoc(D.doc.id);
});

// ---------- 生词本 ----------
let WORDS = [];
let WORD_GROUPS = [];
async function loadWords() {
  WORDS = await api('/api/items');
  $('#words-sub').textContent = WORDS.length ? `${WORDS.length} 个` : '';
  renderWords();
  const known = await api('/api/known');
  $('#known-count').textContent = known.length ? known.length : '';
  $('#known-list').innerHTML = known.length
    ? known.map((k) => `<span class="chip">${esc(k.key)}<button data-k="${esc(k.key)}" aria-label="把 ${esc(k.key)} 移出已会"><i data-i="x"></i></button></span>`).join('')
    : '<span class="muted">扫描或练习时点“会了”的词会放在这里，以后不再出现。</span>';
  icons($('#known-list'));
  $$('#known-list button').forEach((b) => b.addEventListener('click', async () => {
    await api('/api/known', { json: { key: b.dataset.k, on: false } });
    loadWords();
  }));
}

function dueText(it) {
  if (!it.state) return ['还没练', false];
  const days = Math.round((new Date(it.state.due) - new Date(new Date().toISOString().slice(0, 10))) / 864e5);
  return days <= 0 ? ['今天复习', true] : [`${days} 天后复习`, false];
}

const KIND_NAME = { word: '单词', phrase: '词组', sentence: '句子' };
function dayLabel(ts) {
  const d = new Date(ts * 1000), today = new Date();
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(today) - start(d)) / 864e5);
  if (diff === 0) return '今天';
  if (diff === 1) return '昨天';
  return (d.getFullYear() === today.getFullYear() ? '' : d.getFullYear() + '年') + (d.getMonth() + 1) + '月' + d.getDate() + '日';
}
function groupOf(w, mode) {
  if (mode === 'time') return dayLabel(w.added);
  if (mode === 'doc') return w.docTitle || '没有来源文档';
  if (mode === 'kind') return KIND_NAME[w.kind] || '单词';
  return '';
}
try { $('#w-group').value = localStorage.getItem('chuanting.wgroup') || 'time'; } catch { /* 无痕模式 */ }

function renderWords() {
  const q = $('#w-search').value.trim().toLowerCase();
  const mode = $('#w-group').value;
  const list = WORDS.filter((w) => !q || w.key.toLowerCase().includes(q) || w.translation.includes(q));
  const rowHtml = (w) => {
    const [txt, due] = dueText(w);
    return `<div class="w-row" data-key="${esc(w.key)}" tabindex="0" role="button" aria-expanded="false"><span class="k">${esc(w.key)}</span>`
      + (w.kind !== 'word' && mode !== 'kind' ? `<span class="kind-tag">${KIND_NAME[w.kind] || ''}</span>` : '')
      + `<span class="tr">${esc(w.translation)}</span><span class="due ${due ? 'is-due' : ''}">${w.contexts ? `${w.contexts} 句 · ` : ''}${txt}</span></div>`;
  };
  const groups = new Map();
  for (const w of list) {
    const g = groupOf(w, mode);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(w);
  }
  WORD_GROUPS = [...groups.entries()];
  $('#word-list').innerHTML = list.length
    ? WORD_GROUPS.map(([g, ws], gi) => (mode === 'none' ? '' : `<div class="w-group"><b>${esc(g)}</b><span class="muted">${ws.length} 个</span>`
      + `<button class="text-btn" data-drill-group="${gi}">练这组</button></div>`) + ws.map(rowHtml).join('')).join('')
    : `<div class="empty-row">${WORDS.length ? '没有匹配的词。' : '生词本是空的。去“文档”扫描一篇文章，把生词勾选进来。'}</div>`;
  $$('[data-drill-group]').forEach((b) => b.addEventListener('click', () => {
    const keys = WORD_GROUPS[+b.dataset.drillGroup][1].map((w) => w.key);
    startSession('all', keys);
  }));
  $$('#word-list .w-row').forEach((row) => {
    row.addEventListener('click', () => toggleWord(row));
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleWord(row); } });
  });
}

async function toggleWord(row) {
  const next = row.nextElementSibling;
  if (next?.classList.contains('w-detail')) { row.setAttribute('aria-expanded', 'false'); return next.remove(); }
  const it = await api(`/api/item?key=${encodeURIComponent(row.dataset.key)}`);
  const div = document.createElement('div');
  div.className = 'w-detail';
  const ctxs = it.contextList.filter((c) => !it.examples.some((e) => e.sentence === c.sentence));
  div.innerHTML = `
    ${it.examples.map((e) => `<div class="ctx">${highlight(e.sentence, e.surface)}<small>原文</small></div>`).join('')}
    ${ctxs.map((c) => `<div class="ctx">${esc(c.sentence)}<small>AI${c.hint ? ' · ' + esc(c.hint) : ''}</small></div>`).join('')}
    ${it.contextList.length ? '' : '<div class="muted">还没有语境句，第一次练习时会自动生成。</div>'}
    <div class="btn-row btn-row--end"><button class="text-btn regen">重新生成语境句</button><button class="text-btn danger del">从生词本删除</button><button class="btn btn--primary btn--sm drill-one">练这个词</button></div>`;
  row.after(div);
  row.setAttribute('aria-expanded', 'true');
  div.querySelector('.drill-one').addEventListener('click', () => startSession('all', [it.key]));
  div.querySelector('.del').addEventListener('click', async () => {
    await api(`/api/item?key=${encodeURIComponent(it.key)}`, { method: 'DELETE' });
    loadWords();
  });
  div.querySelector('.regen').addEventListener('click', async () => {
    loading(true, 'AI 正在重新编句子…');
    try {
      const r = await api('/api/item/regenerate', { json: { key: it.key } });
      if (r.warnings.length) toast(r.warnings.join('；'), 5000);
      div.remove();
      await loadWords();
      const again = $(`#word-list .w-row[data-key="${CSS.escape(it.key)}"]`);
      if (again) toggleWord(again);
    } finally {
      loading(false);
    }
  });
}

$('#w-search').addEventListener('input', renderWords);
$('#w-group').addEventListener('change', () => {
  try { localStorage.setItem('chuanting.wgroup', $('#w-group').value); } catch { /* 无痕模式 */ }
  renderWords();
});
$('#gen-all').addEventListener('click', async () => {
  loading(true, 'AI 正在为生词编语境句…');
  try {
    const r = await api('/api/contexts/generate', { json: {} });
    toast(r.warnings.length ? r.warnings.join('；') : '都生成好了', 5000);
    loadWords();
  } catch (e) {
    toast(e.message);
  } finally {
    loading(false);
  }
});

// ---------- 设置 ----------
let CFG = null;
const FIELDS = {
  's-ds-key': ['ai', 'deepseek', 'key'], 's-ds-model': ['ai', 'deepseek', 'model'], 's-ds-base': ['ai', 'deepseek', 'base'],
  's-an-key': ['ai', 'anthropic', 'key'], 's-an-model': ['ai', 'anthropic', 'model'],
  's-oa-key': ['ai', 'openai', 'key'], 's-oa-model': ['ai', 'openai', 'model'], 's-oa-base': ['ai', 'openai', 'base'],
  's-per': ['ai', 'per_item'], 's-voice': ['tts', 'voice'], 's-rate': ['tts', 'rate'], 's-pause': ['study', 'pause_ms'],
  's-level': ['filter', 'hide_level'], 's-rank': ['filter', 'hide_rank'], 's-daily': ['study', 'daily_new'],
  's-readmode': ['study', 'read_mode'],
  's-session': ['study', 'session_size'],
  's-phrases': ['extract', 'phrases'],
};
const NUMERIC = new Set(['s-per', 's-pause', 's-rank', 's-daily', 's-session']);
const getPath = (o, p) => p.reduce((x, k) => x?.[k], o);
const patchOf = (p, v) => p.reduceRight((acc, k) => ({ [k]: acc }), v);

async function loadSettings() {
  CFG = await api('/api/settings');
  $('#s-level').innerHTML = '<option value="none">不按等级</option>' + CFG.levels.map((l) => `<option value="${l.id}">${l.name}及更简单的词</option>`).join('');
  for (const [id, p] of Object.entries(FIELDS)) $('#' + id).value = String(getPath(CFG, p) ?? '');
  setProvider(CFG.ai.provider);
}

function setProvider(p) {
  $$('#s-provider button').forEach((b) => { b.classList.toggle('on', b.dataset.v === p); b.setAttribute('aria-checked', String(b.dataset.v === p)); });
  $$('.prov').forEach((el) => el.classList.toggle('on', el.dataset.p === p));
}

async function save(patch) {
  try {
    CFG = { ...(await api('/api/settings', { json: patch })), levels: CFG.levels };
    toast('已保存', 1200);
  } catch (e) {
    toast(e.message);
  }
}

$$('#s-provider button').forEach((b) => b.addEventListener('click', () => { setProvider(b.dataset.v); save({ ai: { provider: b.dataset.v } }); }));
for (const [id, p] of Object.entries(FIELDS)) {
  $('#' + id).addEventListener('change', (e) => {
    const v = NUMERIC.has(id) ? Number(e.target.value) : e.target.value.trim();
    if (id.endsWith('-key') && String(v).startsWith('••••')) return;
    save(patchOf(p, v));
  });
}
$('#s-test').addEventListener('click', async () => {
  $('#s-test-out').textContent = '正在测试…';
  const r = await api('/api/settings/test', { json: {} });
  $('#s-test-out').textContent = r.ok ? `可以用。示例：${r.sample}` : `不能用：${r.error}`;
});

$('#quit-app').addEventListener('click', async () => {
  if (!confirm('关闭川听？正在练习的进度已经自动保存。下次从程序坞或 Spotlight 打开“川听”。')) return;
  try {
    await fetch('/api/quit', { method: 'POST', headers: { 'X-ChuanTing': '1' } });
  } catch { /* 服务已经停了 */ }
  Audio_.stop();
  document.body.innerHTML = '<main style="margin:auto;text-align:center;padding:64px 24px"><h1>川听已关闭</h1>'
    + '<p class="muted" style="margin-top:12px">可以关掉这个网页了。下次从程序坞或 Spotlight 打开“川听”。</p></main>';
});

// ---------- 启动 ----------
icons();
showTab(location.hash.slice(1) in pages ? location.hash.slice(1) : 'practice');
