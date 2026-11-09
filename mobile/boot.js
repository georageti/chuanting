// 川听手机网页版的启动脚本：载入精简词典、装好浏览器里的本地接口、处理朗读，然后通知界面（web/app.js）可以开始了。
import * as lx from './lib/lexicon.js';
import { indexedDBStore } from './lib/db.js';
import { createApi } from './lib/local-api.js';

const say = (on, text) => { try { window.loading?.(on, text); } catch { /* 界面还没准备好 */ } };

async function gunzipJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`词典下载失败（${r.status}）`);
  const stream = r.body.pipeThrough(new DecompressionStream('gzip'));
  return JSON.parse(await new Response(stream).text());
}

// ---------- 朗读 ----------
// 在线：百度翻译的发音（国内能直连，能读整句）。页面设了 no-referrer，请求不带来源网址，否则它会拒绝。
// 离线或出错：手机自带的朗读（speechSynthesis）。
let tts = { engine: 'baidu-us', rate: '-5%' };
const SPD = { '-20%': 3, '-10%': 4, '-5%': 4, '+0%': 5, '+10%': 6 };
const audio = new Audio();
audio.preload = 'auto';
const preloaded = [];

function ttsUrl(text, { slow = false } = {}) {
  if (tts.engine === 'device') return `speech:${slow ? 'slow:' : ''}${encodeURIComponent(text)}`;
  const lan = tts.engine === 'baidu-uk' ? 'uk' : 'en';
  return `https://fanyi.baidu.com/gettts?lan=${lan}&text=${encodeURIComponent(text)}&spd=${slow ? 2 : SPD[tts.rate] ?? 4}&source=web`;
}

function pickVoice(lang) {
  const voices = speechSynthesis.getVoices();
  return voices.find((v) => v.lang === lang && /Samantha|Daniel|Karen|Google|Siri/i.test(v.name))
    || voices.find((v) => v.lang === lang) || voices.find((v) => v.lang.startsWith('en'));
}

function speakText(text, slow, cancelled, onStart) {
  if (!('speechSynthesis' in window)) return Promise.reject(new Error('这台设备不支持朗读'));
  return new Promise((resolve) => {
    if (cancelled()) return resolve();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = tts.engine === 'baidu-uk' ? 'en-GB' : 'en-US';
    const v = pickVoice(u.lang);
    if (v) u.voice = v;
    u.rate = slow ? 0.7 : 0.95;
    u.onstart = () => onStart?.();
    u.onend = u.onerror = () => resolve();
    speechSynthesis.speak(u);
  });
}

function parseSpeech(url) {
  let rest = url.slice('speech:'.length);
  const slow = rest.startsWith('slow:');
  if (slow) rest = rest.slice(5);
  return [decodeURIComponent(rest), slow];
}

const hooks = {
  ttsUrl,
  sharedAudio(url) { audio.pause(); audio.src = url; audio.playbackRate = 1; return audio; },
  speak(url, cancelled, onStart) { const [text, slow] = parseSpeech(url); return speakText(text, slow, cancelled, onStart); },
  stopSpeech() { if ('speechSynthesis' in window) speechSynthesis.cancel(); },
  fallbackSpeech(url, cancelled, onStart) {
    const q = new URL(url).searchParams;
    return speakText(q.get('text') || '', q.get('spd') === '2', cancelled, onStart);
  },
  prefetchAudio(url) {
    if (url.startsWith('speech:')) return Promise.resolve();
    // 在线朗读不允许脚本读取内容，只能让浏览器预先下载到缓存
    const a = new Audio();
    a.preload = 'auto';
    a.src = url;
    preloaded.push(a);
    if (preloaded.length > 24) preloaded.shift();
    return Promise.resolve();
  },
  unlockAudio() {
    // 在第一次点按里用一段静音“解锁”共用播放器和手机朗读
    audio.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=';
    audio.play().catch(() => {});
    if ('speechSynthesis' in window) { const u = new SpeechSynthesisUtterance(''); u.volume = 0; speechSynthesis.speak(u); }
  },
};

// ---------- 启动 ----------
async function boot() {
  say(true, '正在载入词典（第一次打开要下载约 3MB，之后离线也能用）…');
  const [words, forms, phrases] = await Promise.all(['words', 'forms', 'phrases'].map((n) => gunzipJSON(`dict/${n}.json.gz`)));
  lx.init({ words, forms, phrases });
  const local = createApi(indexedDBStore(), {
    onScanProgress: (done, total) => say(true, `AI 正在逐句分析（${done}/${total} 批）…`),
  });
  const refreshTTS = async () => { const c = await local.loadConfig(); tts = { engine: c.tts.mobile_engine || 'baidu-us', rate: c.tts.rate }; };
  await refreshTTS();
  window.CHUANTING = {
    ...hooks,
    async api(path, opts) {
      try {
        const out = await local.api(path, opts);
        if (path.startsWith('/api/settings') || path.startsWith('/api/import')) await refreshTTS();
        return out;
      } catch (e) {
        throw e instanceof Error ? e : new Error(String(e));
      }
    },
  };
  // 让浏览器把数据当作“持久”数据，减少被系统清理的可能
  navigator.storage?.persist?.().catch(() => {});
  say(false);
  window.__ctReady();
}

boot().catch((e) => {
  say(false);
  window.__ctFail?.(`川听没能启动：${String(e.message || e)}。`);
});

if ('serviceWorker' in navigator && location.protocol === 'https:') {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}
