#!/bin/bash
# 双击安装川听（Mac）：装依赖、准备词典、跑自测，再把“川听”放进“应用程序”并打开。
# 第一次双击如果提示“无法验证开发者”，请右键这个文件 → 打开。
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null || ! python3 -c 'import sys; assert sys.version_info >= (3, 10)' 2>/dev/null; then
  echo "需要 Python 3.10 以上：请到 https://www.python.org/downloads/ 下载安装，装好后再双击这个文件。"
  read -n 1 -s -r -p "按任意键关闭"; exit 1
fi
bash scripts/setup.sh && python3 -I scripts/build_mac_app.py --install && open "$HOME/Applications/川听.app"
echo
echo "装好了：以后在“应用程序”或 Spotlight 里打开“川听”。"
read -n 1 -s -r -p "按任意键关闭"
