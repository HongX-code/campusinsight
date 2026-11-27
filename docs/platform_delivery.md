# 多平台交付形态与适配说明

> 状态：Web/PWA、**微信小程序（原生工程）**、Docker、云主机部署脚本**已就绪**；Android/HarmonyOS 的 PWA 安装**待真机验证**；
> 原生 APK / HAP / EXE **未包含**在默认交付内（会引入新工具链，需显式授权）。
> 本文只描述仓库中真实存在的产物与命令，不把"计划"写成"已完成"。

## 1. 平台范围与结论

| 平台 | 交付形态 | 产物 | 状态 |
| --- | --- | --- | --- |
| Web（桌面浏览器） | 响应式 Web | `app/` 源码 + uvicorn/systemd | 已就绪 |
| PWA（Windows / Android / HarmonyOS / iOS） | 可安装 Web 应用 | `manifest.webmanifest`、`sw.js`、192/512/maskable 图标 | 已就绪，真机待验 |
| **微信小程序** | **原生小程序工程** | **`miniprogram/`（5 页 + tabBar 图标）** | **已就绪；未配 AppID 时以游客演示模式运行** |
| **系统日历** | **ICS 订阅** | **`/api/calendar.ics`、`/api/users/{id}/calendar.ics`** | **已就绪** |
| Windows 桌面 | PWA 独立窗口 | 复用 PWA，无额外产物 | 已就绪（安装入口随浏览器） |
| Android | PWA（添加到主屏幕） | 复用 PWA | 待真机验证 |
| HarmonyOS | PWA（添加到桌面） | 复用 PWA | 待真机验证 |
| 容器化 | Docker 单服务 | `deploy/docker/Dockerfile`、`docker-compose.yml` | 已就绪，本机未执行 build |
| 云主机 / 云原生 | ECS + systemd + Nginx + RDS/DCS | `deploy/cloud/*`、`deploy/app/*` | 脚本就绪，待资源验证 |
| 本地开发 | SQLite + 内存缓存 | `run.py`、`start_server.bat/.sh` | 已验证（自检全绿） |

**结论**：以"一套 FastAPI + 响应式 Web + PWA + 微信小程序 + 日历订阅"为主交付，覆盖 Windows、Android、
HarmonyOS 的浏览器与安装场景，并补上微信这条国内学生真实使用的主通路；容器与云主机作为部署形态；
原生安装包不默认提供，避免为比赛引入 Gradle/DevEco/PyInstaller 等额外工具链与维护成本。

## 2. Web 端（桌面浏览器）

- **产物**：仓库源码（`app/`、`config/`、`data/`、`requirements.txt`）。
- **启动**：
  ```bash
  python run.py --check      # 依赖/端口/数据库/页面资源体检
  python run.py --open       # 启动并打开 http://127.0.0.1:8000/
  ```
- **适配要点**：`127.0.0.1` 是浏览器地址；`0.0.0.0` 只是监听通配地址。首页、通知、匹配、
  AI 助手、任务、看板、采集 9 个视图共用同一份 HTML/CSS/JS，无构建步骤。
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

## 4. 微信小程序（原生工程）

- **产物**：`miniprogram/`，原生小程序工程，无第三方框架与构建步骤。
  - `app.json`：5 个页面 + 4 项 tabBar（首页 / 通知 / 匹配 / 我的），图标为 81×81 位图；
  - `app.js`：统一 `request()` 入口（自动附 `x-token`，401 时静默重登并重放一次，
    `retry=false` 防死循环）、`login()`、`ensureLogin()`、`loadStatus()`；
  - 5 个页面：`index`（首屏一次 `/api/mobile/bootstrap` 取全量，不做串行请求）、
    `notices`（分页 + 吸顶筛选）、`notice-detail`（结构化字段 + 办理清单 + 订阅 + 待办）、
    `match`（四档全展示：全部/符合/待核对/不符合）、`mine`（画像、待办三态、设置）。
