"""日期解析测试：文本 / URL / 元数据多策略。"""

from datetime import date

from newsagent.utils.dateparse import (is_sane, normalize_date, parse_date_from_text,
                                       parse_date_from_url)


def test_parse_full_dates():
    assert parse_date_from_text("发布时间：2026-09-10 10:30") == date(2026, 9, 10)
    assert parse_date_from_text("2026年9月10日 讯") == date(2026, 9, 10)
    assert parse_date_from_text("t20260910_4223213") == date(2026, 9, 10)


def test_parse_relative_dates():
    today = date(2026, 9, 14)
    assert parse_date_from_text("3小时前", today=today) == today
    assert parse_date_from_text("昨天 18:20", today=today) == date(2026, 9, 13)
    assert parse_date_from_text("2天前", today=today) == date(2026, 9, 12)


def test_parse_month_day_with_default_year():
    assert parse_date_from_text("09-10 15:00", default_year=2026) == date(2026, 9, 10)


def test_parse_url_dates():
    assert parse_date_from_url("https://www.zgjtb.com/2026-09/10/content_536680.html") == date(2026, 9, 10)
    assert parse_date_from_url("https://www.mot.gov.cn/xinwen/202609/t20260910_4223213.html") == date(2026, 9, 10)


def test_sanity_and_normalize():
    today = date(2026, 9, 14)
    assert not is_sane(date(2001, 1, 1), today=today)
    assert is_sane(date(2026, 9, 10), today=today)
    assert normalize_date("2026-09-10T10:00:00+08:00") == date(2026, 9, 10)
    assert normalize_date(1787702400000) is not None      # 毫秒时间戳
    assert normalize_date(None) is None
    assert normalize_date("不是日期") is None
