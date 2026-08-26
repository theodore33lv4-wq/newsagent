"""时间与周编号工具（按 app.timezone，默认 Asia/Shanghai）。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.9 以下兜底
    ZoneInfo = None  # type: ignore[assignment,misc]

DEFAULT_TZ_NAME = "Asia/Shanghai"


def tz_for(name: str | None) -> timezone:
    name = name or DEFAULT_TZ_NAME
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return timezone(timedelta(hours=8))  # 兜底：东八区


def now_local(tz_name: str | None = None) -> datetime:
    return datetime.now(tz=tz_for(tz_name))


def iso_week(dt: datetime | date | None = None, tz_name: str | None = None) -> str:
    """ISO 周编号，如 '2026-W09'。"""
    if dt is None:
        dt = now_local(tz_name)
    if isinstance(dt, datetime) and dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz_for(tz_name))
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_range(week: str, tz_name: str | None = None) -> tuple[datetime, datetime]:
    """周编号 → (周一 00:00, 周日 23:59:59)（本地时区 aware datetime）。"""
    import re
    m = re.match(r"^(\d{4})-W?(\d{1,2})$", week.strip())
    if not m:
        raise ValueError(f"周编号格式非法: {week!r}（应为 2026-W09）")
    year, wk = int(m.group(1)), int(m.group(2))
    tz = tz_for(tz_name)
    monday = date.fromisocalendar(year, wk, 1)
    start = datetime.combine(monday, time.min, tzinfo=tz)
    end = datetime.combine(monday + timedelta(days=6), time.max, tzinfo=tz)
    return start, end


def week_label_cn(week: str, tz_name: str | None = None) -> str:
    """如 '2026年第9周（02月23日—03月01日）'。"""
    tz = tz_for(tz_name)
    start, end = week_range(week, tz_name)
    fmt = "%m月%d日"
    return f"{start.year}年第{start.isocalendar().week}周（{start.strftime(fmt)}—{end.strftime(fmt)}）"
