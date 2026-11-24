#!/usr/bin/env bash
# 高校智讯 CampusInsight · 局域网一键启动
# 适用：Linux / macOS / WSL / Android Termux；Windows 请用 start_lan.bat。
# Termux 可直接在本机跑服务，手机浏览器访问 http://127.0.0.1:8000/。
set -u
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  if command -v python >/dev/null 2>&1; then PY=python
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
echo "  高校智讯 CampusInsight · 局域网启动"
echo "  ------------------------------------"
echo "  解释器：$PY"
echo "  手机与电脑需连同一 WiFi；防火墙需放行 8000 端口。"
echo "  启动后复制打印的 http://<局域网IP>:8000/ 到手机浏览器。"
echo "  局域网 http 下页面与接口照常可用；离线壳与安装为应用需 HTTPS。"
echo
exec "$PY" run.py --lan "$@"
