# CodeArts 资产目录

> **重要：本目录里的所有文件都是「方案 / 模板 / 工具」，不是 CodeArts 实操证据。**
> 空白模板或工具脚本出现在答辩材料里，等同于「尚未使用」。团队必须在真实开发中
> 复制模板、填写真实内容、导出真实会话与截图，归档到提交目录 `11_CodeArts使用记录/`。

## 目录内容

| 路径 | 内容 | 由谁维护 |
| --- | --- | --- |
| `prompts/P00_system.md` — `P10_opensource.md` | 11 个独立可复制提示词文件 | **自动生成**，见下方「单一数据源」 |
| `../docs/codearts_prompts_advanced.md` | v2.0 进阶提示词：B0 上下文注入块、A0 执行契约、A1—A10 任务提示词、验收矩阵、已知陷阱 | 开发团队维护，**不参与 `split`** |
| `templates/采纳说明.md` | 说明哪些建议被采纳、哪些没被采纳及原因 | 团队实操后填写 |
| `templates/排错记录.md` | 一次由 CodeArts 辅助定位的真实 Bug 闭环 | 团队实操后填写 |
| `templates/开发日志.md` | 逐日逐次操作台账（日期 / 提示词 / 产出 / 验收 / 截图） | 团队实操后填写 |
| `templates/会话对照表.md` | Prompt 与生成结果的一一对照 | 团队实操后填写 |
| `templates/每日检查清单.md` | 每天收工前 5 项检查 | 团队实操后打勾 |
| `templates/填写示例.md` | 5 类模板的填写颗粒度示范，含「写坏的示范」 | 只读示例，明确标注非证据 |
| `CodeArts实操作战台.html` | 单页执行看板：方法论、就绪度、Day 1—Day 7、11 条短提示词、留痕进度 | 离线打开即可用 |

配套工具（不在本目录）：`scripts/codearts_tools.py`
（`doctor` 环境体检 / `bootstrap` 一键骨架 / `split` / `index` / `verify` / `all`）、
`scripts/ci_gate.py`（9 阶段门禁）、`../../申报材料/export_pdf.py`（PPTX → PDF 并校验页数）。
完整方案见 `docs/codearts_prompts.md`；命令里的 `python` 是代称，按 `doctor` 输出替换（§1.0）。

## 单一数据源

提示词正文**只在** `docs/codearts_prompts.md` §2 / §3 维护，`prompts/` 下的文件由脚本自动拆出：

```bash
python scripts/codearts_tools.py split          # 主文档改动后重新生成
python scripts/codearts_tools.py split --check  # 提交前确认两边一致
```

手改拆分结果会在 `--check` 时被判为漂移；`ci_gate.py` 也会跑这一项。

## 推荐执行顺序

1. `python scripts/codearts_tools.py doctor` —— 确认解释器前缀、node 与交付件占用（§1.0）。
2. `python scripts/codearts_tools.py bootstrap` —— 一键生成 `11_CodeArts使用记录/` 骨架；
   `bootstrap --check` 随时核对 18 个文件是否齐全。
3. `python scripts/codearts_tools.py index` —— 确认代码库索引覆盖全部 Python 文件；
   缺文件则先在 CodeArts 客户端重新打开工作区并全量重建索引（见主文档 §1.1）。
4. 打开本目录的 `CodeArts实操作战台.html`，按 Day 1—Day 7 时间轴选当天提示词；
   也可以直接把 `prompts/` 下对应文件整份复制进会话（主文档 §4.2 为准）。
5. **换人接手，或推进云端 / 留痕 / 提交前收口**：改读 `../docs/codearts_prompts_advanced.md`，
   把 §2 的 B0 整段粘进新会话，再复制对应 A 编号的提示词正文并附 A0 执行契约；
   改完按 §5 验收矩阵逐条核对。A 系列不参与 `split`，11 份 P 系列口径不变。
6. 每条提示词单开一个新会话，先让智能体出方案，确认后再改代码。
7. 跑该条提示词的验收命令，再跑 `python scripts/ci_gate.py`（9 阶段）；
   涉及口径时补跑 `python scripts/evidence.py --collect`。
8. 在 `11_CodeArts使用记录/` 填写当天的采纳说明、开发日志与每日检查清单，保存会话导出与截图；
   填写颗粒度参照 `templates/填写示例.md`（示例，非证据）。
9. 提交前跑 `python scripts/codearts_tools.py all`、`bootstrap --check` 与 `ci_gate.py`。

## 状态口径

| 项 | 状态 |
| --- | --- |
| 提示词集、逐日排期、常驻约束、留痕模板与填写示范、单页作战台 | 已就绪 |
| v2.0 进阶提示词（B0 / A0 / A1—A10）、常驻上下文改为指向现场快照 | 已就绪（仍非实操证据） |
| 环境体检 doctor、一键骨架 bootstrap（18 个空模板文件）、9 阶段门禁 | 已就绪 |
| **auto-deploy 真实排错链路归档**（`11_CodeArts使用记录/08_排错记录.md`、`09_开发日志.md`，含 3 张云上截图） | **已完成（基于真实工程记录，非客户端会话导出）** |
| CodeArts **客户端**真实会话导出、七类编号过程截图 | 待团队实操 |
| 采纳说明的真实内容（排错记录 / 开发日志已填真实内容） | 部分完成，采纳说明待补 |
| 华为云 **ECS/EIP/VPC** 部署与公网验证 | **已完成**（2026-11-23，21 项断言，见 `.auto-deploy/华为云ECS部署报告.real.md`） |
| 华为云 RDS / Redis / MaaS 开通与连通截图 | 待资源到位（仅代码与 Terraform 就绪，运行态为 SQLite + 规则引擎） |
| 云端并发压测、真人计时实验 | 待执行 |

> 说明：`11_CodeArts使用记录/08_排错记录.md`、`09_开发日志.md` 记录的是 CodeArts `auto-deploy`
> 技能驱动的真实部署（15 次尝试、SSH 双出口排错、续跑成功）与带时间戳的工程事件，
> 有 `.auto-deploy/` 原始报告与公网截图支撑，**可作为「用 CodeArts 做过开发」的实证**；
> 但它不是 CodeArts 客户端的逐句会话导出，二者不可混为一谈。

在「客户端会话导出 / 七类编号截图 / RDS-DCS-MaaS 连通 / 真人实验」完成前，
`docs/design_compliance.md`、`README.md`、PPT 第 16 页与测试报告中的这些项仍保持「待补齐」，不得提前改成已完成。
