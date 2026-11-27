"""人工审核办理清单质量：把每条可办理通知的清单打出来给人看。

自检只能断言「结构对不对」，断言不了「这一步像不像人话」。
把 42 份真实通知的清单一次性铺开肉眼扫一遍，是发现「联系人被当成步骤」
「同一句话重复两遍」这类问题最快的方式——本项目的多轮清单迭代都是这么发现的。

用法：

    python scripts/review_checklist.py                    # 用现有库，只打印
    python scripts/review_checklist.py --import-real      # 先导入 42 份真实通知再打印
    python scripts/review_checklist.py --steps 6          # 每条通知最多打印 6 步
    python scripts/review_checklist.py --only-degraded    # 只看没拆出步骤的，排查漏抽

默认用临时库，不碰 data/gaoxiao_zhixun.db。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _prepare_db(import_real: bool) -> str:
    """准备一个干净的数据库，返回库文件路径以便结束时清理。"""
    if os.environ.get("DATABASE_URL"):
        # 显式指定了库就照用，方便对着真实数据反复调参
        return ""
    path = Path(tempfile.mkdtemp(prefix="zhixun_review_")) / "review.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{path.as_posix()}"
    os.environ.setdefault("LLM_PROVIDER", "none")
    return str(path)


def _import_real(client) -> tuple[int, int]:
    data = json.loads((ROOT / "data" / "real_notices.json").read_text(encoding="utf-8"))
    created, analyzed = 0, 0
    for source in data.get("sources") or []:
        name = (source.get("source") or {}).get("name", "")
        for item in source.get("items") or []:
            resp = client.post("/api/notices", json={
                "title": item.get("title", ""),
                "content": item.get("content", ""),
                "publish_time": item.get("publish_date", ""),
                "source_url": item.get("url", ""),
                "source_name": name,
                "category": "",
            })
            if resp.status_code not in (200, 201):
                continue
            created += 1
            nid = resp.json()["id"]
            if client.post(f"/api/notices/{nid}/analyze",
                           params={"use_llm": "false"}).status_code == 200:
                analyzed += 1
    return created, analyzed


def _fetch_all(client) -> list[dict]:
    """分页取全量通知。`size` 上限是 100，直接传 500 会被 422 拒掉。"""
    items: list[dict] = []
    page = 1
    while True:
        resp = client.get("/api/notices", params={"page": page, "size": 100})
        if resp.status_code != 200:
            break
        payload = resp.json()
        batch = payload.get("items") or []
        items.extend(batch)
        if len(items) >= int(payload.get("total") or 0) or not batch:
            break
        page += 1
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="人工审核办理清单质量")
    parser.add_argument("--import-real", action="store_true",
                        help="先把 data/real_notices.json 导入临时库再审核")
    parser.add_argument("--steps", type=int, default=4,
                        help="每条通知最多打印几步，默认 4；传 0 打印全部")
    parser.add_argument("--only-degraded", action="store_true",
                        help="只打印没有拆出步骤的通知，用于排查漏抽")
    args = parser.parse_args()

    tmp_db = _prepare_db(args.import_real)

    # 环境变量必须在导入 app 之前就位，所以导入放在函数内部
    import logging  # noqa: PLC0415

    # httpx 会把每一次请求都打一行 INFO，42 份通知导下来是几百行噪声，
    # 把真正的审核输出淹掉了。这里只留本项目自己的日志。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.database.connection import init_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    init_db()
    client = TestClient(app)

    if args.import_real:
        created, analyzed = _import_real(client)
        print(f"导入真实通知 {created} 条，解析成功 {analyzed} 条\n")

    rows = _fetch_all(client)
    actionable = [n for n in rows if (n.get("analysis") or {}).get("actionable")]
    if not actionable:
        print("没有可办理通知。先加 --import-real，或往库里导入样本。")
        return 0

    limit = args.steps if args.steps > 0 else 10_000
    total_steps = total_offline = total_dep = degraded = 0

    for notice in actionable:
        analysis = notice["analysis"]
        checklist = client.get(f"/api/notices/{notice['id']}/checklist").json()
        stats = checklist.get("stats") or {}
        steps = checklist.get("steps") or []

        total_steps += stats.get("total", 0)
        total_offline += stats.get("offline", 0)
        total_dep += stats.get("with_dependency", 0)
        if checklist.get("degraded"):
            degraded += 1
        if args.only_degraded and not checklist.get("degraded"):
            continue

        deadline = analysis.get("deadline") or "—"
        head = (f"id={notice['id']:>3}  截止={deadline:<12}  "
                f"步骤={stats.get('total', 0)}  线下={stats.get('offline', 0)}  "
                f"依赖={stats.get('with_dependency', 0)}")
        print(head)
        print(f"      《{notice['title'][:52]}》")
        if checklist.get("reason"):
            print(f"      说明：{checklist['reason']}")
        for step in steps[:limit]:
            channel = "线下" if step.get("offline") else "线上"
            dep = f"  ←依赖#{step['depends_on']}" if step.get("depends_on") else ""
            # source=deadline 的那步是系统补的时间锚点，原文没有这句话
            mark = " [系统补]" if step.get("source") == "deadline" else ""
            print(f"      {step['seq']}. [{channel}] {step['title'][:44]}{dep}{mark}")
        if len(steps) > limit:
            print(f"      …… 另有 {len(steps) - limit} 步未显示")
        print()

    avg = total_steps / len(actionable) if actionable else 0
    print("=" * 60)
    print(f"可办理通知 {len(actionable)} 条 / 步骤合计 {total_steps} 步"
          f"（平均 {avg:.1f} 步）")
    print(f"线下步骤 {total_offline} 个 / 带前置依赖 {total_dep} 个 / 未拆出步骤 {degraded} 条")

    if tmp_db:
        try:
            os.unlink(tmp_db)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
