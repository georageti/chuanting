// 离线缓存：网站自己的文件（页面、样式、脚本、词典、字体、图标）缓存在手机上，断网也能打开和练习。
// AI 接口和在线朗读不经过这里。VERSION 由 scripts/build_site.py 在构建时换成文件内容的指纹，网站更新后自动换新缓存。
const VERSION = '__VERSION__';
const CACHE = `chuanting-${VERSION}`;
const CORE = ['./', 'index.html', 'app.css', 'app.js', 'boot.js', 'manifest.webmanifest',
  'dict/words.json.gz', 'dict/forms.json.gz', 'dict/phrases.json.gz'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith('chuanting-') && k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  // 页面本身：先上网取新的，取不到再用缓存；其余文件：先用缓存
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).then((r) => { caches.open(CACHE).then((c) => c.put('index.html', r.clone())); return r; })
      .catch(() => caches.match('index.html')));
    return;
  }
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((r) => {
    if (r.ok) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
    return r;
  })));
});
