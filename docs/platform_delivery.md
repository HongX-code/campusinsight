# 多平台交付形态与适配说明

> 状态：Web/PWA、Docker、云主机部署脚本**已就绪**；Android/HarmonyOS 的 PWA 安装**待真机验证**；
> 原生 APK / HAP / EXE **未包含**在默认交付内（会引入新工具链，需显式授权）。
> 本文只描述仓库中真实存在的产物与命令，不把"计划"写成"已完成"。

## 1. 平台范围与结论

| 平台 | 交付形态 | 产物 | 状态 |
| --- | --- | --- | --- |
| Web（桌面浏览器） | 响应式 Web | `app/` 源码 + uvicorn/systemd | 已就绪 |
| PWA（Windows / Android / HarmonyOS / iOS） | 可安装 Web 应用 | `manifest.webmanifest`、`sw.js`、192/512/maskable 图标 | 已就绪，真机待验 |
| Windows 桌面 | PWA 独立窗口 | 复用 PWA，无额外产物 | 已就绪（安装入口随浏览器） |
| Android | PWA（添加到主屏幕） | 复用 PWA | 待真机验证 |
| HarmonyOS | PWA（添加到桌面） | 复用 PWA | 待真机验证 |
| 容器化 | Docker 单服务 | `deploy/docker/Dockerfile`、`docker-compose.yml` | 已就绪，本机未执行 build |
| 云主机 / 云原生 | ECS + systemd + Nginx + RDS/DCS | `deploy/cloud/*`、`deploy/app/*` | 脚本就绪，待资源验证 |
| 本地开发 | SQLite + 内存缓存 | `run.py`、`start_server.bat/.sh` | 已验证（自检 120 项） |

**结论**：以"一套 FastAPI + 响应式 Web + PWA"为主交付，覆盖 Windows、Android、HarmonyOS
的浏览器与安装场景；容器与云主机作为部署形态；原生安装包不默认提供，避免为比赛引入
Gradle/DevEco/PyInstaller 等额外工具链与维护成本。

## 2. Web 端（桌面浏览器）

- **产物**：仓库源码（`app/`、`config/`、`data/`、`requirements.txt`）。
- **启动**：
  ```bash
  python run.py --check      # 依赖/端口/数据库/页面资源体检
  python run.py --open       # 启动并打开 http://127.0.0.1:8000/
  ```
- **适配要点**：`127.0.0.1` 是浏览器地址；`0.0.0.0` 只是监听通配地址。首页、通知、匹配、
  AI 助手、任务、看板、采集 7 个视图共用同一份 HTML/CSS/JS，无构建步骤。
- **优点**：零安装、更新即时、调试成本低；与云端同一份代码。
- **局限**：必须有服务端进程；离线不可用（PWA 缓存仅覆盖页面壳）。
- **适用**：开发、答辩演示、机房桌面浏览器。

## 3. PWA（Windows / Android / HarmonyOS / iOS）

- **产物**：
  - `/manifest.webmanifest`：`display=standalone`、主题色、3 个快捷方式（AI 助手 / 通知 / 任务）；
  - `/sw.js`：导航网络优先 + 缓存回退；静态资源缓存优先并后台更新；`/api/` 不缓存；
  - `app/static/icons/`：192、512、maskable 512。
- **安装**：
  | 端 | 入口 |
  | --- | --- |
  | Windows（Edge/Chrome） | 地址栏右侧「安装」图标 |
  | Android（Chrome） | 菜单 →「添加到主屏幕」 |
  | HarmonyOS（华为浏览器） | 菜单 →「添加到桌面」 |
  | iOS（Safari） | 分享 →「添加到主屏幕」 |
- **适配要点**：
  - Service Worker 只在 **HTTPS 或 localhost** 注册；局域网 `http://192.168.x.x` 页面可用但不启用离线壳；
  - 应用内「AI 助手 → 多端接入」页签实时显示当前地址、安全上下文、Service Worker 注册状态、
    服务端监听地址与各平台安装入口，避免把"局域网 http 不可安装"误判为程序故障；
  - 移动端底部导航 6 项（含「更多」动作面板）、独立搜索入口、`safe-area-inset-*`、16px 表单字号、中文输入法 Enter 防误发；
  - 视图写入 URL hash，Android/HarmonyOS 返回手势按页面层级回退；
  - 离线时显示提示条，AI 问答明确提示需联网。
