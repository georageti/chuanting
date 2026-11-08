"""生成 GitHub Release 的下载包：dist/chuanting-mac.zip、dist/chuanting-windows.zip。

包里是仓库里的全部文件（按工作区原样，保留可执行权限、Windows 脚本的 CRLF 和 BOM），
加上已经生成好的 data/dict.sqlite，装的时候不用再下载 63MB 的原始词典；
另一个系统专用的启动和安装文件不放进去，免得看着乱。

用法：python3 -I scripts/build_release.py   （先跑过 scripts/setup.sh，保证 data/dict.sqlite 存在）
"""
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
TOP = "chuanting"

ONLY_WINDOWS = {"scripts/setup.ps1", "scripts/make_ico.py", "启动川听.bat", "安装川听.bat"}
ONLY_MAC = {"scripts/setup.sh", "scripts/build_mac_app.py", "启动川听.command", "安装川听.command", "assets/chuanting.icns"}

README_MAC = """川听（Mac 版）

1. 先装 Python 3.10 以上：https://www.python.org/downloads/ （已经装过可以跳过）
2. 双击文件夹里的「安装川听.command」。
   第一次如果提示“无法验证开发者”，请右键它 → 打开 → 打开。
3. 装好后会自动打开川听。以后在“应用程序”或 Spotlight（⌘ 空格）里搜“川听”打开，
   用完在网页「设置」最下面点“关闭川听”。

第一次用，到「设置」里填 DeepSeek 的 API Key（platform.deepseek.com 申请）；不填也能用，会直接把原文句子挖空来练。
请把这个文件夹放在固定的位置再安装（比如“文稿”里）；以后挪了位置，重新双击安装一次即可。
详细说明：https://github.com/georageti/chuanting
"""

README_WIN = """川听（Windows 版）

1. 先装 Python 3.10 以上：https://www.python.org/downloads/
   安装时一定要勾选 “Add python.exe to PATH”。
2. 双击文件夹里的「安装川听.bat」。
3. 装好后，桌面和开始菜单会出现“川听”，双击打开（不会弹黑色窗口）。
   用完在网页「设置」最下面点“关闭川听”。

第一次用，到「设置」里填 DeepSeek 的 API Key（platform.deepseek.com 申请）；不填也能用，会直接把原文句子挖空来练。
请先把整个文件夹解压到固定的位置（比如“文档”里）再安装，不要在压缩包里直接运行。
Windows 版还没有在真机上大量测试，遇到问题欢迎到 GitHub 提 issue：
https://github.com/georageti/chuanting
"""


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return [f for f in out.decode("utf-8").split("\0") if f]


def add(z: zipfile.ZipFile, src: Path, arc: str) -> None:
    info = zipfile.ZipInfo.from_file(src, f"{TOP}/{arc}")
    info.compress_type = zipfile.ZIP_DEFLATED
    with open(src, "rb") as f:
        z.writestr(info, f.read(), compresslevel=9)


def build(name: str, skip: set[str], readme: str) -> Path:
    out = DIST / name
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w") as z:
        for f in tracked_files():
            if f in skip:
                continue
            add(z, ROOT / f, f)
        add(z, ROOT / "data/dict.sqlite", "data/dict.sqlite")
        info = zipfile.ZipInfo(f"{TOP}/先看我.txt")
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        z.writestr(info, readme.replace("\n", "\r\n") if "windows" in name else readme)
    return out


def main() -> None:
    if not (ROOT / "data/dict.sqlite").exists():
        sys.exit("缺少 data/dict.sqlite，先运行 bash scripts/setup.sh")
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty:
        print("提醒：工作区有没提交的改动，下载包会按工作区原样打包：\n" + dirty)
    for name, skip, readme in (("chuanting-mac.zip", ONLY_WINDOWS, README_MAC),
                               ("chuanting-windows.zip", ONLY_MAC, README_WIN)):
        p = build(name, skip, readme)
        print(f"{p.name}  {p.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
