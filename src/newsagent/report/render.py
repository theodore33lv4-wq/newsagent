"""HTML 渲染：Jinja2 → 自包含单文件（内联 CSS，浏览器直接打开）。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader

from ..utils.tags import is_vendor_tag
from .generator import ReportData

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _stars(importance) -> str:
    try:
        i = int(importance)
    except (TypeError, ValueError):
        return ""
    return "★" * max(0, min(3, i))


def _index(items: list[dict]) -> dict[int, dict]:
    return {it["idx"]: it for it in items}


def _original_url(value: str | None) -> str | None:
    if not value or any(ch.isspace() or ord(ch) < 32 for ch in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ("http", "https") and parsed.hostname:
            return value
    except ValueError:
        pass
    return None


def render_html(data: ReportData, items: list[dict] | None = None) -> str:
    """渲染自包含 HTML 文本。

    items：附录清单渲染用的条目（可变形版本，带 html_link 相对路径）；
    缺省使用 data.items（测试/无落盘场景）。
    """
    render_items = [
        {**it, "url": _original_url(it.get("url"))}
        for it in (items if items is not None else data.items)
    ]
    by_idx = _index(data.items)

    def item_title(idx) -> str:
        it = by_idx.get(idx)
        return it["title"] if it else "(未知条目)"

    def item_url(idx) -> str:
        it = by_idx.get(idx)
        return _original_url(it.get("url")) if it else None

    def item_tags(idx) -> list[str]:
        it = by_idx.get(idx)
        return it["tags"] if it else []

    def vendor_items() -> list[dict]:
        out = [
            it for it in data.items
            if any(is_vendor_tag(t) for t in it["tags"]) or it.get("companies")
        ]
        return sorted(out, key=lambda it: it.get("importance") or 0, reverse=True)

    template = _env.get_template("report.html.j2")
    return template.render(
        week=data.week, week_label=data.week_label, generated_at=data.generated_at,
        overview=data.overview, overview_points=data.overview_points,
        distribution=data.distribution,
        themes=data.themes, top5=data.top5,
        trends_macro=data.trends_macro, trends_projects=data.trends_projects,
        trends_vendor=data.trends_vendor,
        next_week=data.next_week, items=render_items,
        relevant_count=data.relevant_count, source_count=data.source_count,
        review_note=data.review_note,
        item_title=item_title, item_url=item_url, item_tags=item_tags,
        vendor_items=vendor_items,
        stars=_stars,
    )
