#!/bin/bash
# 一键搭环境：建虚拟环境、装依赖、下载 ECDICT、生成词典库、跑自测。
# 用法：在项目目录里运行  bash scripts/setup.sh   （需要 macOS、Python 3.10 以上）
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
echo "== 1/4 建虚拟环境 .venv"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check -r requirements.txt

echo "== 2/4 下载 ECDICT 词典（约 63MB，MIT 许可）"
mkdir -p data/raw
if [ ! -s data/raw/ecdict.csv ]; then
  curl -fL --retry 3 -o data/raw/ecdict.csv.part https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv
  mv data/raw/ecdict.csv.part data/raw/ecdict.csv
fi

echo "== 3/4 生成 data/dict.sqlite"
[ -s data/dict.sqlite ] || .venv/bin/python -I scripts/build_dict.py

echo "== 4/4 自测"
.venv/bin/python tests/run_tests.py | tail -1

echo
echo "好了。启动：.venv/bin/python run.py"
echo "想要 Mac 上的快捷入口：python3 -I scripts/build_mac_app.py --install"
