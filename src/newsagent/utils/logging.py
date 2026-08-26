"""loguru 日志初始化：控制台 + 滚动文件（data/logs/）。"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def setup_logging(data_dir: Path, run_tag: str, level: str = "INFO") -> Path:
    """初始化日志；返回本次运行的日志文件路径。

    每次调用会重置 logger 的默认 handler，重复调用安全。
    run_tag 例：'2026-W09' 或 '2026-W09-dryrun'。
    """
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"{run_tag}.log"

    # Windows 下默认控制台编码可能非 UTF-8，尽量统一为 UTF-8（失败则忽略）
    try:
        if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    logger.remove()
    logger.add(sys.stderr, format=_FORMAT, level=level.upper(), colorize=True)
    logger.add(
        log_file,
        format=_FORMAT,
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
        enqueue=False,
    )
    return log_file
