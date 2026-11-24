# 高校智讯 · 华为云 ECS 部署报告

> 结论：项目已真实部署到华为云 ECS 并通过公网验证（2026-11-23 完成）。
> 部署工具为工程内 CodeArts `auto-deploy` 自动化脚本（与 CodeArts 插件同一套华为云 API 通道）。
>
> **说明：本报告已做脱敏处理。** 公网 IP、实例 ID、VPC/子网/安全组 ID、私钥路径等
> 属于账号私有信息，统一用 `<占位符>` 表示。完整原始记录仅保存在本地
> `.auto-deploy/`（该目录已列入 `.gitignore`，不随仓库发布）。
> 复现部署时，下列所有占位符都会由 `auto_deploy.py` 自动生成并写回本地运行态。

## 1. 本次部署概览

| 项目 | 值 |
| --- | --- |
| 状态 | 已部署、`service_state: running`、verify 成功 |
| 公网地址 | `http://<ECS_PUBLIC_IP>:8000/` |
| 健康检查 | `http://<ECS_PUBLIC_IP>:8000/api/health` |
| 区域 | cn-north-4（华北-北京四） |
| ECS 实例 ID | `<ECS_INSTANCE_ID>` |
| 实例镜像 | Ubuntu 22.04 server 64bit |
| 公网 IP / 私网 IP | `<ECS_PUBLIC_IP>` / `<ECS_PRIVATE_IP>` |
| VPC / 子网 | `<VPC_ID>` / `<SUBNET_ID>` |
| 安全组 | `<SECURITY_GROUP_ID>` |
| 部署策略 | native（systemd + Python 3.11 venv，非容器） |
| 运行时 | Python 3.11（deadsnakes PPA 安装，解决 SQLAlchemy 2.1.8 需要 ≥3.11 的约束） |
| 受管云资源 | 12 个（terraform.tfstate，仅存在于本机 `.auto-deploy/terraform/`） |

## 2. 部署过程（真实执行记录）

本次重新部署共经历 4 个阶段，均为真实命令与真实输出，未编造任何结果：

1. **首次尝试被账号限制拦截**：`terraform apply` 阶段创建 VPC/子网/安全组成功，但分配 EIP 报
   `VPC.0502 Tenant status is op_restricted`，创建密钥对报
   `KPS.9004 用户账号受限。检查是否违规或余额不足。`。
   已用 `destroy.py` 清理该次残留资源（`6 destroyed`，REMAINING_RESOURCES=0），无遗留计费。
2. **用户充值后复核**：凭证 AK/SK/REGION 均可读（长度 20/40，仅记录长度不打印值），
   Terraform v1.17.2 可用，tfstate 为 0 资源。
3. **根因修复（两处工具缺陷）**：
   - `analyze.sh` 的通用正则误把 auto-deploy 工具自身示例文件里的 `8081 / /health / /myapp`
     当作项目配置，每次部署都覆盖手动修正。已加 evidence-first override：
     识别到 `app.main:app` 的 FastAPI 项目时强制 `listen_port=8000`、`health_endpoint=/api/health`、
     `context_path=/`。重跑分析确认 `8000 / /api/health / /`。
   - 安全组 `ssh_backup` 规则会被 plan/provision 阶段重新生成的 terraform 文件覆盖，导致
     SSH 出站被挡、`ssh_wait` 300 秒超时。解决办法是在 provision 之后、
     `--resume-from deploy` 之前增量补回该规则（`Plan: 1 to add, 0 to change, 0 to destroy`），
     SSH 验证输出 `SSH_AUTH_OK / root / Linux 5.15.0-186-generic x86_64`。
4. **续跑成功**：`auto_deploy.py ... --resume-from deploy --app-port 8000 --yes`，
   `status: recovered`、`EXITCODE=0`，所有阶段 success：
   `install_runtime`（644s，Python 3.11 + 依赖）、`start_service`（创建了 systemd 开机自启 symlink）、
   `deploy`（strategy native completed）、`verify`（service_state running）。

## 3. 公网验证与数据灌入（真实输出）

部署完成后，经公网 HTTP API 完成端到端验证，并灌入真实爬虫数据：

| 验证项 | 结果 |
| --- | --- |
| `GET /api/health` | 200，`status: ok`，SQLite 数据库可用，`app: 高校智讯`，六端支持 |
| 灌入前统计 | `total_notices: 0`（新库为空） |
| 第一批入库 | `POST /api/sources/import` whut_gd_yjsgl limit 5 → `created:5, analyzed:5, failed:[]` |
| 第二批入库 | 其余 6 个武汉理工源 limit 8 → `created:47, skipped:1, analyzed:47, failed:[]` |
| 灌入后统计 | `total_notices: 52, analyzed: 52, unanalyzed: 0, need_review: 9, overdue: 24` |
| 分类/体裁分布 | 学科竞赛/奖助学金/学术讲座等 9 类；事务通知 25 / 新闻资讯 23 / 公示公告 4 |
| `GET /api/notices` | 200，返回 52 条中文通知完整正文 |
| `POST /api/qa/ask` | 200，结构化答案带依据编号，`intent: deadline` 正常识别 |

