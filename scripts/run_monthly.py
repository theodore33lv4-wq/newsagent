"""手动生成月报。用法：python scripts/run_monthly.py --month 2026-10。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from newsagent.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["monthly", *sys.argv[1:]]))
