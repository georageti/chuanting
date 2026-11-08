"""启动川听（不依赖当前目录）：.venv/bin/python run.py [--port 8780] [--no-open]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chuanting.__main__ import main  # noqa: E402

main()
