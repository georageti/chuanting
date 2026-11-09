// 本机存储：浏览器里用 IndexedDB；Node 测试里用内存。所有数据只在这台手机上。
// 表：docs（文档，自增 id）、items（生词本，key）、known（已会，key）、log（练习记录，自增）、kv（设置等）
const STORES = { docs: { keyPath: 'id', autoIncrement: true }, items: { keyPath: 'key' }, known: { keyPath: 'key' },
  log: { keyPath: 'id', autoIncrement: true }, kv: { keyPath: 'k' } };

export function memoryDB() {
  const data = Object.fromEntries(Object.keys(STORES).map((s) => [s, new Map()]));
  const seq = Object.fromEntries(Object.keys(STORES).map((s) => [s, 0]));
  const clone = (v) => (v === undefined ? v : structuredClone(v));
  return {
    async get(s, k) { return clone(data[s].get(k)); },
    async all(s) { return [...data[s].values()].map(clone); },
    async put(s, v) {
      v = clone(v);
      const cfg = STORES[s];
      if (cfg.autoIncrement && v[cfg.keyPath] == null) v[cfg.keyPath] = ++seq[s];
      else if (cfg.autoIncrement) seq[s] = Math.max(seq[s], v[cfg.keyPath]);
      data[s].set(v[cfg.keyPath], v);
      return v[cfg.keyPath];
    },
    async del(s, k) { data[s].delete(k); },
    async clear(s) { data[s].clear(); },
  };
}

export function indexedDBStore(name = 'chuanting') {
  const opening = new Promise((resolve, reject) => {
    const req = indexedDB.open(name, 1);
    req.onupgradeneeded = () => {
      for (const [s, cfg] of Object.entries(STORES)) if (!req.result.objectStoreNames.contains(s)) req.result.createObjectStore(s, cfg);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  const run = async (s, mode, fn) => {
    const db = await opening;
    return new Promise((resolve, reject) => {
      const tx = db.transaction(s, mode);
      const r = fn(tx.objectStore(s));
      tx.oncomplete = () => resolve(r && 'result' in r ? r.result : undefined);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error('存储失败（手机存储空间可能不够了）'));
    });
  };
  return {
    get: (s, k) => run(s, 'readonly', (st) => st.get(k)),
    all: (s) => run(s, 'readonly', (st) => st.getAll()),
    put: (s, v) => run(s, 'readwrite', (st) => st.put(v)),
    del: (s, k) => run(s, 'readwrite', (st) => st.delete(k)),
    clear: (s) => run(s, 'readwrite', (st) => st.clear()),
  };
}