- **登录**：`POST /api/wechat/login`。配置了 `WECHAT_APPID` / `WECHAT_SECRET` 时走真实
  `jscode2session`；未配置时按 `device_key` 派生稳定账号（`mock_` 前缀），
  **同一设备重登复用同一账号**，功能完整可用，便于无小程序账号时答辩演示。
- **票据**：服务端自签 token（默认 30 天，`WECHAT_TOKEN_TTL_DAYS`），
  **不使用微信 `session_key`**——后者 2 小时过期会导致难以解释的掉登录。
- **适配要点**：
  - tabBar 只接受位图，图标由 `scripts/gen_mp_icons.py` 用 Pillow 生成，与 Web 端
    Lucide 图标保持同一描边风格；界面内不出现 Emoji；
  - 真机调试需把 `miniprogram/app.js` 的 `BASE_URL` 改成本机局域网地址（`start_lan.bat` 会打印）；
  - 微信开发者工具导入 `miniprogram/` 目录即可预览，无需 npm 安装。
- **优点**：学生日常真正会打开的入口；订阅消息能把「截止提醒」推到微信里，不必装 App。
- **局限**：未配置 AppID 时无法发订阅消息（自动降级为站内提醒，界面明确标注）；
  正式发布需主体资质与审核。
- **自检**：`python scripts/check_miniprogram.py`（结构、图标尺寸、JSON 可解析、
  `node --check`、WXML 标签配平、无 Emoji、页面不直连 `wx.request`）；
  `python scripts/e2e_miniprogram.py`（登录→首屏→清单→待办→订阅→冲突→鉴权全链路）。

## 4.1 多机型显示适配（Web 响应式 + 小程序 rpx）

- **问题**：学生用不同手机打开 Web 端时，待办/数据总览页的卡片会「显示不全」——
  右侧被切到屏幕外，必须横向拖动才能看全。
- **根因**：CSS Grid 的 `1fr` 轨道默认最小尺寸是 `auto`（等于内容的 min-content），
  而 `.li-title` 用了 `white-space:nowrap`，把轨道最小宽度顶到 480px+，
  在 375px 的手机上直接溢出；「更多」面板切成 `display:flex` 后没有 `flex-direction:column`，
  6 个 `width:100%` 的条目横向排开也会溢出。
- **修复**：
  - `.card` 加 `min-width:0`、`.grid-2` 改 `repeat(2, minmax(0, 1fr))`，
    让轨道能收缩，长标题交给 `text-overflow:ellipsis` 省略；
  - `.sheet` 显式 `flex-direction:column`；
  - 320px 窄屏顶栏主按钮收为图标按钮（录入入口仍在「更多」面板里）。
- **小程序端**：布局一律用 `rpx`（750rpx = 屏宽，微信自动按机型缩放），
  自检断言 WXSS 不出现裸 `px` 宽度/高度；这从单位层面保证不同手机一致。
- **验证**：`python scripts/check_responsive.py` 用 Playwright 在
  320 / 375 / 390 / 430 / 768 五档宽度逐视图检查 `document.scrollWidth`，
  并展开「更多」面板、详情抽屉确认贴合视口，共 40 项断言全绿。
  静态写法层面的断言同时写进 `scripts/check_frontend.py` 与 `scripts/check_miniprogram.py`。

## 5. 系统日历订阅（ICS）

- **产物**：`GET /api/calendar.ics`（全站截止日）、`GET /api/users/{id}/calendar.ics`
  （只含该用户未完成待办）。
- **设计要点**：
  - 全天事件用 `DTSTART;VALUE=DATE` + `DTEND`（**DTEND 不含当天，故取截止日 +1 天**，
    少这一天事件会整条消失）；两个 `VALARM`：截止前 3 天与当天上午；
  - `UID` 由通知编号 + `CALENDAR_UID_DOMAIN` 生成，**稳定不变**，
    重复订阅不会在日历里产生重复条目；
  - 中文长行按 RFC 5545 折到 75 字节以内，且**折行位置按 UTF-8 边界判断**，
    不会把汉字截成半个（早期版本这里有个静默吞行的 bug，已修并加了断言钉住）。
