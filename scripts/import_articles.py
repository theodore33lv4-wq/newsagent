"""从 data/articles/<周>/*.json 导入（或重置）条目到索引库。

典型用途：
1. 拿到别人机器上的存档数据（只有 articles/raw 目录）时，导入到本地库；
2. 标签体系调整后，把这些条目重置为待分类，配合
   `run_weekly.py --week <周> --no-collect` 重新打标并生成周报。

用法：
    .venv\\Scripts\\python scripts\\import_articles.py --week 2026-W38
    .venv\\Scripts\\python scripts\\import_articles.py --week 2026-W38 --reset
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from newsagent.archive.store import Store  # noqa: E402
from newsagent.utils.config import Config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入或重置某一周的存档条目")
    parser.add_argument("--week", required=True, help="ISO 周编号，如 2026-W38")
    parser.add_argument("--reset", action="store_true",
                        help="把导入的条目标记为待分类（用于重新打标）")
    args = parser.parse_args(argv)

    cfg = Config.load()
    store = Store(cfg.data_dir)
    week_dir = cfg.data_dir / "articles" / args.week
    raw_dir = cfg.data_dir / "raw" / args.week

    files = sorted(week_dir.glob("*.json"))
    if not files:
        print(f"未找到 {week_dir} 下的文章 JSON，请确认数据已复制到位。")
        return 1

    inserted = updated = skipped = 0
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  跳过无法解析的文件 {f.name}: {exc}")
            skipped += 1
            continue
        guid = data.get("guid") or f.stem
        raw_file = raw_dir / f"{guid}.html"
        record = {
            "guid": guid,
            "week": data.get("week") or args.week,
            "url": data.get("url", ""),
            "url_key": data.get("url_key", ""),
            "title": data.get("title", ""),
            "title_key": data.get("title_key", ""),
            "source_id": data.get("source_id", ""),
            "source_name": data.get("source_name", ""),
            "published_at": data.get("published_at"),
            "fetched_at": data.get("fetched_at"),
            "html_file": str(Path("raw") / args.week / raw_file.name)
                         if raw_file.exists() else None,
            "text_file": str(Path("articles") / args.week / f.name),
            "text_chars": int(data.get("text_chars") or 0),
            "content_hash": data.get("content_hash"),
            "extractor": data.get("extractor"),
        }
        result = store.upsert_article(record, reset_classification=args.reset)
        if result == "inserted":
            inserted += 1
        else:
            updated += 1

    total = store.query(week=args.week, relevant_only=False, limit=1000)
    pending = [r for r in total if r["status"] == "archived"]
    print(f"周 {args.week}：导入 {inserted} 条 / 更新 {updated} 条"
          f"{f' / 跳过 {skipped} 条' if skipped else ''}")
    print(f"库中该周共 {len(total)} 条，其中待分类 {len(pending)} 条"
          f"（原始快照 {'已就位' if raw_dir.exists() else '缺失'}）")
    if args.reset and pending:
        print(f"\n下一步：.venv\\Scripts\\python scripts\\run_weekly.py "
              f"--week {args.week} --no-collect   # 重新打标并生成周报")
    return 0


if __name__ == "__main__":
    sys.exit(main())
