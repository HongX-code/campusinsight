# P9　界面与体验优化（Arctic Glass 约束下）

> 自动生成：由 `scripts/codearts_tools.py split` 从 `docs/codearts_prompts.md` 拆出。
> **请勿手改本文件**；要改提示词请改主文档后重新执行 `split`。
> 完整说明、执行顺序、红线与留痕模板见 `docs/codearts_prompts.md`（P9）。

**建议附加文件**：`docs/ui_design.md`、`app/templates/index.html`、`app/static/css/style.css`、`app/static/js/app.js`、`scripts/capture.py`。
**期望产出**：问题表 + 最小改动 + 重拍清单。
**验收**：`python scripts/capture.py` 重拍后逐张目检；`node --check app/static/js/app.js`。
**留痕**：截图 #3 可并入「问题修复」；重拍截图归入 `docs/shot-*.png`。
**人工复核**：任何视觉改动都要重新核对 24 张截图与 PPT 中的界面页，避免材料与实机不一致。

## 提示词正文（整段复制到 CodeArts）

```text
请在严格遵守 docs/ui_design.md 的 Arctic Glass Design System 前提下，做一次界面可用性审查，
先输出问题清单，我确认后再改，且不要改设计变量。

审查项：
1. 9 个视图在 1280 / 1920 / 390 三档宽度下是否出现横向溢出、文字截断、按钮不可点；
2. 空数据、加载中、错误、部分字段缺失、已截止五种状态下，卡片与列表是否有可读的降级呈现；
3. AI 相关视觉是否只用紫色 #7C3AED 且保持克制，蓝色 #2563EB 是否只用于核心操作；
   有没有回退成「满屏蓝色表格后台」的倾向；
4. 字段与 evidence 是否同组展示，点击 evidence 能否定位并高亮原文；
   匹配分是否始终标注「规则加权匹配度（非录取概率）」；
5. 截止时间四态（正常绿 / 临近橙 / 紧急红 / 已截止灰）与「日期待核对」的容错文案是否正确；
6. 键盘可达性、焦点可见、按钮 hover/active 反馈、动效是否控制在 150—300ms。

输出：问题表格（视图 / 现象 / 复现步骤 / 建议改法 / 涉及文件 / 是否影响 24 张截图），
按「影响用户完成一次完整办理闭环」排序。只提出最小改动，不引入组件库、不改 CSS 变量命名、
不重新设计视觉风格。改完后给出需要重拍的截图清单与 scripts/capture.py 的执行方式。
```

---

投喂前请先读 `docs/codearts_prompts.md` §1（尤其是 §1.1 的代码库索引重建）与 §2 的 P0 常驻约束；
本条对应留痕要求见主文档 §4.2 逐日排期与 §5 留痕方案。
