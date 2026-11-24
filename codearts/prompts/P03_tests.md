# P3　边界用例与回归测试生成（测试环节）

> 自动生成：由 `scripts/codearts_tools.py split` 从 `docs/codearts_prompts.md` 拆出。
> **请勿手改本文件**；要改提示词请改主文档后重新执行 `split`。
> 完整说明、执行顺序、红线与留痕模板见 `docs/codearts_prompts.md`（P3）。

**建议附加文件**：`tests/test_smoke.py`、`app/serializers.py`、`app/static/js/app.js`。
**期望产出**：用例矩阵 + 自检扩充。
**验收**：`python tests/test_smoke.py` 全通过；`node --check app/static/js/app.js` 通过。
**留痕**：截图 #4「测试记录」——同框显示新增用例数量与全绿输出。
**人工复核**：用例必须真的能失败于修复前（可临时回退验证），避免写「恒真断言」。

## 提示词正文（整段复制到 CodeArts）

```text
请为本项目补一组**接口层与界面层**的边界回归用例，不要重复现有 tests/test_smoke.py 已覆盖的服务层断言。

重点覆盖以下真实风险面：
1. 分页越界（page=0 / 负数 / 极大值 / 非整数）、size 超出上限、非法 notice_id / user_id；
2. 四个日期字段的各种非法输入（缺补零、带尾缀、超范围、非字符串），断言返回 400 而不是 500；
3. 删除通知、删除用户后不出现孤儿 user_tasks / notice_analysis；
4. 问答接口 top_k 的 0 / 1 / 10 / 11 / 非整数边界；
5. 空库、单条数据、全部数据都缺解析记录时，看板 / 匹配 / 日程返回结构完整且不抛异常；
6. 前端 app.js 在接口返回空数组、缺字段、非法 deadline 时不会显示 NaN / undefined，
   也不会因为单个接口失败让整个首页降级（当前用 Promise.allSettled 分块降级）。

要求：先列出用例矩阵（编号 / 输入 / 期望 / 覆盖文件），我确认后再写代码；
新增用例用项目现有 check() 断言风格，不引入 pytest 等新依赖；
每条用例注明它防的是哪一类真实故障。最后给出运行命令与预期通过项数。
```

---

投喂前请先读 `docs/codearts_prompts.md` §1（尤其是 §1.1 的代码库索引重建）与 §2 的 P0 常驻约束；
本条对应留痕要求见主文档 §4.2 逐日排期与 §5 留痕方案。
