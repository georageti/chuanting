"""把上传的文档变成纯文本：Word（.docx）、TXT、Markdown。"""
from __future__ import annotations

import io
import re
from pathlib import Path

SUPPORTED = {".docx", ".txt", ".md", ".markdown", ".text"}


class UnsupportedFile(ValueError):
    pass


def decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "gb18030", "latin-1"):
        try:
            text = data.decode(enc)
            if enc == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def markdown_to_text(md: str) -> str:
    md = re.sub(r"```.*?```", "\n", md, flags=re.S)           # 代码块
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)             # 注释
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)                # 图片
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)            # 链接留文字
    md = re.sub(r"<[^>]+>", "", md)                            # HTML 标签
    md = re.sub(r"^\s{0,3}#{1,6}\s*", "", md, flags=re.M)      # 标题
    md = re.sub(r"^\s{0,3}>\s?", "", md, flags=re.M)           # 引用
    md = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", md, flags=re.M)  # 列表符号
    md = re.sub(r"(\*\*|__|\*|_|`|~~)(?=\S)(.+?)(?<=\S)\1", r"\2", md)  # 粗体斜体
    md = re.sub(r"^\s*([-*_]\s*){3,}$", "", md, flags=re.M)    # 分隔线
    md = re.sub(r"^\|.*\|$", lambda m: " ".join(c.strip() for c in m.group(0).strip("|").split("|")) + ".", md, flags=re.M)
    return md


def docx_to_text(data: bytes) -> str:
    import docx  # python-docx

    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" ".join(cells))
    return "\n\n".join(p for p in parts if p.strip())


def to_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".docx":
        try:
            return docx_to_text(data)
        except Exception as e:  # 损坏或其实是 .doc 改的后缀
            raise UnsupportedFile(f"这个 Word 文件打不开（{e.__class__.__name__}）。如果是老的 .doc，请在 Word 里另存为 .docx") from e
    if ext in (".md", ".markdown"):
        return markdown_to_text(decode_text(data))
    if ext in (".txt", ".text", ""):
        return decode_text(data)
    if ext == ".doc":
        raise UnsupportedFile("老的 .doc 格式不支持，请在 Word 里另存为 .docx")
    raise UnsupportedFile(f"暂不支持 {ext} 文件，目前支持 Word（.docx）、TXT、Markdown")
