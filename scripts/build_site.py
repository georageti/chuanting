"""拼出手机网页版（纯静态网站）：dist/site/，可以直接放到 GitHub Pages 等静态托管上。

界面和 Mac 版共用 web/（index.html、app.css、app.js、字体、图标），只是：
- <html> 加上 class="mobile-web"（隐藏 Mac 专用的功能，显示手机专用的）
- 在 app.js 之前声明“本地模式”，之后加载 mobile/boot.js（浏览器里的本地接口）
- 加上 PWA 清单、苹果主屏幕图标，以及 no-referrer（在线朗读接口要求不带来源网址）

只用 Python 标准库，GitHub Actions 里也能跑。用法：python3 -I scripts/build_site.py [输出目录]
"""
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "dist/site"

HEAD_EXTRA = """<meta name="referrer" content="no-referrer">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="appicons/icon-180.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="川听">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
"""
BEFORE_APP = """<script>
window.CHUANTING_LOCAL = true;
window.CHUANTING_READY = new Promise((r) => { window.__ctReady = () => { window.__ctOk = true; document.getElementById('ct-fail')?.remove(); r(); }; });
// 有脚本没下载成功（网络抖动、刚发布时 CDN 偶尔 503）或者半分钟还没准备好：提示并给“重新加载”按钮，不要一声不响地卡住
window.__ctFail = (why) => {
  if (window.__ctOk || document.getElementById('ct-fail')) return;
  document.body.insertAdjacentHTML('afterbegin', '<div id="ct-fail" style="position:fixed;inset:0 0 auto 0;z-index:60;padding:14px 16px;'
    + 'background:#fff3f3;color:#8a1c14;font:15px/1.5 system-ui;display:flex;gap:12px;align-items:center;justify-content:space-between">'
    + '<span>' + why + '</span><button onclick="location.reload()" style="border:0;border-radius:8px;padding:8px 14px;background:#1f6b3a;color:#fff;font:inherit;white-space:nowrap;flex:none">重新加载</button></div>');
};
setTimeout(() => window.__ctFail('川听加载得有点慢，可能是网络不好。'), 30000);
</script>
"""
AFTER_APP = """
<script type="module" src="boot.js" onerror="window.__ctFail('川听有文件没下载成功，请检查网络后重新加载。')"></script>"""


def main() -> None:
    web, mobile = ROOT / "web", ROOT / "mobile"
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for name in ("app.css", "app.js"):
        shutil.copy2(web / name, OUT / name)
    for d in ("fonts", "icons"):
        shutil.copytree(web / d, OUT / d)
    for d in ("lib", "vendor", "dict", "appicons"):
        shutil.copytree(mobile / d, OUT / d)
    for name in ("boot.js", "manifest.webmanifest"):
        shutil.copy2(mobile / name, OUT / name)

    html = (web / "index.html").read_text(encoding="utf-8")
    html = html.replace('<html lang="zh-CN"', '<html lang="zh-CN" class="mobile-web"', 1)
    html = html.replace('<link rel="stylesheet" href="fonts/fonts.css">', HEAD_EXTRA + '<link rel="stylesheet" href="fonts/fonts.css">', 1)
    assert '<script src="app.js"></script>' in html
    html = html.replace('<script src="app.js"></script>', BEFORE_APP + '<script src="app.js"></script>' + AFTER_APP, 1)
    (OUT / "index.html").write_text(html, encoding="utf-8")

    # 离线缓存的版本号：所有文件内容的指纹，网站一更新手机上就换新缓存
    h = hashlib.sha256()
    for f in sorted(p for p in OUT.rglob("*") if p.is_file()):
        h.update(f.relative_to(OUT).as_posix().encode())
        h.update(f.read_bytes())
    version = h.hexdigest()[:12]
    sw = (mobile / "sw.js").read_text(encoding="utf-8").replace("__VERSION__", version)
    (OUT / "sw.js").write_text(sw, encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"已生成 {OUT}（{size / 1e6:.1f} MB，版本 {version}）")


if __name__ == "__main__":
    main()
