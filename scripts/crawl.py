"""站点批量采集命令行工具。

用法：
    python scripts/crawl.py --list                          # 查看已配置的采集源
    python scripts/crawl.py --source whut_gd_yjsgl --limit 5 --dry-run
    python scripts/crawl.py --all --limit 6 --save data/real_notices.json
    python scripts/crawl.py --all --limit 6 --import --analyze

`--save` 会把抓取结果落成 JSON 快照：既是离线演示数据，也是申报材料里
"数据集来源与采集过程"的举证材料（含真实 URL、字数、附件数、耗时）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database.connection import SessionLocal, init_db          # noqa: E402
from app.database.models import Notice                            # noqa: E402
from app.services import collector                                # noqa: E402
from app.services.textutil import content_hash                    # noqa: E402


def cmd_list() -> None:
    for src in collector.load_sources(only_enabled=False):
        flag = "启用" if src.enabled else "停用"
        print(f"[{flag}] {src.id:22s} {src.name}")
        print(f"         {src.url}")


def print_report(result: dict) -> None:
    for block in result["sources"]:
        meta, stats = block["source"], block["stats"]
        print(f"\n■ {meta['name']}  （{meta['id']}）")
        print(f"  列表页 {stats.get('pages', 0)} · 候选 {stats.get('candidates', 0)} "
              f"· 详情成功 {stats.get('detail_ok', 0)} · 失败 {stats.get('detail_fail', 0)} "
              f"· 均长 {stats.get('avg_chars', 0)} 字 · 耗时 {stats.get('elapsed', 0)}s")
        for item in block["items"][:12]:
            print(f"   - {item['title'][:34]:36s} {item['publish_date'] or '----------'} "
                  f"{item.get('chars', 0):>4d}字  {item.get('content_type', '')}")
        for err in block["errors"][:3]:
            print(f"   ! {err}")
    print(f"\n合计 {result['total']} 条，失败源：{result['summary'].get('failed') or '无'}")


def cmd_crawl(args) -> None:
    if args.all:
        source_ids = None
    elif args.source:
        source_ids = args.source
    else:
        print("请用 --source <id> 或 --all 指定采集范围")
        return

    result = collector.crawl_all(source_ids=source_ids, limit=args.limit,
                                 max_pages=args.pages, fetch_detail=not args.list_only)
    print_report(result)

    if args.save:
        path = Path(args.save)
        if not path.is_absolute():
            path = ROOT / path
        collector.save_snapshot(result, path)
        print(f"快照已保存：{path}")

    if not args.import_db:
        return

    from app.routers.notices import run_analysis

    init_db()
    db = SessionLocal()
    created = skipped = 0
    try:
        for block in result["sources"]:
            for item in block["items"]:
                title, content = item["title"], item.get("content", "")
                if len(content) < 10:
                    continue
                fingerprint = content_hash(title, content)
                if db.query(Notice).filter(Notice.content_hash == fingerprint).first():
                    skipped += 1
                    continue
                notice = Notice(
                    title=title,
                    content=content,
                    source_url=item.get("url", ""),
                    source_name=block["source"]["name"],
                    publish_time=item.get("publish_date", ""),
                    content_hash=fingerprint,
                    ingest_mode="crawl",
                    attachments=json.dumps(item.get("attachments") or [], ensure_ascii=False),
                )
                db.add(notice)
                db.commit()
                db.refresh(notice)
                created += 1
                if args.analyze:
                    run_analysis(db, notice)
    finally:
        db.close()
    print(f"入库完成：新增 {created} 条，跳过重复 {skipped} 条"
          + ("（已逐条解析）" if args.analyze else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description="高校智讯 · 站点批量采集")
    parser.add_argument("--list", action="store_true", help="列出采集源")
    parser.add_argument("--source", nargs="*", help="指定采集源 id（可多个）")
    parser.add_argument("--all", action="store_true", help="采集全部启用的源")
    parser.add_argument("--limit", type=int, default=8, help="每个源抓取条数上限")
    parser.add_argument("--pages", type=int, default=1, help="每个源翻页上限")
    parser.add_argument("--list-only", action="store_true", help="只抓列表页，不抓详情")
    parser.add_argument("--save", default="", help="把抓取结果保存为 JSON 快照")
    parser.add_argument("--import", dest="import_db", action="store_true", help="写入数据库")
    parser.add_argument("--analyze", action="store_true", help="入库后立即解析")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并打印，不入库")
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.dry_run:
        args.import_db = False
        cmd_crawl(args)
    else:
        cmd_crawl(args)


if __name__ == "__main__":
    main()
