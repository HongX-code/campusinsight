# P8　文档 / PPT / 表单口径同步（针对当前交付件）

> 自动生成：由 `scripts/codearts_tools.py split` 从 `docs/codearts_prompts.md` 拆出。
> **请勿手改本文件**；要改提示词请改主文档后重新执行 `split`。
> 完整说明、执行顺序、红线与留痕模板见 `docs/codearts_prompts.md`（P8）。

**建议附加文件**：`README.md`、`docs/test_report.md`、`docs/design_compliance.md`、`docs/benchmark.md`、`../../申报材料/build_ppt.py`、`../../申报材料/fill_form.py`。
**期望产出**：差异清单 + 最小替换清单 + 重建/验证命令。
**留痕**：截图 #2「代码生成 / 修改记录」——显示口径对照与最小 diff。
**人工复核**：字数、页数、旧值扫描三项必须人工过一遍；PDF 导出后确认内容确实更新（曾出现覆盖静默失败）。

## 提示词正文（整段复制到 CodeArts）

```text
请做一次「交付件口径一致性检查与同步」，先报告差异，我确认后再改。

检查范围：README.md、docs/ 下全部 Markdown、`../../申报材料/` 下的 build_ppt.py / fill_form.py / export_pdf.py，
以及它们生成的 PPTX / PDF / DOCX 中所写的数字与状态。

必须一致的口径（以代码与 docs/benchmark.md 为准）：
- 49 个路由端点、259 项自检、约 21000 行源码（Python 16400 + 前端 4629）、9 个视图、24 张截图；
- 42 份真实快照 / 74 份语料 / 24+8 双集标注 / 20 页 PPT / 16 行表格 DOCX / 16 份 docs Markdown + 根 README（合计 17 份）；
- 单条解析中位数 1.86 ms、P95 7.21 ms、100 条 0.202 s、495.5 条/秒；
- 「四层保护 + 规则校验（C1—C11）」、可信 AI、Arctic Glass、非录取概率的匹配度表述；
- 华为云 RDS / Redis / MaaS / CodeArts 的「代码就绪，待开通验证」状态，以及
  CodeArts 留痕「提示词方案已就绪、实操记录待补齐」的表述。

任务：
1. 逐文件列出写死数字的位置，标出与基准不符或可能漂移的项；
2. 检查申报材料三项字数限制（500 / 500 / 1000）与 PPT 页数、DOCX 表格行数；
3. 检查 build_ppt.py 与 fill_form.py 是否都从 docs/benchmark.md 动态读取性能值，
   而不是硬编码；若发现硬编码，改成复用 scripts/sync_metrics.py 的回灌口径；
4. 给出需要改动的最小文件清单与逐处替换建议，不要整篇重写文档；
5. 说明改完后需要重新生成哪些交付件、用什么命令验证（PPTX/PDF 页数、DOCX 字数、旧值扫描）。

不得为了让材料好看把「待验证」改成「已完成」；不得改动任何评测脚本的输出数据。
```

## 验收命令

```bash
python scripts/sync_metrics.py                     # 幂等回灌
python ../../申报材料/build_ppt.py && python ../../申报材料/fill_form.py
python ../../申报材料/export_pdf.py          # 导出 PDF 并校验页数（避免 SaveAs 覆盖静默失败）
python ../../申报材料/export_pdf.py --check  # 只核对 PPTX / PDF 页数是否一致
# 重新导出 PDF 后校验页数；全项目扫描旧口径（81 项 / 9700 / 旧性能值）应为 0 命中
```

---

投喂前请先读 `docs/codearts_prompts.md` §1（尤其是 §1.1 的代码库索引重建）与 §2 的 P0 常驻约束；
本条对应留痕要求见主文档 §4.2 逐日排期与 §5 留痕方案。
