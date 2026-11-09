"""newsagent CLI：python -m newsagent [pipeline|report] [选项]。"""

from __future__ import annotations

import argparse
import sys

from loguru import logger

from .utils.config import Config


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--week",
                        help="ISO 周编号（如 2026-W09），默认上一完整周")
    parser.add_argument("--limit", type=int,
                        help="限制本轮处理条数（<=0 表示不限制）")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅采集+去重预览，不做任何写入")
    parser.add_argument("--regen", action="store_true",
                        help="跳过采集，仅重新生成指定周综述")
    parser.add_argument("--no-collect", action="store_true",
                        help="跳过采集：仅对库中待分类条目重新打标并生成周报"
                             "（标签体系调整后常用）")
    parser.add_argument("--provider",
                        choices=["mock", "openai-compat", "ollama"],
                        help="临时覆盖 llm.provider")
    parser.add_argument("--log-level", type=str.upper,
                        choices=["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"],
                        help="控制台日志级别（默认 INFO）")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="newsagent",
                                     description="智能交通新闻智能体")
    _add_common(parser)
    parser.set_defaults(log_level="INFO")
    sub = parser.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    _add_common(common)
    sub.add_parser("pipeline", help="运行每周流水线", parents=[common])
    sub.add_parser("report", help="仅生成综述", parents=[common])

    args = parser.parse_args(argv)

    try:
        cfg = Config.load()
    except Exception as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 2

    if args.provider:
        cfg.llm["provider"] = args.provider

    from .pipeline import run_pipeline

    try:
        stats = run_pipeline(
            cfg, week=args.week, limit=args.limit, dry_run=args.dry_run,
            regen=args.regen or args.command == "report",
            collect=not args.no_collect, log_level=args.log_level,
        )
    except Exception as exc:
        logger.exception("流水线中断: {}", exc)
        if not args.dry_run:
            from .utils.notify import notify_failure

            try:
                notify_failure(cfg, "newsagent 流水线中断", str(exc))
            except Exception as notify_exc:
                logger.warning("异常告警发送失败: {}", notify_exc)
        return 1
    _print_summary(stats, dry_run=args.dry_run)
    return 0 if not stats.errors else 1


def _print_summary(stats, *, dry_run: bool = False) -> None:
    print()
    print("===== 运行摘要 =====")
    if stats.errors:
        print("结果        : 部分完成，周报已写出" if stats.report_paths else "结果        : 失败")
    elif dry_run:
        print("结果        : 预览完成")
    else:
        print("结果        : 完成" if stats.report_paths else "结果        : 本周无相关条目")
    print(f"周数        : {stats.week}")
    print(f"候选/新条目 : {stats.candidates} / {stats.new_articles}")
    print(f"存档        : 目标周 {stats.archived} / 下载或提取失败 {stats.archived_failed}")
    print(f"过滤与归档  : 非新闻页 {stats.rejected} / 归档到历史周 {stats.historical} "
          f"/ 归档到后续周 {stats.deferred} / 日期待判定 {stats.date_pending}")
    if stats.reject_reasons:
        detail = "、".join(f"{k}×{v}" for k, v in stats.reject_reasons.items())
        print(f"过滤明细    : {detail}")
    print(f"分类        : {stats.classified} 条（相关 {stats.relevant}，"
          f"厂商类 {stats.vendor}，待人工 {stats.classify_failed}）")
    if stats.report_paths:
        paths = [str(path) for key, path in stats.report_paths.items() if key.endswith("_path")]
        print(f"综述        : {' / '.join(paths)}")
    else:
        print("综述        : 未生成")
    for message in stats.coverage_warnings:
        print(f"覆盖提示    : {message}")
    if stats.errors:
        print(f"异常        : {len(stats.errors)} 处")
        for e in stats.errors[:5]:
            print(f"  - {e}")
    print("=================")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
