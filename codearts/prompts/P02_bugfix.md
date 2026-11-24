# P2　缺陷定位与修复（问题定位 + 问题修复环节）

> 自动生成：由 `scripts/codearts_tools.py split` 从 `docs/codearts_prompts.md` 拆出。
> **请勿手改本文件**；要改提示词请改主文档后重新执行 `split`。
> 完整说明、执行顺序、红线与留痕模板见 `docs/codearts_prompts.md`（P2）。

**目的**：这是最有说服力的一次留痕——让 CodeArts 从现象出发独立定位一个真实 Bug，而不是把答案直接告诉它。素材来自 `docs/test_report.md` §6.10 记录的本轮真实缺陷。
**建议附加文件**：`app/routers/notices.py`、`app/routers/dashboard.py`、`app/routers/users.py`、`app/services/textutil.py`、`docs/test_report.md`。
**期望产出**：第一轮调用链与根因；第二轮最小 diff + 新增断言。
**留痕**：截图 #3「缺陷修复」——需同框显示根因结论与修复后的测试通过输出；会话导出含两轮对话。
**人工复核**：确认修复没有把「非法日期」静默改成合法日期（应为 400 明确报错）；确认容错是「跳过脏数据」而不是「悄悄丢掉正常数据」。

## 提示词正文（整段复制到 CodeArts）

```text
请帮我定位并修复一个已复现的接口层缺陷，先定位、后修复，分两轮回答。

【现象】人工在「修正解析结果」里把 deadline 填成了 "2026-11-13x"，之后
GET /api/dashboard/stats、GET /api/users/{id}/agenda、GET /api/users/{id}/matches
等多个接口对**所有用户**返回 500，整个看板和日程打不开。

【第一轮：只定位，不要改代码】
1. 沿写入口 → 存储 → 查询 → 日期运算的调用链，指出异常具体从哪一行抛出、为什么字符串比较
   会让它进入「7 天内截止」区间；
2. 说明为什么服务层 81 项自检当时全绿却没抓到；
3. 检查同类问题是否还存在于 start_time / registration_start / event_time，
   以及 SQLite 外键关闭时删除通知会不会留下孤儿 user_tasks；
4. 给出最小修复方案（文件 + 函数 + 改动点），以及需要补的回归断言。

等我回复「按方案修复」后再进入第二轮。第二轮要求：
- 只改必要文件，不重构无关模块；日期解析统一收敛到 app/services/textutil.py 的唯一入口；
  写入口对 4 个日期字段返回 400；看板/日程对库里已有的历史脏数据做容错跳过，不能 500；
- 删除通知/用户时显式清理关联 user_tasks；
- 在 tests/test_smoke.py 增加能复现该 500 的断言，以及删除后无孤儿待办的断言；
- 输出验收命令与预期结果。不要改动任何评测数据、性能数字或 docs/benchmark.md。
```

## 验收命令

```bash
python tests/test_smoke.py                 # 101/101（若新增断言则按实际数量全通过）
python scripts/evaluate.py                 # 开发集五项指标不得退化
python scripts/evaluate.py --set holdout    # 留出集五项指标不得退化
```

---

投喂前请先读 `docs/codearts_prompts.md` §1（尤其是 §1.1 的代码库索引重建）与 §2 的 P0 常驻约束；
本条对应留痕要求见主文档 §4.2 逐日排期与 §5 留痕方案。