- **价值**：这是**不需要任何推送权限**的第三条通路——系统日历原生带提醒，
  不依赖微信授权、不依赖浏览器通知，评委现场可当场订阅验证。

## 6. Windows 桌面

- **推荐形态**：用 Edge/Chrome 将 PWA 安装为独立窗口（开始菜单/桌面快捷方式）。
- **产物**：无额外二进制；复用 PWA。
- **适配要点**：窗口尺寸按桌面断点（≥1200 三栏、768–1199 图标栏、<768 底部导航）；
  `start_server.bat` 负责选解释器、体检、启动并打开浏览器。
- **优点**：与 Web 完全同源，更新即生效；无需签名/打包。
- **局限**：仍需本机运行 Python 服务；不是真正的原生 EXE（无托盘、无自动更新）。
- **可选 EXE（未做）**：PyInstaller/Nuitka 打包会新增构建依赖与体积，且需要处理静态资源路径，
  本次未纳入；如比赛要求"双击即用"，需单独授权并增加构建脚本。

## 7. Android

- **默认形态**：PWA（Chrome「添加到主屏幕」）。
- **适配要点**：触控目标 ≥ 38–50px；`touch-action: manipulation` 关闭双击缩放；
  软键盘弹起时输入框不被底部导航遮挡（`.scroll` 预留 96px + safe-area）；
  返回键通过 `pushState/popstate` 在视图间回退。
- **优点**：无需 APK、无需签名与商店审核；与 iOS/鸿蒙共用一套。
- **局限**：不能调用原生能力（推送、相册、文件系统）；部分国产浏览器对 PWA 安装支持不完整。
- **可选 APK（未做）**：需新增 Android 工程（WebView 壳或 Capacitor），引入 Gradle/SDK，
  超出"不引入新工具链"的约束；如确需，建议只做薄壳并复用同一后端。

## 8. HarmonyOS

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

## 9. 容器化（Docker）

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

## 10. 云主机 / 云原生部署（华为云 ECS）

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

## 11. 配置与启动脚本一览

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
| **小程序图标** | **`scripts/gen_mp_icons.py`** | **Pillow 生成 81×81 tabBar 位图（4 组常态/选中）** |
| **小程序静态自检** | **`scripts/check_miniprogram.py`** | **结构 / 图标 / JSON / WXML 配平 / 无 Emoji** |
| **小程序端到端** | **`scripts/e2e_miniprogram.py`** | **登录 → 首屏 → 清单 → 待办 → 订阅 → 冲突 → ICS** |
| **前端一致性自检** | **`scripts/check_frontend.py`** | **DOM id / 视图分发 / SVG symbol / 样式类闭环 / 移动端视口断言** |
| **多机型视口自检** | **`scripts/check_responsive.py`** | **320/375/390/430/768 五档宽度：无横向滚动，面板贴合视口** |
| 清理 | `terraform destroy` / `destroy.py` | 删除云资源（不可恢复） |

## 12. 平台选择建议

| 场景 | 推荐形态 | 理由 |
| --- | --- | --- |
| 答辩现场演示 | Web + 云主机 | 公网可访问，评委可直接打开 |
| **学生日常（国内）** | **微信小程序** | **不用装 App，订阅消息直达微信** |
| 学生日常（无微信） | PWA（Android/HarmonyOS） | 添加到桌面，接近原生体验 |
| **不愿授权推送** | **日历订阅（ICS）** | **系统日历自带提醒，零权限** |
| 教师/管理端 | Web 桌面 | 大屏看板与采集操作更顺手 |
| 快速复现/CI | Docker | 一条命令起环境 |
| 无网络机房 | 本地 + SQLite | 不依赖云资源，规则引擎兜底 |
| 需要应用商店分发 | 原生 APK/HAP（未做） | 需新增工具链与审核，当前不纳入 |

## 13. 华为云三件套的可举证状态（/api/health 单点可见）

