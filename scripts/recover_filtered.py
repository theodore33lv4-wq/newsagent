"""预览某周被过滤的条目，按 GUID 恢复明确选中的记录。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from newsagent.archive.store import Store  # noqa: E402
from newsagent.utils.config import Config  # noqa: E402
from newsagent.utils.dates import iso_week, week_range  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="恢复某一周被过滤的新闻条目")
    parser.add_argument("--week", required=True, help="ISO 周编号，如 2026-W38")
    parser.add_argument("--apply", action="store_true",
                        help="确认执行恢复（不加此参数仅列出清单）")
    parser.add_argument("--guid", action="append", default=[], help="选中的条目 GUID，可重复指定")
    args = parser.parse_args(argv)
    if args.apply and not args.guid:
        parser.error("--apply 需要至少一个 --guid")

    cfg = Config.load()
    try:
        week = iso_week(week_range(args.week)[0])
    except ValueError as exc:
        parser.error(str(exc))
    if not (cfg.data_dir / "index.sqlite3").is_file():
        print("未找到索引库。")
        return 1 if args.apply else 0
    store = Store(cfg.data_dir)
    rows = store.list_filtered(week)
    if args.guid:
        selected = set(args.guid)
        rows = [row for row in rows if row["guid"] in selected]
        if {row["guid"] for row in rows} != selected:
            parser.error("部分 GUID 在指定周内不存在或已不处于过滤状态")

    print(f"周 {week} 被过滤条目：{len(rows)} 条")
    if not rows:
        print("无需处理。")
        return 0
    for row in rows:
        print(f"  {row['guid']} [{row['published_at'] or '无日期'}] {row['title']} | {row['note'] or '未知原因'}")

    if not args.apply:
        print("\n以上仅为清单。使用 --guid 选中条目，再加 --apply 执行恢复。")
        return 0

    n = store.recover_filtered(week, args.guid)
    print(f"\n已恢复 {n} 条为待处理。有正文的条目可重新打标，空正文条目将在采集时重试。")
    return 0 if n == len(set(args.guid)) else 1


if __name__ == "__main__":
    sys.exit(main())
