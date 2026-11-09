// 把上传的文档变成纯文本：Word（.docx）、TXT、Markdown。对应 Python 版 chuanting/docs.py。
// .docx 是 zip 包：自己解析 zip 目录，用浏览器自带的 DecompressionStream 解压 word/document.xml，不引入第三方库。

export class UnsupportedFile extends Error {}

export function markdownToText(md) {
  return md
    .replace(/```[\s\S]*?```/g, '\n')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/<[^>]+>/g, '')
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s*([-*+]|\d+[.)])\s+/gm, '')
    .replace(/(\*\*|__|\*|_|`|~~)(?=\S)(.+?)(?<=\S)\1/g, '$2')
    .replace(/^\s*([-*_]\s*){3,}$/gm, '')
    .replace(/^\|.*\|$/gm, (row) => row.replace(/^\||\|$/g, '').split('|').map((c) => c.trim()).join(' ') + '.');
}

export function decodeText(bytes) {
  const u8 = new Uint8Array(bytes);
  if (u8[0] === 0xff && u8[1] === 0xfe) return new TextDecoder('utf-16le').decode(u8);
  if (u8[0] === 0xfe && u8[1] === 0xff) return new TextDecoder('utf-16be').decode(u8);
  try { return new TextDecoder('utf-8', { fatal: true }).decode(u8).replace(/^﻿/, ''); } catch { /* 不是 UTF-8 */ }
  try { return new TextDecoder('gb18030').decode(u8); } catch { return new TextDecoder('latin1').decode(u8); }
}

async function inflateRaw(data) {
  const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/** 从 zip 里取一个文件（读中央目录，支持存储和 deflate 两种压缩） */
export async function unzipEntry(buf, wanted) {
  const u8 = new Uint8Array(buf);
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  let eocd = -1;
  for (let i = u8.length - 22; i >= Math.max(0, u8.length - 65557); i--) if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  if (eocd < 0) throw new UnsupportedFile('这个 Word 文件打不开（不是有效的 .docx）。如果是老的 .doc，请在 Word 里另存为 .docx');
  const count = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  for (let n = 0; n < count; n++) {
    if (dv.getUint32(p, true) !== 0x02014b50) break;
    const method = dv.getUint16(p + 10, true);
    const csize = dv.getUint32(p + 20, true);
    const nameLen = dv.getUint16(p + 28, true), extraLen = dv.getUint16(p + 30, true), commentLen = dv.getUint16(p + 32, true);
    const local = dv.getUint32(p + 42, true);
    const name = new TextDecoder().decode(u8.subarray(p + 46, p + 46 + nameLen));
    if (name === wanted) {
      const start = local + 30 + dv.getUint16(local + 26, true) + dv.getUint16(local + 28, true);
      const data = u8.subarray(start, start + csize);
      if (method === 0) return data;
      if (method === 8) return inflateRaw(data);
      throw new UnsupportedFile('这个 Word 文件的压缩方式不支持');
    }
    p += 46 + nameLen + extraLen + commentLen;
  }
  return null;
}

export async function docxToText(buf) {
  const xml = await unzipEntry(buf, 'word/document.xml');
  if (!xml) throw new UnsupportedFile('这个 Word 文件里没有正文');
  const doc = new TextDecoder().decode(xml);
  const paras = [];
  for (const p of doc.match(/<w:p[ >][\s\S]*?<\/w:p>/g) || []) {
    const text = (p.match(/<w:t[^>]*>[^<]*<\/w:t>|<w:tab\/>|<w:br\/>/g) || [])
      .map((t) => (t.startsWith('<w:t') ? t.replace(/<[^>]+>/g, '') : ' ')).join('');
    if (text.trim()) paras.push(text);
  }
  const unescape = (s) => s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, '&');
  return unescape(paras.join('\n\n'));
}

export async function toText(filename, buf) {
  const ext = (filename.match(/\.[^.]+$/) || [''])[0].toLowerCase();
  if (ext === '.docx') return docxToText(buf);
  if (ext === '.md' || ext === '.markdown') return markdownToText(decodeText(buf));
  if (ext === '.txt' || ext === '.text' || ext === '') return decodeText(buf);
  if (ext === '.doc') throw new UnsupportedFile('老的 .doc 格式不支持，请在 Word 里另存为 .docx');
  throw new UnsupportedFile(`暂不支持 ${ext} 文件，目前支持 Word（.docx）、TXT、Markdown`);
}
