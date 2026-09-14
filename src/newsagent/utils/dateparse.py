"""日期解析：从文本、URL 与网页元数据中提取发布日期。

用于两处：
- 采集层：列表页的链接文本 / URL 路径里往往带日期（如 .../2026-09/10/content_xxx.html）
- 存档层：文章页的 meta、正文声明、JSON-LD、URL 中的日期

统一返回 datetime.date；带合理性校验（默认最近 3 年 ~ 明天之间）。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

_CN_NUM = {"元": 1, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}

# 完整日期：2026-09-10 / 2026/9/10 / 2026年9月10日 / 20260910
_FULL_PATTERNS = [
    re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})"),
    re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?"),
    re.compile(r"(20\d{2})(\d{2})(\d{2})(?!\d)"),
]
# URL 中的日期：/2026-09/10/ 、/2026/09/10/ 、/202609/t20260910_
_URL_PATTERNS = [
    re.compile(r"/(20\d{2})[-/](\d{1,2})/(\d{1,2})/"),
    re.compile(r"/(20\d{2})(\d{2})/t(20\d{2})(\d{2})(\d{2})_"),
    re.compile(r"/(20\d{2})/(\d{1,2})/(\d{1,2})/"),
]
_REL_PATTERNS = [
    (re.compile(r"(\d+)\s*分钟前"), "minutes"),
    (re.compile(r"(\d+)\s*小时前"), "hours"),
    (re.compile(r"(\d+)\s*天前"), "days"),
    (re.compile(r"昨天"), "yesterday"),
    (re.compile(r"前天"), "before_yesterday"),
    (re.compile(r"刚刚|刚才"), "now"),
]
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})[-/月](\d{1,2})日?(?!\d)")


def _mk(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def is_sane(d: Optional[date], *, today: Optional[date] = None,
            years_back: int = 3, days_ahead: int = 1) -> bool:
    """合理性校验：避免把"20260910 编号""第409批"之类误当日期。"""
    if d is None:
        return False
    today = today or date.today()
    return (today - timedelta(days=365 * years_back)) <= d <= (today + timedelta(days=days_ahead))


def parse_date_from_text(text: str, *, default_year: Optional[int] = None,
                         today: Optional[date] = None) -> Optional[date]:
    """从任意文本中提取日期（含相对时间表述，如"3小时前""昨天"）。"""
    if not text:
        return None
    today = today or date.today()

    for pat in _FULL_PATTERNS:
        for m in pat.finditer(text):
            groups = [g for g in m.groups() if g]
            if len(groups) >= 3:
                d = _mk(int(groups[0]), int(groups[1]), int(groups[2]))
            elif len(groups) == 2 and default_year:  # 仅月日
                d = _mk(default_year, int(groups[0]), int(groups[1]))
            else:
                continue
            if is_sane(d, today=today):
                return d

    # 相对时间
    for pat, kind in _REL_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if kind == "minutes":
            return today
        if kind == "hours":
            return today
        if kind == "days":
            return today - timedelta(days=int(m.group(1)))
        if kind == "yesterday":
            return today - timedelta(days=1)
        if kind == "before_yesterday":
            return today - timedelta(days=2)
        if kind == "now":
            return today

    # 仅"09-10"这类月日：需 default_year
    if default_year:
        m = _MONTH_DAY.search(text)
        if m:
            d = _mk(default_year, int(m.group(1)), int(m.group(2)))
            if is_sane(d, today=today, days_ahead=400):
                return d
    return None


def parse_date_from_url(url: str, *, today: Optional[date] = None) -> Optional[date]:
    """从 URL 路径中提取日期（列表页与详情页通用）。"""
    if not url:
        return None
    today = today or date.today()
    for pat in _URL_PATTERNS:
        m = pat.search(url)
        if not m:
            continue
        nums = [int(g) for g in m.groups() if g]
        if len(nums) == 5:          # /202609/t20260910_
            d = _mk(nums[2], nums[3], nums[4])
        elif len(nums) >= 3:
            d = _mk(nums[0], nums[1], nums[2])
        else:
            continue
        if is_sane(d, today=today):
            return d
    return parse_date_from_text(url, today=today)


def normalize_date(value, *, today: Optional[date] = None) -> Optional[date]:
    """把各种日期表示统一成 date：ISO 串、时间戳、常见中文格式。"""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value if is_sane(value, today=today) else None
    if isinstance(value, datetime):
        d = value.date()
        return d if is_sane(d, today=today) else None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            d = datetime.fromtimestamp(ts).date()
        except (OverflowError, OSError, ValueError):
            return None
        return d if is_sane(d, today=today) else None
    text = str(value).strip()
    if not text:
        return None
    m = re.match(r"(20\d{2})-(\d{2})-(\d{2})", text)   # 2026-09-10T10:00:00+08:00
    if m:
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d if is_sane(d, today=today) else None
    return parse_date_from_text(text, today=today)


def to_iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None
