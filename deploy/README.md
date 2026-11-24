# 云端部署与多平台交付

本目录只放**部署资产**，不改变应用运行架构：仍是单 FastAPI 进程，数据库/缓存通过环境变量外接。
所有命令中的 `python` 是代称；Windows 本机请用 `py -3.12`。

## 目录

```text
deploy/
├── cloud/
│   ├── provision.sh              # Terraform 开通 VPC/安全组/RDS/DCS
│   ├── cloud.env.example         # 连接串模板（不含真实密码）
│   └── terraform/                # VPC + 子网 + 两个安全组 + RDS + DCS
├── app/
│   ├── deploy_app.sh             # ECS 上手动部署（建库/建表/导入/systemd/Nginx）
│   ├── gaoxiao-zhixun.service    # systemd 单元
│   └── nginx-gaoxiao-zhixun.conf # 反向代理与静态缓存
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── README.md
```

## 前置条件（缺一不可）

| 项 | 要求 |
| --- | --- |
| 华为云账号 | 具备 VPC、RDS、DCS、ECS、EIP 的创建权限；建议使用子账号并只授予本项目资源组权限 |
| 凭证 | `export HW_ACCESS_KEY=... HW_SECRET_KEY=...`（可选 `HW_PROJECT_ID`），**不要写进任何文件** |
| Region | 默认 `cn-north-4`（华北-北京四）；跨 Region 需同时改 `terraform.tfvars` 与 auto-deploy 的 `--region` |
| Terraform | >= 1.5，且执行机可访问 `*.myhuaweicloud.com` |
| SSH | 22 端口来源 CIDR；演示可临时 `0.0.0.0/0`，正式环境必须收敛 |
| 预算 | 主备 RDS 2C4G + DCS 1G + ECS 2C4G，量级见 `docs/cloud_services.md` §6 |

## 路径 A：一键部署（推荐，使用 auto-deploy 技能）

> auto-deploy 只创建 ECS 并部署应用，**不创建 RDS/DCS**；所以先跑 §1 开通数据服务。

### 1. 开通 RDS / DCS

```bash
cp deploy/cloud/terraform/terraform.tfvars.example deploy/cloud/terraform/terraform.tfvars
# 填写 rds_password / redis_password / 规格 / SSH 来源 CIDR
export HW_ACCESS_KEY=...
export HW_SECRET_KEY=...
bash deploy/cloud/provision.sh
```

产物：
- `deploy/cloud/cloud-output.json`：VPC/子网/私网 IP/端口（无密码）
- `deploy/cloud/cloud.env`：`DATABASE_URL` / `REDIS_URL`（600，已 gitignore）

### 2. 部署应用（ECS + systemd）

```bash
python .codeartsdoer/skills/auto-deploy/scripts/auto_deploy.py \
  --repo . \
  --app-name gaoxiao-zhixun \
  --region cn-north-4 \
  --network-mode reuse \
  --existing-vpc-id <cloud-output.json 的 vpc_id> \
  --existing-subnet-id <cloud-output.json 的 subnet_id> \
  --env-file deploy/cloud/cloud.env \
  --env LLM_PROVIDER=none \
  --yes
```

若已有 ECS，可跳过 auto-deploy，直接在 ECS 上手动部署：

```bash
sudo APP_DIR=/opt/gaoxiao-zhixun ENV_FILE=/etc/gaoxiao-zhixun/env bash deploy/app/deploy_app.sh
sudo WITH_NGINX=1 APP_DIR=/opt/gaoxiao-zhixun ENV_FILE=/etc/gaoxiao-zhixun/env bash deploy/app/deploy_app.sh
```

### 3. 验收

```bash
curl -s http://<ECS 公网 IP>/api/health
# 期望：status=ok；database.kind 含 "MySQL / 华为云 RDS"

curl -s http://<ECS 公网 IP>/api/ai/status
# 期望：cache.kind 含 "Redis（华为云 DCS）"；llm 未配置时 active_engine=rule
```

三个必须人工确认的点：
1. `/api/health` 的 `access.listen` 是 `0.0.0.0`，`access.url` 不是 `0.0.0.0`；
2. 首次启动日志出现 `数据库=MySQL / 华为云 RDS`，而不是 SQLite；
3. 断网/重启容器后数据仍在（RDS 持久化，不是容器内 SQLite）。

## 路径 B：容器交付

```bash
docker compose -f deploy/docker/docker-compose.yml up -d --build
# 不传 DATABASE_URL 时自动回落 SQLite（仅适合本地演示）
```

镜像内是单进程 uvicorn，健康检查打 `/api/health`；数据目录挂 `zhixun-data` 卷。
接云上 RDS/DCS 时，把 `DATABASE_URL` / `REDIS_URL` 通过宿主机环境变量传进去即可。

## 路径 C：本地开发

```bash
python run.py --check
python run.py --open
```

手机/平板联调（同一 Wi-Fi）：

```bash
start_lan.bat        # Windows：监听 0.0.0.0 并打印可复制的局域网地址
./start_lan.sh       # Linux / macOS
```

## 安全与清理

- AK/SK 只走环境变量；`terraform.tfvars`、`cloud.env`、`*.tfstate` 已被 `.gitignore` 忽略。
- 若凭证曾出现在聊天/截图中，立即在 IAM 控制台停用并轮换。
- 演示结束先备份，再销毁：
  ```bash
  cd deploy/cloud/terraform && terraform destroy
  ```
  `terraform destroy` 会删除 RDS 实例与数据，不可恢复。

## 状态边界

| 项目 | 状态 |
| --- | --- |
| Terraform 资源定义、部署脚本、systemd/Nginx/Dockerfile | **已就绪**（本仓库内可直接执行） |
| 真实开通 RDS/DCS、公网部署、HTTPS | **待资源验证**（需账号、Terraform、可达网络） |
| Android / HarmonyOS 真机安装 | **待真机验证**（PWA 形态已就绪） |
| 并发压测与云端性能数字 | **未做**，不得把本地 benchmark 当云端结果 |