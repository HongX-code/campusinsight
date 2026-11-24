#!/usr/bin/env bash
# 在华为云 ECS 上手动部署 CampusInsight（推荐优先使用 auto-deploy 技能）
#
# 前置条件：
#   · 代码已上传到 /opt/gaoxiao-zhixun
#   · /etc/gaoxiao-zhixun/env 已存在（600），至少包含 DATABASE_URL、REDIS_URL
#   · 以 root 执行；系统已安装 python3.10+、nginx（可选）
#
# 用法：
#   sudo APP_DIR=/opt/gaoxiao-zhixun ENV_FILE=/etc/gaoxiao-zhixun/env bash deploy/app/deploy_app.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/gaoxiao-zhixun}"
ENV_FILE="${ENV_FILE:-/etc/gaoxiao-zhixun/env}"
APP_USER="${APP_USER:-gaoxiao}"
WITH_NGINX="${WITH_NGINX:-0}"

if [ "$(id -u)" -ne 0 ]; then
  echo "[FAIL] 请用 root 或 sudo 执行" >&2
  exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "[FAIL] 缺少 $ENV_FILE（至少包含 DATABASE_URL 与 REDIS_URL）" >&2
  exit 1
fi

echo "[1/6] 创建系统用户"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

echo "[2/6] 创建虚拟环境并安装依赖"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "[3/6] 建库、建表并导入演示数据"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
cd "$APP_DIR"
"$APP_DIR/.venv/bin/python" scripts/cloud_bootstrap.py --create-db --with-samples

echo "[4/6] 安装 systemd 服务"
install -m 644 "$APP_DIR/deploy/app/gaoxiao-zhixun.service" /etc/systemd/system/gaoxiao-zhixun.service
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data"
systemctl daemon-reload
systemctl enable gaoxiao-zhixun
systemctl restart gaoxiao-zhixun

echo "[5/6] 等待健康检查"
for i in $(seq 1 20); do
  if "$APP_DIR/.venv/bin/python" - <<'PY'
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=3) as r:
    data = json.load(r)
print(data["status"], data["database"]["kind"])
raise SystemExit(0 if data["status"] == "ok" else 1)
PY
  then
    echo "[OK] 服务健康"
    break
  fi
  sleep 2
  if [ "$i" -eq 20 ]; then
    echo "[FAIL] 健康检查超时，请查看 journalctl -u gaoxiao-zhixun -n 100" >&2
    exit 1
  fi
done

echo "[6/6] Nginx（可选）"
if [ "$WITH_NGINX" = "1" ]; then
  install -m 644 "$APP_DIR/deploy/app/nginx-gaoxiao-zhixun.conf" /etc/nginx/conf.d/gaoxiao-zhixun.conf
  nginx -t
  systemctl reload nginx
  echo "[OK] Nginx 已接管 80 端口"
else
  echo "[SKIP] 未启用 Nginx；如需域名/HTTPS，设置 WITH_NGINX=1 重跑"
fi

echo
echo "访问：http://<ECS 公网 IP>/  或  http://<ECS 公网 IP>:8000/"
echo "健康：http://<ECS 公网 IP>/api/health"