- **优点**：一次实现覆盖多端；可安装、独立窗口、有离线壳；无需应用商店审核。
- **局限**：无原生推送/后台任务/文件系统等能力；不同浏览器对安装入口与 standalone 的支持存在版本差异；
  局域网 http 场景无法验证离线能力。
- **适用**：比赛演示、学生手机快速访问、不想维护多套客户端的团队。

## 4. Windows 桌面

- **推荐形态**：用 Edge/Chrome 将 PWA 安装为独立窗口（开始菜单/桌面快捷方式）。
- **产物**：无额外二进制；复用 PWA。
- **适配要点**：窗口尺寸按桌面断点（≥1200 三栏、768–1199 图标栏、<768 底部导航）；
  `start_server.bat` 负责选解释器、体检、启动并打开浏览器。
- **优点**：与 Web 完全同源，更新即生效；无需签名/打包。
- **局限**：仍需本机运行 Python 服务；不是真正的原生 EXE（无托盘、无自动更新）。
- **可选 EXE（未做）**：PyInstaller/Nuitka 打包会新增构建依赖与体积，且需要处理静态资源路径，
  本次未纳入；如比赛要求"双击即用"，需单独授权并增加构建脚本。

## 5. Android

- **默认形态**：PWA（Chrome「添加到主屏幕」）。
- **适配要点**：触控目标 ≥ 38–50px；`touch-action: manipulation` 关闭双击缩放；
  软键盘弹起时输入框不被底部导航遮挡（`.scroll` 预留 96px + safe-area）；
  返回键通过 `pushState/popstate` 在视图间回退。
- **优点**：无需 APK、无需签名与商店审核；与 iOS/鸿蒙共用一套。
- **局限**：不能调用原生能力（推送、相册、文件系统）；部分国产浏览器对 PWA 安装支持不完整。
- **可选 APK（未做）**：需新增 Android 工程（WebView 壳或 Capacitor），引入 Gradle/SDK，
  超出"不引入新工具链"的约束；如确需，建议只做薄壳并复用同一后端。

## 6. HarmonyOS

- **默认形态**：PWA（华为浏览器「添加到桌面」；HarmonyOS NEXT 浏览器按 PWA 处理）。
- **适配要点**：
  - `viewport-fit=cover` + `env(safe-area-inset-*)` 适配全面屏手势区；
  - 字体使用系统字体回退（`HarmonyOS Sans` 未显式声明，依赖 `system-ui`/`Noto Sans SC`）；
  - 不使用仅 Chromium 支持的实验 API；Service Worker 注册失败时静默降级；
  - 输入框 16px，避免聚焦缩放；中文输入法组合态不触发发送。
- **优点**：与 Android/iOS 同一份 PWA；在鸿蒙浏览器中可获得接近原生的全屏体验。
- **局限**：无法使用鸿蒙原生能力（卡片、分布式、推送）；不同系统版本的浏览器内核差异需要真机回归。
- **可选 HAP（未做）**：需 DevEco Studio + ArkTS 工程 + Web 组件，属于另一套交付物；
  本次不生成，避免出现"有工程但未验证"的空壳。

## 7. 容器化（Docker）

- **产物**：`deploy/docker/Dockerfile`（python:3.12-slim + 单进程 uvicorn）、`docker-compose.yml`、
  根目录 `.dockerignore`。
- **启动**：
  ```bash
  docker compose -f deploy/docker/docker-compose.yml up -d --build
  docker compose -f deploy/docker/docker-compose.yml logs -f
  ```
- **配置**：`DATABASE_URL` / `REDIS_URL` / `LLM_*` 通过宿主机环境变量注入；不写入镜像。
  未配置数据库时回落 SQLite，数据挂 `zhixun-data` 卷。
- **适配要点**：容器内监听 `0.0.0.0:8000`（这是容器内正确用法）；健康检查打 `/api/health`；
  单进程 `--workers 1`，与项目架构一致。
