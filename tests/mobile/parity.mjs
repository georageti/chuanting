// 手机网页版 extract.js 和 Python 版 extract.py 对拍：同样的文本，候选词和例句位置要一致。
// 用法：node tests/mobile/parity.mjs   （会调用 .venv/bin/python 跑 Python 版）
// 手机版用精简词典，只允许一种差别：Python 版认出、但精简词典里没收录的生僻单词。
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import * as lx from '../../mobile/lib/lexicon.js';
import { extract, filterCandidates } from '../../mobile/lib/extract.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const load = (n) => JSON.parse(gunzipSync(readFileSync(path.join(ROOT, 'mobile/dict', n))).toString('utf8'));
lx.init({ words: load('words.json.gz'), forms: load('forms.json.gz'), phrases: load('phrases.json.gz') });

const TEXTS = [
  readFileSync(path.join(ROOT, 'tests/sample.txt'), 'utf8'),
  `Ministers have been sitting on the fence for months, but the budget finally forced their hand.
Critics say the reforms will cut through red tape, yet small firms fear they will bear the brunt of the new levy.
The central bank kept rates on hold, signalling that it would not bow to political pressure.
Officials played down the risk of a downturn, insisting that the economy had turned a corner.`,
  `Housing costs have spiralled out of control in the capital. Young renters are being priced out of neighbourhoods they grew up in.
The mayor has vowed to crack down on speculative buyers. Developers, however, argue that red tape is the real culprit.
Dr. Smith said the U.S. economy was "running out of steam", and she called it off at once. They took their coats off and sat down.`,
];

const py = JSON.parse(execFileSync(path.join(ROOT, '.venv/bin/python'), ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(ROOT)})
from chuanting import extract
texts = json.loads(sys.stdin.read())
out = []
for t in texts:
    cands = [c.to_dict() for c in extract.extract(t)]
    out.append({"all": cands, "filtered": [c["key"] for c in extract.filter_candidates(cands)]})
print(json.dumps(out))
`], { input: JSON.stringify(TEXTS), maxBuffer: 1 << 26 }).toString());

let failed = 0;
TEXTS.forEach((text, i) => {
  const js = extract(text);
  const jsMap = new Map(js.map((c) => [c.key, c]));
  const pyMap = new Map(py[i].all.map((c) => [c.key, c]));
  const onlyPy = [...pyMap.keys()].filter((k) => !jsMap.has(k));
  const onlyJs = [...jsMap.keys()].filter((k) => !pyMap.has(k));
  // 允许：精简词典里没有的生僻单词
  const badOnlyPy = onlyPy.filter((k) => pyMap.get(k).kind !== 'word' || lx.word(k));
  const surfaceDiff = [...jsMap.keys()].filter((k) => pyMap.has(k)
    && JSON.stringify(jsMap.get(k).examples.map((e) => e.surface)) !== JSON.stringify(pyMap.get(k).examples.map((e) => e.surface)));
  const jsFiltered = filterCandidates(js).map((c) => c.key);
  const filtDiff = py[i].filtered.filter((k) => !jsFiltered.includes(k) && (pyMap.get(k).kind !== 'word' || lx.word(k)));
  const ok = !badOnlyPy.length && !onlyJs.length && !surfaceDiff.length && !filtDiff.length;
  if (!ok) failed++;
  console.log(`${ok ? '✓' : '✗'} 文本 ${i + 1}：Python ${pyMap.size} 个，JS ${jsMap.size} 个`
    + (onlyPy.length ? `；精简词典没收的生僻词 ${onlyPy.length - badOnlyPy.length} 个` : ''));
  if (badOnlyPy.length) console.log('   只在 Python：', badOnlyPy.join(', '));
  if (onlyJs.length) console.log('   只在 JS：', onlyJs.join(', '));
  if (surfaceDiff.length) console.log('   例句位置不同：', surfaceDiff.join(', '));
  if (filtDiff.length) console.log('   筛选结果不同：', filtDiff.join(', '));
});
console.log(failed ? `\n${failed} 段不一致` : '\n全部一致');
process.exit(failed ? 1 : 0);
