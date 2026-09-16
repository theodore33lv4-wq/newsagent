"""把某一周被过滤的条目恢复为待分类状态（数据补救工具）。

用途：早期版本曾把"目标周之后发布"的新闻误判为过期新闻并过滤掉。
这些条目已登记去重（不会再被采集），需要手工放回待分类队列，
下一次运行该周周报时就会正常打标与收录。

用法：
    .venv\\Scripts\\python scripts\\recover_filtered.py --week 2026-W38            # 先看清单
    .venv\\Scripts\\python scripts\\recover_filtered.py --week 2026-W38 --apply    # 确认后执行
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from newsagent.archive.store import Store  # noqa: E402
from newsagent.utils.config import Config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="恢复某一周被过滤的新闻条目")
    parser.add_argument("--week", required=True, help="ISO 周编号，如 2026-W38")
    parser.add_argument("--apply", action="store_true",
                        help="确认执行恢复（不加此参数仅列出清单）")
    args = parser.parse_args(argv)

    cfg = Config.load()
    store = Store(cfg.data_dir)
    rows = store.list_filtered(args.week)

    print(f"周 {args.week} 被过滤条目：{len(rows)} 条")
    if not rows:
        print("无需处理。")
        return 0
    by_reason: dict[str, int] = {}
    for r in rows:
        reason = (r.get("note") or "未知原因").split("：")[-1][:28]
        by_reason[reason] = by_reason.get(reason, 0) + 1
    for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"  - {reason}: {n} 条")
    print("前几条示例：")
    for r in rows[:5]:
        print(f"  [{r['published_at'] or '无日期'}] {r['title'][:38]}")

    if not args.apply:
        print("\n以上仅为清单。确认无误后加 --apply 执行恢复。")
        return 0

    n = store.recover_filtered(args.week)
    print(f"\n已恢复 {n} 条为待分类状态；下次运行该周周报时会正常打标并收录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
