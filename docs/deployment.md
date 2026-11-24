# 部署指南（含华为云接入）

本文档说明如何把「高校智讯」从本地运行到华为云上，并给出赛题要求的云服务接入点。
**状态标注很重要**：`已实现` 表示代码已完成且本地验证通过；`待资源验证` 表示代码路径已就绪，
但需要真实的华为云账号与代金券开通后才能确认连通性。

## 1. 本地运行（3 条命令）

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows；macOS/Linux 用 source .venv/bin/activate
pip install -r requirements.txt
python scripts/init_db.py
python scripts/import_samples.py
python run.py --check             # 可选：启动前体检（依赖 / 端口 / 数据库 / 页面资源）
python run.py                     # 浏览器打开 http://127.0.0.1:8000
```

Windows 也可直接双击 `start_server.bat`，它会自动挑选装好依赖的解释器、跑一遍体检再打开浏览器。

默认使用 SQLite，无需安装数据库即可完整体验全部功能。**本地默认监听 `127.0.0.1`；
`0.0.0.0` 只是「监听所有网卡」的通配地址，不是浏览器网址**，直接访问
`http://0.0.0.0:8000/` 会得到「无法访问此页面」，详见 §6。

## 2. 环境变量

复制 `.env.example` 为 `.env`，按需填写。`config.py` 会读取该文件并注入环境变量。

| 变量 | 作用 | 示例 |
| --- | --- | --- |
| `DATABASE_URL` | 数据库连接串；留空则用 SQLite | `mysql+pymysql://user:pass@host:3306/zhixun?charset=utf8mb4` |
| `LLM_PROVIDER` | `none` / `maas` / `openai`；为 `none` 时走规则引擎 | `maas` |
| `LLM_BASE_URL` | 大模型服务地址（OpenAI 兼容协议） | `https://api.modelarts-maas.com/v1` |
| `LLM_API_KEY` | 模型服务密钥 | `sk-****` |
| `LLM_MODEL` | 模型名称 | `DeepSeek-V3` |
| `REDIS_URL` | 缓存地址；留空则用内存缓存 | `redis://:***@host:6379/0` |
| `CRAWL_ALLOWED_DOMAINS` | 允许采集的域名后缀 | `edu.cn,ac.cn` |
| `APP_PORT` | 服务端口 | `8000` |

## 3. 华为云服务接入点

### 3.1 RDS for MySQL（数据持久化）— 代码已实现

对应赛题要求"关联 RDS 等华为云服务"。系统通过 `DATABASE_URL` 单点切换，业务代码无需改动。

开通后按以下步骤切换：

```bash
# 1) 在 RDS 控制台创建实例，记录内网地址、端口、账号
# 2) 创建数据库与账号（字符集必须 utf8mb4，否则中文通知会乱码）
CREATE DATABASE zhixun DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

# 3) 写入 .env
DATABASE_URL=mysql+pymysql://zhixun_user:密码@rm-xxxx.mysql.rds.myhuaweicloud.com:3306/zhixun?charset=utf8mb4

# 4) 建表并验证
python scripts/init_db.py
python -c "from app.database.connection import db_health; print(db_health())"
```

也可以直接执行 `app/database/init.sql`（已按 MySQL 语法写好，含索引与外键）。
`/api/health` 返回的 `database.kind` 会显示为"MySQL / 华为云 RDS"，这是验证切换是否生效的最快方式。

> 注意：RDS 安全组必须放通应用所在主机的内网网段。本地调试连接公网地址时，需临时绑定弹性公网 IP，
> 并在答辩材料中说明生产环境使用内网连接。

### 3.2 Redis（缓存）— 代码已实现

`app/services/cache.py` 采用"Redis 优先、内存降级"策略：配置了 `REDIS_URL` 就用 Redis，
未配置或连接失败自动回落到进程内字典缓存，**不会因为缓存不可用导致接口 500**。
缓存用于解析结果复用（同一份通知内容不重复计费）。

### 3.3 MaaS 大模型服务（AI 解析与问答）— 代码已实现，待密钥验证

`app/services/llm_client.py` 按 OpenAI 兼容协议调用，适配华为云 MaaS 与 ModelArts 推理服务。
填入 `LLM_PROVIDER=maas` 与密钥后，`/api/ai/status` 会显示 `engine: llm`。

切换后建议完整回归一次：

```bash
python scripts/evaluate.py --llm          # 大模型引擎在开发集上的成绩
python scripts/evaluate.py --set holdout --llm
python tests/test_smoke.py
```