"全华为平台可用"必须区分**代码已就绪**与**真实已连通**。系统把这套判断收敛到 `/api/health`
一个端点，评委打开 `http://<host>/api/health` 即可一眼核验，不必分别打三个接口再自己拼结论。

```jsonc
// GET /api/health（离线自检环境示例，未开通云资源时的真实返回）
{
  "status": "ok",
  "database": {"ok": true, "kind": "SQLite（本地）"},
  "cache": {"kind": "内存缓存（单进程）", "keys": 0},
  "active_engine": "rule",
  "huawei": {
    "rds":  {"configured": false, "active": false, "kind": "SQLite（本地）"},
    "dcs":  {"configured": false, "active": false, "kind": "内存缓存（单进程）"},
    "maas": {"configured": false, "active": false, "provider": "none", "model": "DeepSeek-V3"},
    "fallback": {
      "database": "SQLite（本地）",
      "cache": "内存缓存（单进程）",
      "engine": "内置规则引擎（离线兜底）"
    }
  }
}
```

- `configured` 看环境变量（`is_mysql` / `is_redis` / `is_maas`），`active` 看当前实际连接；
  两者分开，杜绝"配了就算连通"。
- `fallback` 段明确写出降级到了什么：未接 RDS 就用 SQLite、未接 DCS 就用内存缓存、
  未接 MaaS 就用规则引擎——**降级不丢功能，且如实可见**。
- 代码侧就绪清单：`app/config.py` 的 `DATABASE_URL` / `REDIS_URL` / `LLM_*` 切换开关、
  `app/database/connection.py` 的 MySQL 连接池与超时参数、`app/services/cache.py` 的 DCS 客户端、
  `app/services/llm_client.py` 的 MaaS OpenAI 兼容适配与熔断，均已在 `tests/test_smoke.py`
  中新增断言（健康检查单点亮出三件套状态、区分配置与连通、离线如实标注降级目标等）。

**真实连通边界**（不得含糊）：

| 组件 | 状态 | 证据 |
| --- | --- | --- |
| ECS / EIP / VPC / 安全组 | **已真实部署并公网验证** | `.auto-deploy/华为云ECS部署报告.real.md`：2026-11-23 部署成功，21 项公网断言通过 |
| RDS for MySQL | 代码与 Terraform 就绪，**未开通** | `deploy/cloud/terraform/rds.tf`；运行态健康检查显示 SQLite |
| DCS for Redis | 代码与 Terraform 就绪，**未开通** | `deploy/cloud/terraform/dcs.tf`；运行态健康检查显示内存缓存 |
| MaaS（DeepSeek-V3） | OpenAI 兼容适配就绪，**未开通** | `app/services/llm_client.py`；运行态 `active_engine=rule` |

开通 RDS/DCS/MaaS 后，按 `deploy/README.md` 传入 `cloud.env` 即可切换，
`/api/health` 的 `huawei.*.active` 会随真实连通自动变为 `true`。

## 14. 待验证清单

1. Android / HarmonyOS 真机安装与全屏表现（需手动截图，标注日期与机型）；
2. 局域网 http 场景下 PWA 安装入口是否存在（预期不启用 SW）；
3. Docker 镜像构建与健康检查（本环境未执行 build）；
4. 云上 RDS/DCS 连通、Nginx/HTTPS、重启后数据持久性；
5. 移动端新截图（6 项导航、引擎状态卡）——现有 `docs/shot-mobile*.png` 为旧版；
6. **微信小程序真机预览与订阅消息实发**——当前为工程静态自检 + 服务端端到端自检，
   **未在真实微信环境里下发过订阅消息**（需 AppID/Secret 与体验版）；未配置时走游客演示模式；
7. **小程序真机 UI 走查**——`pages/` 五个页面按 750rpx 设计稿写就，
   但未在 iOS/Android 真机上逐页核对字体回退与安全区。

以上未验证项在材料中保持"待真机/待资源验证"，不得表述为"已支持/已上线"。