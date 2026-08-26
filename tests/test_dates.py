"""周编号与时间工具测试。"""

from datetime import datetime

import pytest

from newsagent.utils.dates import iso_week, week_label_cn, week_range


def test_iso_week_from_date():
    assert iso_week(datetime(2026, 8, 24)) == "2026-W35"


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