> 关键设计：大模型的输出**不直接入库**。`analyzer.py` 会把模型结果与规则结果做交叉校验，
> 日期必须能在原文中定位到（接地校验），校验不过的字段置空并标记 `needs_review=true`。
> 这样即使模型在某个通知上出错，也不会污染匹配与问答链路。

### 3.4 ECS / 应用托管（部署上线）— 待资源验证

最简部署（单机 + systemd + Nginx 反代）：

```bash
# 1) ECS 安装 Python 3.11+
sudo apt update && sudo apt install -y python3.11 python3.11-venv nginx

# 2) 上传代码并装依赖
cd /opt && git clone <仓库地址> gaoxiao-zhixun && cd gaoxiao-zhixun
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 3) 配置 .env（RDS + Redis + MaaS 三项）
vi .env

# 4) systemd 常驻
sudo tee /etc/systemd/system/zhixun.service > /dev/null <<'EOF'
[Unit]
Description=GaoXiao ZhiXun
After=network.target

[Service]
WorkingDirectory=/opt/gaoxiao-zhixun
ExecStart=/opt/gaoxiao-zhixun/.venv/bin/python run.py
Restart=always
Environment=APP_HOST=0.0.0.0
Environment=APP_PORT=8000

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now zhixun
```

Nginx 反向代理（80 → 8000），并放通安全组 80 端口即可从外网访问。

> **上线前必须改的三件事**：① 关闭 `DEBUG`；② 采集白名单收紧到本校域名；
> ③ 若对外网开放，需在 Nginx 层加访问控制或限流，避免被当作抓取代理。

### 3.5 CodeArts 代码智能体 — 需在开发过程中实际使用并留痕

赛题明确要求"下载并使用华为云码道 CodeArts 代码智能体"，并要求体现其在需求理解、代码生成、
项目构建、问题修复、部署上线各环节的价值。**完整提示词集（P0 系统级 + P1—P10 任务级）、
执行顺序、七类截图清单与留痕模板见 `docs/codearts_prompts.md`**；工程级常驻约束已写入
`.codeartsdoer/AGENTS.md`。建议留痕方式：

| 留痕类型 | 具体做法 | 对应提示词 |
| --- | --- | --- |
| 使用截图 | 需求描述 → 生成代码 → 调试修复 三个环节各截一张，标注日期 | P1 / P5 / P2 |
| 会话记录 | 导出关键 Prompt 与生成结果的对照（体现"想法到应用"的闭环） | `codearts_prompts.md` §5.3 |
| 采纳说明 | 说明哪些模块由 CodeArts 生成、做了哪些人工修正，避免夸大 | §5.4 模板 |
| 排错记录 | 记录一次由 CodeArts 辅助定位的真实 Bug，比"用过了"更有说服力 | P2 + §5.5 模板 |
| 构建 / 部署记录 | 流水线全绿、云资源列表、外网访问首页各一张 | P5 / P6 / P7 |

**一键开工（v1.1 起）**：按 `docs/codearts_prompts.md` §1.0 先跑 `codearts_tools.py doctor`
确认解释器前缀（本机 `python` 指向未装依赖的 3.13，正确前缀为 `py -3.12`），再跑 `bootstrap`
生成 `11_CodeArts使用记录/` 的 18 个文件骨架，最后用 `ci_gate.py --dry-run` 核对 9 阶段门禁。
逐日任务、11 条短提示词与七类截图清单可在 `codearts/CodeArts实操作战台.html` 单页查看
（离线可用，勾选进度保存在本机浏览器；打印 / 存 PDF 可作团队执行卡）。

> **前置动作**：`.codeartsdoer/.codebase/` 的代码库索引建立于 2026-11-05，
> 缺口清单会随新增脚本变化，以 `python scripts/codearts_tools.py index` 现场输出为准
> （详见 `codearts_prompts.md` §1.1）。投喂任何提示词前，先在 CodeArts 客户端
> 重新打开工作区并全量重建索引，否则智能体看不到 `validator.py` / `fields.py` / `genre.py` 等模块。
>
> 这一项无法由代码仓库替代，需要团队在真实开发中操作并截图存档。提示词方案与 `AGENTS.md`
> 属于"已就绪"，会话截图、云资源开通与部署记录仍为"待补齐"，材料里不得提前写成已完成。

## 4. 云资源连通性自检

资源开通后，用下面的脚本一次性确认四类依赖是否真的连通，输出可直接贴进答辩材料：

```bash
python - <<'PY'
from app.config import settings
from app.database.connection import db_health
from app.services.cache import cache
from app.services.llm_client import available

print("数据库 :", db_health())
print("缓存   :", "Redis" if getattr(cache, "backend", "") == "redis" else "内存（降级）")
print("大模型 :", "已启用 " + settings.LLM_MODEL if available() else "未启用（规则引擎）")
print("采集白名单:", settings.CRAWL_ALLOWED_DOMAINS)
PY
```

