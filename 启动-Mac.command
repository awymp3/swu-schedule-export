#!/bin/bash
# 双击运行：自动登录 + 抓取课程表
# 若系统未安装 Python 3，会下载到项目 .runtime/，不会修改系统 PATH。

ROOT="$(cd "$(dirname "$0")" && pwd)" || exit 1
cd "$ROOT" || exit 1

echo "============================================"
echo "   课程表助手 · 全自动抓取"
echo "============================================"
echo "   构建版本：2026.07.15-browser-10"
echo "   Copyright (c) 2026 Jiapeng Lee"
echo "   GitHub: https://github.com/awymp3/swu-schedule-export"
echo "   Email: wadrqhh@gmail.com"
echo

RUNTIME_DIR="$ROOT/.runtime"
PYTHON_DIR="$RUNTIME_DIR/python"
UV_DIR="$RUNTIME_DIR/uv"
PYTHON=""
PIP_SCOPE=(--user)
PIP_TUNA="https://pypi.tuna.tsinghua.edu.cn/simple"
PIP_ALIYUN="https://mirrors.aliyun.com/pypi/simple/"
# GitHub release downloads are routed through a China-accessible proxy first.
# The official source remains a fallback when the proxy is unavailable.
UV_GITHUB_PROXY="https://ghproxy.net/https://github.com"
UV_PYTHON_MIRROR="$UV_GITHUB_PROXY/astral-sh/python-build-standalone/releases/download"

is_usable_python() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}

find_local_python() {
  local candidate
  candidate="$(find "$PYTHON_DIR" -type f -path '*/bin/python3' -perm -111 -print -quit 2>/dev/null)"
  if [ -n "$candidate" ] && is_usable_python "$candidate"; then
    printf '%s' "$candidate"
  fi
}

install_local_python() {
  local uv installer
  mkdir -p "$RUNTIME_DIR" || return 1
  uv="$UV_DIR/uv"
  if [ ! -x "$uv" ]; then
    installer="$RUNTIME_DIR/install-uv.sh"
    echo "未检测到 Python 3，正在下载项目本地运行时…"
    if ! curl --fail --location --retry 3 --silent --show-error \
      https://astral.sh/uv/install.sh -o "$installer"; then
      echo "❌ 下载本地运行时工具失败，请检查网络后重试。"
      return 1
    fi
    if ! env UV_INSTALL_DIR="$UV_DIR" UV_NO_MODIFY_PATH=1 \
      UV_INSTALLER_GITHUB_BASE_URL="$UV_GITHUB_PROXY" sh "$installer"; then
      echo "⚠️ 国内镜像下载 uv 失败，改用官方源重试…"
      if ! env UV_INSTALL_DIR="$UV_DIR" UV_NO_MODIFY_PATH=1 sh "$installer"; then
        echo "❌ 本地运行时工具安装失败。"
        return 1
      fi
    fi
  fi
  if [ ! -x "$uv" ]; then
    uv="$(find "$UV_DIR" -type f -name uv -perm -111 -print -quit 2>/dev/null)"
  fi
  if [ -z "$uv" ] || [ ! -x "$uv" ]; then
    echo "❌ 未能找到已下载的本地运行时工具。"
    return 1
  fi
  if ! env UV_PYTHON_INSTALL_MIRROR="$UV_PYTHON_MIRROR" \
    "$uv" python install --install-dir "$PYTHON_DIR" --no-bin 3.12; then
    echo "⚠️ 国内镜像下载 Python 失败，改用官方源重试…"
    if ! "$uv" python install --install-dir "$PYTHON_DIR" --no-bin 3.12; then
      echo "❌ Python 本地运行时下载失败，请检查网络后重试。"
      return 1
    fi
  fi
  return 0
}

# 优先使用系统 Python；没有时复用或下载项目内的 Python。
if command -v python3 >/dev/null 2>&1 && is_usable_python "$(command -v python3)"; then
  PYTHON="$(command -v python3)"
else
  PYTHON="$(find_local_python)"
  if [ -z "$PYTHON" ]; then
    if ! install_local_python; then
      echo
      read -n 1 -s -r -p "按任意键退出…"
      exit 1
    fi
    PYTHON="$(find_local_python)"
    if [ -z "$PYTHON" ]; then
      echo "❌ 本地 Python 安装完成后仍未找到可执行文件。"
      echo
      read -n 1 -s -r -p "按任意键退出…"
      exit 1
    fi
  fi
  PIP_SCOPE=()
fi

echo "使用 Python：$PYTHON"
"$PYTHON" -m ensurepip --upgrade >/dev/null 2>&1
if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
  echo "❌ 当前 Python 未提供 pip，无法安装所需依赖。"
  echo
  read -n 1 -s -r -p "按任意键退出…"
  exit 1
fi

echo "🔍 检查依赖..."
NEED=()
"$PYTHON" -c "import setuptools" 2>/dev/null || NEED+=(setuptools)
"$PYTHON" -c "import setuptools, undetected_chromedriver" 2>/dev/null || NEED+=(undetected-chromedriver)
"$PYTHON" -c "import ddddocr" 2>/dev/null || NEED+=(ddddocr)
"$PYTHON" -c "import selenium" 2>/dev/null || NEED+=(selenium)
"$PYTHON" -c "import PIL" 2>/dev/null || NEED+=(pillow)
if [ ${#NEED[@]} -gt 0 ]; then
  echo "📦 首次使用，正在安装依赖（清华镜像）：${NEED[*]}"
  if ! "$PYTHON" -m pip install "${PIP_SCOPE[@]}" "${NEED[@]}" --upgrade \
    --prefer-binary --timeout 30 --retries 3 --index-url "$PIP_TUNA"; then
    echo "⚠️ 清华镜像安装失败，改用阿里云镜像重试…"
    if ! "$PYTHON" -m pip install "${PIP_SCOPE[@]}" "${NEED[@]}" --upgrade \
      --prefer-binary --timeout 30 --retries 3 --index-url "$PIP_ALIYUN"; then
      echo "⚠️ 阿里云镜像安装失败，改用官方 PyPI 重试…"
      "$PYTHON" -m pip install "${PIP_SCOPE[@]}" "${NEED[@]}" --upgrade \
        --prefer-binary --timeout 30 --retries 3 || {
        echo "❌ 依赖安装失败，请检查网络后重试。"
        echo
        read -n 1 -s -r -p "按任意键退出…"
        exit 1
      }
    fi
  fi
fi

"$PYTHON" "$ROOT/capture_auto.py"
EXITCODE=$?

echo
read -n 1 -s -r -p "按任意键关闭本窗口…"
exit "$EXITCODE"
