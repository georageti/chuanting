"""启动：.venv/bin/python -m chuanting [--port 8780] [--no-open]"""
import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="川听：扫描文档，听语境，拼单词")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"川听已启动：{url}  （按 Ctrl+C 退出）")
    uvicorn.run("chuanting.server:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