预期输出（全部接通时）：

```
数据库 : {'ok': True, 'kind': 'MySQL / 华为云 RDS'}
缓存   : Redis
大模型 : 已启用 DeepSeek-V3
采集白名单: ['edu.cn', 'ac.cn']
```

## 5. 部署检查清单

| 检查项 | 验证方式 |
| --- | --- |
| 服务可访问 | 另一台设备或手机 4G 网络打开首页 |
| 数据库已切换 | `/api/health` 中 `database.kind` 为 RDS |
| 数据已迁移 | 首页看板通知总数与本地一致 |
| 解析可用 | 随机挑 3 条通知重新解析，字段与原文依据对应 |
| 问答可用 | 提一个范围内问题得到带引用的回答；提一个范围外问题得到"未收录" |
| 采集可用 | 粘贴一条本校公开通知链接，能成功入库 |
| 缓存生效 | 同一通知重复解析耗时明显下降（或日志显示缓存命中） |
| 密钥未泄露 | `.env` 未提交到仓库，`.gitignore` 已包含 `.env` |

提交前另跑一次 `python scripts/codearts_tools.py all`。这套体检自身经过缺陷注入反向验证
（口径扫描、提示词多余文件、指标重复锚点、端点数、端口误判五类漏报均已修复，见 `docs/test_report.md` T12）；
**体检项数以现场输出为准**，不要把项数写死在材料里。

## 6. 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 「无法访问此页面」且地址是 `http://0.0.0.0:8000/` | `0.0.0.0` 是通配监听地址，不是网址 | 改开 `http://127.0.0.1:8000/`；项目默认已只监听回环 |
| 连接被拒绝 / ERR_CONNECTION_REFUSED | 服务未启动或进程已退出 | 跑 `python run.py --check` 后 `python run.py`，保持终端不关 |
| 8000 被占用 | 上次服务未退出或其他程序占端口 | `APP_PORT=8001 python run.py`，或按体检提示结束占用进程 |
| 首页空白 / 接口 404 | 直接双击打开 HTML，或 Nginx 只代理了静态路径 | 经后端地址访问；Nginx 用 `location /` 整体反代到 8000 |
| 连 RDS 报 `Access denied` | 账号未授权该库或来源 IP 不在白名单 | 检查 RDS 账号权限与安全组 |
| 中文乱码 | 库或表字符集不是 utf8mb4 | 重建库并指定 `utf8mb4_unicode_ci` |
| `llm_available: false` | `LLM_PROVIDER` 仍为 `none` 或密钥为空 | 改 `.env` 后重启进程 |
| 采集返回 400 | 目标域名不在 `CRAWL_ALLOWED_DOMAINS` | 加入白名单，或改用手动录入 |
| 页面能开但接口 404 | Nginx 只代理了静态路径 | 确认 `location /` 整体反代到 8000 |
## 7. 多端访问（Windows / Android / HarmonyOS / PWA）

### 7.1 交付形态

系统是「单 FastAPI 进程 + 响应式 Web」，浏览器即客户端，不额外维护 APK / HAP：

| 资源 | 路径 | 作用 |
| --- | --- | --- |
| PWA 清单 | `/manifest.webmanifest` | 名称、图标、`standalone` 显示模式、AI 助手 / 通知 / 任务三个快捷方式 |
| Service Worker | `/sw.js` | 页面导航网络优先、静态资源缓存优先、`/api/` 不缓存 |
| 图标 | `/static/icons/icon-*.png` | 192 / 512 / maskable 512；由 `python scripts/gen_pwa_icons.py` 重新生成 |
| 健康信息 | `/api/health` 的 `platforms` | 明示 manifest / service worker 路径与支持端 |

### 7.2 局域网调试

```bash
# Windows：双击 start_lan.bat，或命令行
py -3.12 run.py --lan

# Linux / macOS / WSL / Android Termux
./start_lan.sh

# 只体检不启动（排错用）
py -3.12 run.py --check --lan
```

`--lan` 等价于 `--host 0.0.0.0`，并额外探测本机所有局域网 IPv4，
把 `http://192.168.x.x:8000/` 逐条打印出来；手机与电脑连同一 Wi-Fi 后直接打开即可，
不必手工执行 `ipconfig`。Windows 防火墙需允许 Python 或放行 8000 端口。
`/api/system/info` 的 `network` 字段返回同一组地址与 `lan_exposed` 标志，
前端「多端接入」页签据此展示并提供复制按钮。

### 7.3 PWA 安装条件（必须如实告知评审）

