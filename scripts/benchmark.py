"""处理效率实测：对应总控文档 25.4、技术文档2 第六十一章。

只写真实测出来的数字。脚本每次运行都会重新计时并覆盖 docs/benchmark.md，
环境信息一并记录，避免把本地开发机的结果当成云端性能。

明确不测、也不编造的部分：
  · 人工阅读整理一份通知的耗时 —— 需要真人计时实验，本报告只给测量口径；
  · 云端 RDS / 公网 / 并发压测 —— 需在华为云部署后单独执行。

用法：python scripts/benchmark.py
"""
from __future__ import annotations

import json
import platform
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.services.analyzer import analyze  # noqa: E402

REPEAT = 3
BATCH_N = 100
API_N = 20


def load_corpus() -> list[dict]:
    items: list[dict] = []
    for rel in ("data/sample_notices.json", "data/evaluation/holdout_notices.json"):
        p = BASE / rel
        if not p.exists():
            continue
        for x in json.loads(p.read_text(encoding="utf-8"))["notices"]:
            items.append({"title": x["title"], "content": x["content"],
                          "publish": x.get("publish_time", ""), "origin": "构造/标注集"})
    p = BASE / "data/real_notices.json"
    if p.exists():
        for block in json.loads(p.read_text(encoding="utf-8"))["sources"]:
            for x in block["items"]:
                items.append({"title": x["title"], "content": x["content"],
                              "publish": x.get("publish_date", ""), "origin": "真实采集集"})
    return items


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * q))]


def bench_single(corpus: list[dict]) -> list[float]:
    samples: list[float] = []
    for _ in range(REPEAT):
        for it in corpus:
            t0 = time.perf_counter()
            analyze(it["title"], it["content"], it["publish"], use_llm=False)
            samples.append((time.perf_counter() - t0) * 1000)
    return samples


def bench_batch(corpus: list[dict]) -> tuple[float, int]:
    seq = [corpus[i % len(corpus)] for i in range(BATCH_N)]
    t0 = time.perf_counter()
    for it in seq:
        analyze(it["title"], it["content"], it["publish"], use_llm=False)
    return (time.perf_counter() - t0), len(seq)


def bench_db() -> tuple[list[float], str]:
    from sqlalchemy import func, select

    from app.database.connection import SessionLocal, init_db
    from app.database.models import Notice
    init_db()
    times: list[float] = []
    with SessionLocal() as db:
        for _ in range(API_N):
            t0 = time.perf_counter()
            db.execute(select(func.count()).select_from(Notice)).scalar()
            times.append((time.perf_counter() - t0) * 1000)
    return times, "SELECT COUNT(*) FROM notices"


def bench_api() -> tuple[dict[str, list[float]], str]:
    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    endpoints = {
        "GET /api/health": "/api/health",
        "GET /api/notices?size=20": "/api/notices?size=20",
        "GET /api/dashboard/stats": "/api/dashboard/stats",
        "GET /api/sources": "/api/sources",
    }
    out: dict[str, list[float]] = {}
    with client:
        for label, path in endpoints.items():
            vals: list[float] = []
            for _ in range(API_N):
                t0 = time.perf_counter()
                resp = client.get(path)
                vals.append((time.perf_counter() - t0) * 1000)
                if resp.status_code >= 400:
                    return {}, f"{label} 返回 {resp.status_code}，本次 API 基准无效"
            out[label] = vals
    return out, "TestClient 进程内调用（不含网络传输）"


