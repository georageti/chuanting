// 复习排程：FSRS-5。对应 Python 版 chuanting/srs.py，状态格式完全一样（方便和 Mac 版互相导入）。
const W = [0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046, 1.54575, 0.1192,
  1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315, 2.9898, 0.51655, 0.6621];
const DECAY = -0.5;
const FACTOR = 19 / 81;
const RETENTION = 0.9;
const MAX_DAYS = 3650;
export const MASTERED_DAYS = 60;
export const AGAIN = 1, HARD = 2, GOOD = 3;

const pad = (n) => String(n).padStart(2, '0');
export function today(d = new Date()) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
export function addDays(day, n) {
  const [y, m, dd] = day.split('-').map(Number);
  return today(new Date(y, m - 1, dd + n));
}
export function daysBetween(a, b) {
  const t = (s) => { const [y, m, d] = s.split('-').map(Number); return Date.UTC(y, m - 1, d); };
  return Math.round((t(b) - t(a)) / 864e5);
}

const clampD = (d) => Math.min(10, Math.max(1, d));
const initS = (g) => Math.max(W[g - 1], 0.1);
const initD = (g) => clampD(W[4] - Math.exp(W[5] * (g - 1)) + 1);
const recall = (t, s) => (1 + FACTOR * t / s) ** DECAY;
const interval = (s) => Math.min(MAX_DAYS, Math.max(1, Math.round((s / FACTOR) * (RETENTION ** (1 / DECAY) - 1))));
function nextD(d, g) {
  const delta = -W[6] * (g - 3);
  return clampD(W[7] * initD(4) + (1 - W[7]) * (d + delta * (10 - d) / 9));
}
function recallS(d, s, r, g) {
  const hard = g === HARD ? W[15] : 1;
  return s * (1 + Math.exp(W[8]) * (11 - d) * s ** (-W[9]) * (Math.exp((1 - r) * W[10]) - 1) * hard);
}
function forgetS(d, s, r) {
  const long = W[11] * d ** (-W[12]) * ((s + 1) ** W[13] - 1) * Math.exp((1 - r) * W[14]);
  return Math.min(long, s / Math.exp(W[17] * W[18]), s);
}

export function grade(mistakes, peeked, hinted = false) {
  if (mistakes >= 3 || peeked) return AGAIN;
  if (mistakes > 0 || hinted) return HARD;
  return GOOD;
}

/** 第一次学：刚听过就拼，最多按“模糊”起步 */
export function learn(g, day = today()) {
  g = Math.min(g, HARD);
  const s = initS(g);
  return { s, df: initD(g), last: day, due: addDays(day, interval(s)), reps: 1, lapses: g === AGAIN ? 1 : 0, learned: day };
}

export function review(state, g, day = today()) {
  const st = { ...state };
  if (st.last === day) return st; // 同一天第二遍不改排程
  const elapsed = Math.max(0, daysBetween(st.last || st.learned || day, day));
  const r = recall(elapsed, st.s);
  st.reps = (st.reps || 0) + 1;
  if (g === AGAIN) { st.lapses = (st.lapses || 0) + 1; st.s = forgetS(st.df, st.s, r); }
  else st.s = recallS(st.df, st.s, r, g);
  st.df = nextD(st.df, g);
  st.last = day;
  st.due = addDays(day, interval(st.s));
  return st;
}