- **优点**：环境一致、易回滚、易在任意云主机运行；镜像即交付物。
- **局限**：镜像体积（依赖较多）；SQLite 卷不适合多副本；不做集群编排。
- **适用**：快速在陌生环境复现、CI 构建、云端单机部署。

## 8. 云主机 / 云原生部署（华为云 ECS）

- **产物**：
  - `deploy/cloud/terraform/`：VPC、子网、安全组、RDS、DCS；
  - `deploy/cloud/provision.sh`：一键开通并生成 `cloud.env`；
  - `deploy/app/deploy_app.sh` + `gaoxiao-zhixun.service` + `nginx-gaoxiao-zhixun.conf`：手动部署路径；
  - `.codeartsdoer/skills/auto-deploy/`：单 ECS 自动部署技能（推荐路径）。
- **部署**：先 Terraform 开通数据服务，再用 auto-deploy 创建 ECS 并部署应用（复用已有 VPC/子网），
  注入 `DATABASE_URL` / `REDIS_URL`。
- **适配要点**：安全组只开 22/80/443；数据库/缓存只走私网；Nginx 反代 8000 并对 `/static` 做缓存；
  systemd `EnvironmentFile` 注入密钥；`/api/health` 验证数据库类型。
- **优点**：公网可访问、与本地同一套代码、可绑定域名与 HTTPS。
- **局限**：单机无自动扩缩；ECS 故障即停服（除非另做多机 + ELB，超出当前约束）；
  RDS/DCS 持续计费，演示结束必须销毁。
- **适用**：答辩公网演示、比赛要求的"云上可运行"。

## 9. 配置与启动脚本一览

| 场景 | 脚本 / 文件 | 说明 |
| --- | --- | --- |
| Windows 本地 | `start_server.bat` | 选解释器 → 体检 → 启动并打开浏览器 |
| 局域网调试（Windows） | `start_lan.bat` | 监听 `0.0.0.0`，打印可复制的局域网地址 |
| 局域网调试（Linux/macOS） | `start_lan.sh` | 同上，供手机/平板访问 |
| Linux/macOS/Termux | `start_server.sh` | 默认回环监听；`APP_HOST=0.0.0.0` 可供手机访问 |
| 仅体检 | `python run.py --check` | 依赖、端口、数据库、页面与多端资源 |
| 容器 | `deploy/docker/docker-compose.yml` | 单服务 + 数据卷 |
| 云资源 | `deploy/cloud/provision.sh` | Terraform 开通 RDS/DCS |
| ECS 手动部署 | `deploy/app/deploy_app.sh` | 建库、建表、导入、systemd、可选 Nginx |
| ECS 自动部署 | `auto_deploy.py`（技能） | 创建 ECS、上传代码、启动、健康检查 |
| 数据初始化 | `scripts/cloud_bootstrap.py` | MySQL 建库 + 建表 + 可选样本 |
| 清理 | `terraform destroy` / `destroy.py` | 删除云资源（不可恢复） |

## 10. 平台选择建议

| 场景 | 推荐形态 | 理由 |
| --- | --- | --- |
| 答辩现场演示 | Web + 云主机 | 公网可访问，评委可直接打开 |
| 学生日常使用 | PWA（Android/HarmonyOS） | 添加到桌面，接近原生体验 |
| 教师/管理端 | Web 桌面 | 大屏看板与采集操作更顺手 |
| 快速复现/CI | Docker | 一条命令起环境 |
| 无网络机房 | 本地 + SQLite | 不依赖云资源，规则引擎兜底 |
| 需要应用商店分发 | 原生 APK/HAP（未做） | 需新增工具链与审核，当前不纳入 |

## 11. 待验证清单

1. Android / HarmonyOS 真机安装与全屏表现（需手动截图，标注日期与机型）；
2. 局域网 http 场景下 PWA 安装入口是否存在（预期不启用 SW）；
3. Docker 镜像构建与健康检查（本环境未执行 build）；
4. 云上 RDS/DCS 连通、Nginx/HTTPS、重启后数据持久性；
5. 移动端新截图（6 项导航、引擎状态卡）——现有 `docs/shot-mobile*.png` 为旧版。

以上未验证项在材料中保持"待真机/待资源验证"，不得表述为"已支持/已上线"。