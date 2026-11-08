"""在 Mac 上生成 Windows 快捷方式用的图标 assets/chuanting.ico（和 川听.app 同一个图标）。

ICO 里直接放 PNG（Windows Vista 起都支持），尺寸 16 / 24 / 32 / 48 / 64 / 128 / 256。
用法：python3 -I scripts/make_ico.py   （需要 swiftc 和 sips，只在 Mac 上跑；生成的 .ico 提交进仓库）
"""
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_mac_app import ICON_SWIFT  # noqa: E402

SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> None:
    out = ROOT / "assets" / "chuanting.ico"
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "icon.swift").write_text(ICON_SWIFT, encoding="utf-8")
        subprocess.run(["swiftc", "-O", str(d / "icon.swift"), "-o", str(d / "icon")], check=True, capture_output=True)
        big = d / "icon.png"
        subprocess.run([str(d / "icon"), str(big)], check=True)
        pngs = []
        for s in SIZES:
            p = d / f"{s}.png"
            subprocess.run(["sips", "-z", str(s), str(s), str(big), "--out", str(p)], check=True, capture_output=True)
            pngs.append(p.read_bytes())
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    entries, blobs = b"", b""
    for s, data in zip(SIZES, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    out.write_bytes(header + entries + blobs)
    print(f"已生成 {out}（{out.stat().st_size // 1024} KB）")


if __name__ == "__main__":
    main()
