#!/bin/bash
# Linux 启动：全自动登录 + 抓取课程表
# 运行：bash 启动-Linux.sh   （或 chmod +x 后 ./启动-Linux.sh）
cd "$(dirname "$0")" || exit 1

echo "============================================"
echo "   📅 课程表助手 · 全自动抓取"
echo "============================================"
echo "   构建版本：2026.07.15-browser-5"
echo "   Copyright (c) 2026 Jiapeng Lee"
echo "   GitHub: https://github.com/awymp3/swu-schedule-export"
echo "   Email: wadrqhh@gmail.com"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 未找到 python3，请先安装：sudo apt install python3 python3-pip python3-tk"
  read -n 1 -s -r -p "按任意键退出…"; exit 1
fi

echo "🔍 检查依赖..."
NEED=""
python3 -c "import setuptools" 2>/dev/null || NEED="$NEED setuptools"
python3 -c "import setuptools, undetected_chromedriver; raise SystemExit(0 if undetected_chromedriver.__version__ == '3.1.6' else 1)" 2>/dev/null || NEED="$NEED undetected-chromedriver==3.1.6"
python3 -c "import ddddocr" 2>/dev/null || NEED="$NEED ddddocr"
python3 -c "import selenium; raise SystemExit(0 if selenium.__version__ == '4.9.1' else 1)" 2>/dev/null || NEED="$NEED selenium==4.9.1"
python3 -c "import PIL" 2>/dev/null || NEED="$NEED pillow"
if [ -n "$NEED" ]; then
  echo "📦 首次使用，正在安装依赖（清华镜像）：$NEED"
  if ! pip3 install $NEED --upgrade --prefer-binary --timeout 30 --retries 3 -i https://pypi.tuna.tsinghua.edu.cn/simple; then
    echo "⚠️ 清华镜像安装失败，改用阿里云镜像重试…"
    if ! pip3 install $NEED --upgrade --prefer-binary --timeout 30 --retries 3 -i https://mirrors.aliyun.com/pypi/simple/; then
      echo "⚠️ 阿里云镜像安装失败，改用官方 PyPI 重试…"
      pip3 install $NEED --upgrade --prefer-binary --timeout 30 --retries 3 || exit 1
    fi
  fi
fi
# 图形输入窗口需要 tkinter
python3 -c "import tkinter" 2>/dev/null || echo "⚠️ 未装 tkinter，账号将用终端输入。如需弹窗：sudo apt install python3-tk"

python3 capture_auto.py

echo
read -n 1 -s -r -p "按任意键关闭…"
