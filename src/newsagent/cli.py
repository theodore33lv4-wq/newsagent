"""newsagent CLI：python -m newsagent [pipeline|report] [选项]。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from .utils.config import Config


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--week", default=None,
                        help="ISO 周编号（如 2026-W09），默认当前周")
    parser.add_argument("--limit", type=int, default=None,
                        help="限制本轮处理条数（<=0 表示不限制）")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅采集+去重预览，不做任何写入")
    parser.add_argument("--regen", action="store_true",
                        help="跳过采集，仅重新生成指定周综述")
    parser.add_argument("--provider", default=None,
                        choices=["mock", "openai-compat", "ollama"],
                        help="临时覆盖 llm.provider")
    parser.add_argument("--log-level", default="INFO",
                        help="控制台日志级别（默认 INFO）")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="newsagent",
                                     description="智能交通新闻智能体")
    sub = parser.add_subparsers(dest="command")

    p_pipe = sub.add_parser("pipeline", help="运行每周流水线")
    _add_common(p_pipe)

    p_report = sub.add_parser("report", help="仅生成综述")
    _add_common(p_report)

    args = parser.parse_args(argv)
    if not args.command:
        args.command = "pipeline"

    try:
        cfg = Config.load()
    except Exception as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "provider", None):
        cfg.llm["provider"] = args.provider

    from .pipeline import run_pipeline

    if args.command == "report" and not getattr(args, "regen", False):
        args.regen = True  # report 子命令默认仅重生成综述

    limit = getattr(args, "limit", None)
    stats = run_pipeline(
        cfg, week=getattr(args, "week", None),
        limit=limit if limit and limit > 0 else None,
        dry_run=getattr(args, "dry_run", False),
        regen=getattr(args, "regen", False),
    )
    _print_summary(stats)
    return 0 if not stats.errors else 1


def _print_summary(stats) -> None:
    print()
    print("===== 运行摘要 =====")
    print(f"周数        : {stats.week}")
    print(f"候选/新条目 : {stats.candidates} / {stats.new_articles}")
    print(f"存档        : 成功 {stats.archived} / 失败 {stats.archived_failed}")
    print(f"分类        : {stats.classified} 条（相关 {stats.relevant}，"
          f"厂商动态 {stats.vendor}，待人工 {stats.classify_failed}）")
    if stats.report_paths:
        html = stats.report_paths.get("html_path")
        docx = stats.report_paths.get("docx_path")
        print(f"综述        : {html}  /  {docx}")
    else:
        print("综述        : 未生成")
    if stats.errors:
        print(f"异常        : {len(stats.errors)} 处（详见 data/logs 日志）")
        for e in stats.errors[:5]:
            print(f"  - {e}")
    print("=================")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