数据来源为武汉理工大学研究生院与新闻经纬的 7 个公开栏目，全部经 `--auto-analyze` 完成结构化解析。

## 4. 运行中的运维命令

SSH 私钥由 `auto_deploy.py` 生成在本机 `.auto-deploy/keys/` 下（**不随仓库发布**）。
Windows 上先用 `icacls` 收紧权限再用 `ssh`。

```bash
# 查看服务状态
systemctl status gaoxiao-zhixun
# 重启 / 停止
systemctl restart gaoxiao-zhixun
systemctl stop gaoxiao-zhixun
# 看日志
journalctl -u gaoxiao-zhixun -f
# 应用目录
cd /opt/auto-deploy/gaoxiao-zhixun/current
```

## 5. 停止计费（销毁资源）

确认不再需要演示环境时，在工程目录执行（会销毁 terraform.tfstate 中全部 12 个资源）：

```bash
python .codeartsdoer/skills/auto-deploy/scripts/destroy.py --workdir .auto-deploy/terraform --auto-approve
```

销毁前应先把云端数据库备份到本地（如需保留演示数据）：

```bash
scp -i <私钥路径> root@<ECS_PUBLIC_IP>:/opt/auto-deploy/gaoxiao-zhixun/current/data/gaoxiao_zhixun.db <本地路径>
```

## 6. 安全说明

- 安全组 SSH（22）仅放行部署时确认的出口 IP（`<DEPLOYER_EGRESS_IP>/32`），
  不做 `0.0.0.0/0` 全放；应用端口 8000 对 `0.0.0.0/0` 开放以便评委访问；出网全放行。
- 访问密钥（AK/SK）仅存在于本机用户级环境变量与 terraform 运行时环境，未写入任何工程文件。
- 本机为双线路/NAT 出口，HTTP 探测出口与 SSH 实际出口可能不同，
  这是本次需要 `ssh_backup` 规则的根本原因；后续重装系统或换网络时请留意。
- 仓库内所有 8 类凭证（AK/SK、数据库口令、LLM Key、私钥、tfstate）均已由 `.gitignore` 排除。

## 7. 上线功能全面验证（2026-11-23）

部署完成后对公网站点做了一轮覆盖全部核心链路的端到端验证，全部真实请求、真实输出：

| 链路 | 验证内容 | 结果 |
| --- | --- | --- |
| 静态与入口 | 首页 200、JS/CSS 200、健康检查 200 | 通过 |
| AI 助手 | 已精简为「智能问答 / AI 解析实验室」双页签，无多端接入残留 | 通过 |
| 数据看板 | `total_notices=78 / analyzed=78`，分类、体裁、趋势分布齐全 | 通过 |
| 通知中心 | 列表分页返回正常 | 通过 |
| 用户与匹配 | 演示用户 3 人，`/api/users/1/matches` 返回 55 条 | 通过 |
| 智能问答 | 6 个示例问题全部 `hit=true / mode=structured`，华为 ICT 截止日命中 2026-11-15、不再误命中 MPAcc | 通过 |
| AI 解析实验室 | 新闻稿推文判为 `news / 不可办理`；无冒号地点「地点在南湖校区…」抽取为「南湖校区大学生活动中心」 | 通过 |
| 采集中心 | 增量预览可用（自动跳过已收录并继续翻页找新内容） | 通过 |
| 真实浏览器交互 | Edge headless 实测点击示例问题→出现 3 个追问按钮→点击追问成功续问，0 console 错误 | 通过 |

合计 **21 项接口/功能断言全部通过**，浏览器追问交互复验通过；本地 `codearts_tools.py verify` 42/42 全绿。
结论：部署在华为云上的功能**正常可用、链路完整**。

## 8. 重新开机与版本同步（2026-11-24）

### 8.1 故障现象与定位

上午发现公网 `<ECS_PUBLIC_IP>:8000` 与 `:22` 全部不可达，HTTP 健康检查超时、ICMP 不通。
排查过程与结论：

| 步骤 | 命令/操作 | 输出 | 判断 |
| --- | --- | --- | --- |
| 端口探测 | `Test-NetConnection <ECS_PUBLIC_IP> -Port 8000/22` | 均 `False` | 非单点端口问题 |
| 本机出口 IP | `myip.ipip.net` | `<DEPLOYER_EGRESS_IP>` | 出口在安全组放行名单内，排除网络出口被挡 |
| 刷新云状态 | `terraform apply -refresh-only -auto-approve` | `Resources: 0 added, 0 changed, 0 destroyed` | 资源未丢失、未被销毁 |
| 读实例真实状态 | `terraform show -json` | `status = SHUTOFF`、`updated_at = 2026-11-24T02:37:50Z` | **实例被关机** |
| EIP 状态 | 同上 | `status = BOUND`、`associate_id` 存在 | 弹性 IP 仍绑定，未释放 |

结论：不是欠费停服、不是安全组、不是代码问题，而是**云主机被关机**。

