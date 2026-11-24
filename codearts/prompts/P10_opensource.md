# P10　开源交付前检查（决赛要求）

> 自动生成：由 `scripts/codearts_tools.py split` 从 `docs/codearts_prompts.md` 拆出。
> **请勿手改本文件**；要改提示词请改主文档后重新执行 `split`。
> 完整说明、执行顺序、红线与留痕模板见 `docs/codearts_prompts.md`（P10）。

**期望产出**：六类内容核对表 + 敏感信息清单 + TODO。
**验收**：按清单逐项打勾；确认 `git status` 中无 `.env` / `.db` / 索引缓存。
**留痕**：截图归入 12_华为云部署记录 或开源附件。
**人工复核**：真实通知快照涉及第三方站点内容，开源前由队长确认授权与署名口径。

## 提示词正文（整段复制到 CodeArts）

```text
入围中国总决赛 / 全球总决赛需将作品开源至 Gitee 或 GitHub。请做一次开源前检查，只报告不改代码。

检查项：
1. 是否包含赛题要求的六类内容：项目源代码、依赖环境（requirements.txt）、部署指导
   （docs/deployment.md）、使用文档（README.md）、开源许可证说明（LICENSE）、
   Demo 截图或演示视频链接（docs/shot-*.png、docs/video_script.md）；
2. 敏感信息扫描：.env、真实密钥、账号 ID、内网地址、团队个人隐私信息、
   .codeartsdoer/ 下的索引缓存与 file-index.db、本地 SQLite 数据文件是否被 .gitignore 排除；
3. 42 份真实通知快照的来源与合规标注是否完整（docs/dataset.md），
   是否需要在开源版本中对抓取来源做说明；
4. README 是否能让一个没参与开发的人在 10 分钟内启动并跑通一次完整闭环；
5. 给出一份「开源仓库目录建议」和一份「提交前 TODO 清单」。

不要删除 .workbuddy/ 等工程数据目录，不要修改任何采集到的真实数据文件，
只输出检查结果与建议清单。
```

---

投喂前请先读 `docs/codearts_prompts.md` §1（尤其是 §1.1 的代码库索引重建）与 §2 的 P0 常驻约束；
本条对应留痕要求见主文档 §4.2 逐日排期与 §5 留痕方案。
