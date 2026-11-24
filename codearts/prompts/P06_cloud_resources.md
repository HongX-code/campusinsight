# P6　华为云资源快速开通（Skills 关联 RDS / Redis / MaaS）

> 自动生成：由 `scripts/codearts_tools.py split` 从 `docs/codearts_prompts.md` 拆出。
> **请勿手改本文件**；要改提示词请改主文档后重新执行 `split`。
> 完整说明、执行顺序、红线与留痕模板见 `docs/codearts_prompts.md`（P6）。

**期望产出**：资源清单、四项对照表、5 个控制台动作。
**验收**：对照表中每个环境变量都能在 `app/config.py` 找到；`.env.example` 与之一致。
**留痕**：截图 #6「云资源开通」——控制台资源列表 + 连通性脚本输出（真实开通后补）。
**人工复核**：凭证类信息绝不入库、不进文档、不进截图；截图前遮挡账号 ID 与密钥。

## 提示词正文（整段复制到 CodeArts）

```text
请协助准备「高校智讯」接入华为云资源的开通清单与配置对照，不要假设资源已经开通。

依据：app/config.py 的全部环境变量、app/database/init.sql、app/services/cache.py、
app/services/llm_client.py、.env.example、docs/deployment.md。

任务：
1. 列出需要开通的资源及最小规格建议：RDS for MySQL、DCS for Redis、MaaS 大模型推理、
   ECS / Flexus 应用服务器；说明每一项对应代码里的哪个开关与降级行为；
2. 输出一张「控制台操作 → 环境变量 → 代码行为 → 验证命令」对照表；
   环境变量只写键名，密钥值一律留占位符，绝不写入任何真实凭证或编造的账号信息；
3. 给出资源开通后的连通性自检步骤（对应 docs/deployment.md §4 的脚本），
   以及每一项「成功 / 失败」分别应该如何记录；
4. 说明未配置时的降级路径（Redis → 进程内缓存、MaaS → 规则引擎、RDS → 本地 SQLite），
   确保演示不因外网不通而中断；
5. 提示可能产生的费用与安全组 / 白名单 / 采集域名白名单配置要点。

只做清单与配置对照，不修改业务代码，不写「已开通 / 已连通」。
最后列出需要团队在控制台实际执行并截图的 5 个动作。
```

---

投喂前请先读 `docs/codearts_prompts.md` §1（尤其是 §1.1 的代码库索引重建）与 §2 的 P0 常驻约束；
本条对应留痕要求见主文档 §4.2 逐日排期与 §5 留痕方案。
