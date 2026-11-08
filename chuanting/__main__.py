"""启动：python run.py [--port 8780] [--no-open]

- 川听已经在这个端口运行时，只打开网页，不再启动第二份。
- 用 pythonw（Windows 上没有黑色窗口）启动时没有控制台，输出写到数据目录里的 川听.log。
"""
import argparse
import sys
import threading
import urllib.request
import webbrowser


def already_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "api/stats", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description="川听：扫描文档，听语境，拼单词")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/"

    if already_running(url):
        print(f"川听已经在运行：{url}")
        if not args.no_open:
            webbrowser.open(url)
        return

    if sys.stdout is None or sys.stderr is None:  # pythonw / 后台启动：没有控制台
        from chuanting.store import HOME

        HOME.mkdir(parents=True, exist_ok=True)
        log = open(HOME / "川听.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log

    import uvicorn

    from chuanting import server

    config = uvicorn.Config(server.app, host="127.0.0.1", port=args.port, log_level="warning")
    srv = uvicorn.Server(config)
    server.UVICORN = srv  # “关闭川听”通过它正常退出（Mac、Windows 通用）
    if not args.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"川听已启动：{url}  （按 Ctrl+C 退出）", flush=True)
    srv.run()


if __name__ == "__main__":
    main()
