"""周编号与时间工具测试。"""

from datetime import datetime

import pytest

from newsagent.utils.dates import iso_week, target_week, week_label_cn, week_range


def test_iso_week_from_date():
    assert iso_week(datetime(2026, 8, 24)) == "2026-W35"


def test_target_week_is_previous_complete_week():
    """口径验证：周一 9/7 运行 → 目标周为上周 W36；一周内任何天运行都相同。"""
    assert iso_week(datetime(2026, 9, 7, 8, 30)) == "2026-W37"   # 今天是周一
    assert target_week(datetime(2026, 9, 7, 8, 30)) == "2026-W36"  # 目标=上周
    assert target_week(datetime(2026, 9, 13, 23, 0)) == "2026-W36"  # 周日补跑仍落在上周
    assert target_week(datetime(2026, 9, 10, 12, 0)) == "2026-W36"  # 周中补跑同样稳


def test_week_range():
    start, end = week_range("2026-W35")
    assert start.isoformat() == "2026-08-24T00:00:00+08:00"
    assert end.day == 30


def test_week_label():
    label = week_label_cn("2026-W35")
    assert "2026" in label and "第35周" in label


def test_week_range_invalid():
    with pytest.raises(ValueError):
        week_range("not-a-week")