def main() -> None:
    import logging
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 避免 80 行请求日志淹没基准结果
    corpus = load_corpus()
    if not corpus:
        print("没有可用于基准的通知语料，请先运行 scripts/import_samples.py")
        return

    single = bench_single(corpus)
    batch_sec, batch_n = bench_batch(corpus)
    db_times, db_label = bench_db()
    api, api_note = bench_api()

    lines = [
        "# 处理效率实测报告",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　|　"
        f"脚本：`scripts/benchmark.py`（每次运行重新计时）",
        "",
        "## 1. 测量环境",
        "",
        "| 项 | 值 |",
        "| --- | --- |",
        f"| 操作系统 | {platform.system()} {platform.release()} |",
        f"| Python | {platform.python_version()} |",
        "| 数据库 | 本地 SQLite（开发机文件） |",
        "| 解析引擎 | 内置规则引擎（`use_llm=False`，无网络调用） |",
        f"| 语料 | {len(corpus)} 份（构造/标注集 + 42 份真实采集快照） |",
        f"| 单条重复轮次 | {REPEAT} 轮，共 {len(single)} 次采样 |",
        "",
        "> 这是**本地开发机**的实测值，不代表华为云 RDS / 公网部署后的性能；"
        "云端数字需在部署后重跑本脚本并单独记录。",
        "",
        "## 2. 单条通知解析耗时",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 样本数 | {len(single)} |",
        f"| 中位数 | {statistics.median(single):.2f} ms |",
        f"| 平均值 | {statistics.mean(single):.2f} ms |",
        f"| P95 | {pct(single, 0.95):.2f} ms |",
        f"| 最慢 | {max(single):.2f} ms |",
        "",
        "计时范围包含：文本归一化、文体判别、分类、日期打分、字段抽取、"
        "结构化约束抽取、原文接地与规则校验，**不含**数据库写入与网络 I/O。",
        "",
        "## 3. 批量处理",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 连续解析条数 | {batch_n} |",
        f"| 总耗时 | {batch_sec:.3f} s |",
        f"| 平均每条 | {batch_sec / batch_n * 1000:.2f} ms |",
        f"| 吞吐量 | {batch_n / batch_sec:.1f} 条/秒 |",
        "",
        f"语料不足 {BATCH_N} 份时按序循环复用，测量的是解析吞吐，不是去重入库吞吐。",
        "",
        "## 4. 接口响应与数据库查询",
        "",
        f"接口通过 TestClient 在进程内调用 {API_N} 次（不含网络传输），"
        "数据库查询为同一进程内计时。",
        "",
        "| 项目 | 平均 | 中位数 | P95 |",
        "| --- | --- | --- | --- |",
    ]
    for label, vals in api.items():
        lines.append(f"| {label} | {statistics.mean(vals):.2f} ms | "
                     f"{statistics.median(vals):.2f} ms | {pct(vals, 0.95):.2f} ms |")
    lines.append(f"| {db_label} | {statistics.mean(db_times):.2f} ms | "
                 f"{statistics.median(db_times):.2f} ms | {pct(db_times, 0.95):.2f} ms |")
    if not api:
        lines += ["", f"> API 基准未执行：{api_note}"]
    else:
        lines += ["", f"> 说明：{api_note}。"]

    lines += [
        "",
        "## 5. 人工对照口径（本次未实测）",
        "",
        "总控文档 25.4 要求比较「人工阅读整理耗时 vs 系统自动解析耗时」。"
        "人工耗时需要真人按统一任务书计时（例如：给出 10 份通知，记录读出类别、"
        "截止日期、适用对象、资格条件、材料并填表的时间），本次没有组织该实验，"
        "**因此不填写任何人工耗时或「效率提升百分比」**。",
        "",
        "后续执行建议：3 名计时员 × 10 份通知，取中位数，"
        "与本脚本第 2 节的单条中位数同口径比较，再写入比赛材料。",
        "",
        "## 6. 未覆盖项",
        "",
        "| 项 | 原因 | 何时补 |",
        "| --- | --- | --- |",
        "| 云端 RDS 查询耗时 | 本地为 SQLite，驱动与网络不同 | 华为云 RDS 连通后 |",
        "| 公网 API 响应 | 本地进程内调用不含网络 | 部署后从外网压测 |",
        "| 并发访问 | 需压测工具与独立部署实例 | 部署后用多客户端实测 |",
        "| 大模型链路耗时 | 需 MaaS 密钥与网络，且受服务端排队影响 | 密钥到位后 |",
        "",
        "> 所有数字均由本脚本现场计时产生；脚本不写入任何预设常量结果。",
        "",
    ]

    out = BASE / "docs" / "benchmark.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"语料 {len(corpus)} 份 | 单条中位数 {statistics.median(single):.2f} ms | "
          f"批量 {batch_n} 条 {batch_sec:.3f} s | 输出 {out.relative_to(BASE)}")


if __name__ == "__main__":
    main()