### 8.2 开机操作（真实执行）

provider `huaweicloud 1.90.0` 的 `power_action` 只接受 `[ON OFF REBOOT FORCE-OFF FORCE-REBOOT]`，
先误写 `"start"` 被 plan 拒绝（`expected power_action to be one of [...]`），改为 `"ON"` 后通过：

```hcl
# .auto-deploy/terraform/ecs.tf
resource "huaweicloud_compute_instance" "app" {
  # ...（其余配置不变）
  power_action = "ON"
}
```

```bash
terraform plan   # Plan: 0 to add, 1 to change, 0 to destroy（仅 + power_action = "ON"）
terraform apply -auto-approve
# → Modifications complete after 32s
# → Apply complete! Resources: 0 added, 1 changed, 0 destroyed.
```

**没有重建实例、没有更换 IP、磁盘数据与数据库全部保留。**

### 8.3 开机后验证

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| SSH 端口 | `Test-NetConnection :22` | OPEN |
| 应用端口 | `Test-NetConnection :8000` | OPEN |
| 健康检查 | `GET /api/health` | `HTTP 200`，`status=ok`，`database.ok=true` |
| 服务状态 | `systemctl is-active gaoxiao-zhixun` | `active`（且 `enabled`，随开机自启） |

### 8.4 代码同步（本地 → 云端）

此前有两批改动只在本地、未上线：详情抽屉的视图刷新修复，以及**角色账号登录改造**。
先比对两端 md5 再上传，避免全量覆盖误删云端数据：

```text
本地文件 60  云端文件 58  相同 53
[修改] 5   app/static/css/style.css、app/static/js/app.js、app/templates/index.html、
            scripts/codearts_tools.py、tests/test_smoke.py
[新增] 2   scripts/publish_gitee.py、scripts/publish_github.py
[云端独有] 0
```

逐个 `scp` 上传后重新生成清单：**本地 60 / 云端 60 / 相同 60**，完全一致。
随后 `chown -R deploy:deploy`（scp 以 root 落地会破坏服务属主）、清理 `__pycache__`、`systemctl restart`。

### 8.5 上线后复验（2026-11-24）

云端接口断言（直打公网 API）：

| 分组 | 断言 | 结果 |
| --- | --- | --- |
| 健康 | `/api/health` 200、`status=ok`、`database.ok=true`、六端声明齐全 | 通过 |
| 角色账号 | `/api/users` 200、3 个角色（张明/李雨/王浩）、姓名完整 | 通过 |
| 通知 | `/api/notices?page=1&size=10` 200、`total=78`、返回 10 条 | 通过 |
| 匹配 | `/api/users/1/matches` 200、含三档结论与逐条理由 | 通过 |
| 日程/待办 | `/api/users/1/agenda` 200、`/api/users/1/tasks` 200 | 通过 |
| 看板 | `/api/dashboard/stats` 200（overview/categories/engines/modes/genres/deadlines） | 通过 |
| 采集 | `/api/sources` 200 | 通过 |
| AI | `/api/ai/status` 200、`/api/qa/ask` 200 返回非空答案（2341 字符） | 通过 |
| 待办闭环 | POST 创建 → PATCH 推进 → DELETE 清理，全部 200 | 通过 |
| 静态资源 | style.css(80KB)/app.js(85KB)/manifest/sw.js 全部 200 | 通过 |

合计 **29/29 全通过**。

公网真实浏览器端到端（Edge headless 直连公网地址）：

| 步骤 | 断言 | 结果 |
| --- | --- | --- |
| 首页 | 可访问，标题正确 | 通过 |
| 未登录态 | 顶栏显示「登录」，不默认替用户选人 | 通过 |
| 登录弹窗 | 点击头像打开，3 张角色卡片渲染，姓名正确 | 通过 |
| 选择角色 | 顶栏切换为「张明」，弹窗自动关闭 | 通过 |
| 匹配页 | 显示「当前登录：张明 · 大三 · 信息管理与信息系统」，渲染 15 条匹配 | 通过 |
| 登录持久化 | 刷新页面后登录态保持 | 通过 |
| 通知/看板 | 列表 12 条、看板统计内容完整 | 通过 |
| AI 助手 | 双页签 `chat,lab`（无「多端接入」），解析实验室可切换 | 通过 |
| 注销 | 清除登录态后回到未登录 | 通过 |
| 运行质量 | 0 console 错误、0 网络请求失败 | 通过 |

合计 **17/17 全通过**。截图见 `docs/screenshots/cloud-01-home.png` ～ `cloud-05-qa.png`。

### 8.6 本次结论

- 云主机已重新开机，公网 `http://<ECS_PUBLIC_IP>:8000/` 恢复访问，数据零丢失。
- 本地与云端代码完全一致（60/60 文件 md5 相同），角色账号登录改造已正式上线。
- 接口 29/29、浏览器 17/17 全部通过，服务 `enabled` 随开机自启。
- **计费状态**：ECS 与 EIP 均在运行并计费；长期不用请按第 5 节执行 `terraform destroy` 释放。
