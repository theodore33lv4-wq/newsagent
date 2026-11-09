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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from newsagent.archive.store import Store  # noqa: E402
from newsagent.collect.dedup import title_key, url_key  # noqa: E402
from newsagent.utils.config import Config  # noqa: E402
from newsagent.utils.dates import iso_week, stored_date, week_range  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入或重置某一周的存档条目")
    parser.add_argument("--week", required=True, help="ISO 周编号，如 2026-W38")
    parser.add_argument("--reset", action="store_true",
                        help="重置索引中归属指定周的已分类条目")
    args = parser.parse_args(argv)

    cfg = Config.load()
    tz_name = cfg.app.get("timezone")
    try:
        week = iso_week(week_range(args.week, tz_name)[0], tz_name)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    store = Store(cfg.data_dir)
    week_dir = cfg.data_dir / "articles" / week
    raw_dir = cfg.data_dir / "raw" / week

    files = sorted(week_dir.glob("*.json"))
    if not files and not args.reset:
        print(f"未找到 {week_dir} 下的文章 JSON，请确认数据已复制到位。")
        return 1

    inserted = updated = skipped = 0
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("存档需为 JSON 对象")
            guid = data.get("guid") or f.stem
            url, title = data.get("url"), data.get("title")
            if (not isinstance(guid, str) or not re.fullmatch(r"[0-9a-f]{16}", guid)
                    or not isinstance(url, str) or not url.strip()
                    or not isinstance(title, str) or not title.strip()):
                raise ValueError("GUID、URL 或标题无效")
            published = stored_date(data.get("published_at"), tz_name)
            if data.get("published_at") and published is None:
                raise ValueError("存档发布日期不是 ISO 日期")
            text = data.get("text")
            status = data.get("status")
            if status != "filtered" and not (isinstance(text, str) and text.strip()):
                status = "retryable"
            raw_file = raw_dir / f"{guid}.html"
            record = {
                "guid": guid,
                "week": iso_week(published, tz_name) if published else week,
                "url": url,
                "url_key": url_key(url),
                "title": title,
                "title_key": title_key(title),
                "source_id": data.get("source_id") or "",
                "source_name": data.get("source_name") or "",
                "published_at": published.isoformat() if published else None,
                "fetched_at": data.get("fetched_at") or datetime.now(timezone.utc).isoformat(),
                "html_file": str(raw_file.relative_to(cfg.data_dir)) if raw_file.is_file() else None,
                "text_file": str(f.relative_to(cfg.data_dir)),
                "text_chars": len(text) if isinstance(text, str) else 0,
                "content_hash": data.get("content_hash"),
                "extractor": data.get("extractor"),
                "status": status,
                "note": data.get("note"),
            }
            result = store.upsert_article(record)
        except (OSError, TypeError, ValueError) as exc:
            print(f"  跳过无法解析的文件 {f.name}: {exc}")
            skipped += 1
            continue
        if result == "inserted":
            inserted += 1
        else:
            updated += 1

    if args.reset and not skipped:
        print(f"已重置 {store.reset_week(week)} 条已分类条目")
    total = store.query(week=week, relevant_only=False, limit=-1)
    pending = [r for r in total if r["status"] == "archived"]
    print(f"周 {week}：导入 {inserted} 条 / 已存在 {updated} 条"
          f"{f' / 跳过 {skipped} 条' if skipped else ''}")
    print(f"库中该周共 {len(total)} 条，其中待分类 {len(pending)} 条"
          f"（原始快照 {'已就位' if raw_dir.exists() else '缺失'}）")
    if args.reset and pending:
        print(f"\n下一步：.venv\\Scripts\\python scripts\\run_weekly.py "
              f"--week {week} --no-collect   # 重新打标并生成周报")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
