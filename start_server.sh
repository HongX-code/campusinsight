#!/usr/bin/env bash
# 高校智讯 CampusInsight · 跨平台一键启动（Linux / macOS / WSL / Termux）
# Windows 请使用同目录下的 start_server.bat。
set -u
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  if command -v python >/dev/null 2>&1; then
    PY=python
  else
    echo "[错误] 未找到 Python 3.10+，请先安装后重试。"
    exit 1
  fi
fi

if ! "$PY" -c "import fastapi" >/dev/null 2>&1; then
  echo "[错误] $PY 未安装项目依赖。"
  echo "      请先执行：$PY -m pip install -r requirements.txt"
  exit 1
fi

echo
echo "  高校智讯 CampusInsight · 本地一键启动"
echo "  ------------------------------------"
echo "  解释器：$PY"
echo

if ! "$PY" run.py --check; then
  echo
  echo "  [体检未通过] 请按上面 FAIL 项处理后重试。"
  exit 1
fi

echo
echo "  浏览器请打开 http://127.0.0.1:8000/ （0.0.0.0 是监听地址，不是网址）。"
echo "  手机 / 平板访问：先设置 APP_HOST=0.0.0.0，再用电脑局域网 IP 访问。"
echo "  按 Ctrl+C 停止服务。"
echo
if [ "$#" -eq 0 ]; then
  set -- --open
fi
exec "$PY" run.py "$@"