- **HTTPS 或 localhost**：Service Worker 只在安全上下文注册。局域网 `http://192.168.x.x`
  可以正常使用页面与接口，但不会启用离线壳；这不是故障。
- 正式公网部署请配置 HTTPS（Nginx + 证书），再验证「添加到主屏幕 / 安装」。
- 鸿蒙 / Android / Windows 的安装入口位置不同，但都识别同一份 manifest；
  华为浏览器、Chrome、Edge 对 `display: standalone` 的支持存在版本差异。
- 离线壳只缓存页面骨架与静态资源，不会缓存解析结果、待办与问答答案；
  离线时会提示「AI 问答需联网」。

### 7.4 状态与验收

| 项目 | 状态 | 证据 |
| --- | --- | --- |
| manifest / sw / 图标路由可访问 | 已就绪 | `python tests/test_smoke.py` 的「多端资源」断言 |
| 响应式断点（桌面 / 平板 / 手机） | 已就绪 | `docs/ui_design.md`、`docs/shot-mobile*.png` |
| 局域网 http 访问页面与接口 | 已就绪 | 本机 127.0.0.1 实测；局域网 IP 待真机验证 |
| 局域网地址自动探测与一键启动 | 已就绪 | `run.py --check --lan` 现场输出局域网 IPv4；`start_lan.bat` / `start_lan.sh` 存在性纳入自检与体检 |
| AI 助手三页签与浏览器巡检 | 已就绪 | 1440 / 390 两档视口：0 console 错误、0 横向溢出；解析实验室 13 字段卡、8 条校验；多端面板 3 地址 / 5 平台 |
| Service Worker 注册状态可观测 | 已就绪 | `localhost` 下 `getRegistration()` 为真；非安全上下文在界面如实说明，不再静默吞错 |
| Android / HarmonyOS 安装为应用 | 待真机验证 | 需要真机 + HTTPS（或 localhost 调试）截图后才可改为已验证 |
| 离线壳实际断网表现 | 待真机验证 | 需在浏览器 DevTools 或真机断网下截图 |

### 7.5 真机验证清单（提交前完成后再改状态）

按顺序执行，每步留一张带时间与设备型号的原图，放入 `11_CodeArts使用记录/` 对应日期目录；
**未完成的项保持「待验证」，不得用桌面浏览器截图替代**。

| # | 平台 | 操作 | 验收点 |
| --- | --- | --- | --- |
| 1 | Windows | 双击 `start_lan.bat`，记录打印的局域网地址 | 体检通过、端口已监听、地址可复制 |
| 2 | Android（Chrome） | 同一 Wi-Fi 打开局域网地址 | 首页、通知中心、AI 助手三页签均可用；无横向滚动；返回手势按视图层级回退 |
| 3 | HarmonyOS（华为浏览器） | 打开同一地址 | 同上；重点看 `-webkit-backdrop-filter`、安全区、16px 输入不缩放 |
| 4 | Android / HarmonyOS | 切到「多端接入」页签 | 安全上下文显示「否（局域网 http）」、离线壳显示未启用且给出原因说明 |
| 5 | 任一桌面浏览器 | `localhost` 下打开同一页签 | 安全上下文「是」、离线壳「已注册」 |
| 6 | 浏览器 DevTools | 切 Offline 后刷新 | 已缓存页面骨架可浏览，`/api/` 返回 503 并有明确提示 |
| 7 | HTTPS 部署后 | 菜单 →「添加到主屏幕 / 安装」 | 独立窗口、图标与 `standalone` 生效；截图记录安装入口 |
## 8. 云资源选型与多平台交付索引

| 主题 | 文档 / 资产 | 说明 |
| --- | --- | --- |
| RDS / DCS 选型、VPC、安全组、成本对比 | `docs/cloud_services.md` | 含负载匹配依据、连接池/超时、与本地自建方案对比 |
| 各平台交付形态与适配要点 | `docs/platform_delivery.md` | Web / PWA / Windows / Android / HarmonyOS / Docker / ECS |
| 云资源开通与应用部署命令 | `deploy/README.md` | Terraform + auto-deploy + 手动 systemd 三条路径 |
| Terraform 资源定义 | `deploy/cloud/terraform/` | VPC、子网、两个安全组、RDS for MySQL、DCS for Redis |
| 容器交付 | `deploy/docker/` | 单进程镜像、健康检查、数据卷 |
| 云端建库 | `scripts/cloud_bootstrap.py` | 建 utf8mb4 库、建表、可选导入样本 |

> 云资源相关状态一律为「代码就绪，待开通验证」；真实连通、公网部署与并发压测完成后，
> 才允许在材料中改为「已验证」并附截图与退出码。
