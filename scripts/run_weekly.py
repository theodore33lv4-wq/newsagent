"""每周流水线入口（给 Windows 任务计划程序使用）。

用法：python scripts\\run_weekly.py [--week 2026-W09] [--limit N] [--dry-run] [--regen]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from newsagent.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
