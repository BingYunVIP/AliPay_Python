#!/usr/bin/env bash
# ByPay · 支付宝测试工具 启动脚本
# 用法：./run.sh   （首次会自动创建虚拟环境并安装依赖）

set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "首次运行：正在创建虚拟环境并安装依赖…"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  echo "依赖安装完成。"
fi

exec python3 main.py
