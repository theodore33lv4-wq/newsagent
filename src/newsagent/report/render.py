"""HTML 渲染：Jinja2 → 自包含单文件（内联 CSS，浏览器直接打开）。"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .generator import ReportData

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(("html",)),
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


def render_html(data: ReportData, items: list[dict] | None = None) -> str:
    """渲染自包含 HTML 文本。

    items：附录清单渲染用的条目（可变形版本，带 html_link 相对路径）；
    缺省使用 data.items（测试/无落盘场景）。
    """
    render_items = items if items is not None else data.items
    by_idx = _index(data.items)

    def item_title(idx) -> str:
        it = by_idx.get(idx)
        return it["title"] if it else "(未知条目)"

    def item_url(idx) -> str:
        it = by_idx.get(idx)
        return (it["url"] if it and it.get("url") else "#") or "#"

    def item_tags(idx) -> list[str]:
        it = by_idx.get(idx)
        return it["tags"] if it else []

    def vendor_items() -> list[dict]:
        out = [
            it for it in data.items
            if any(t.startswith("厂商动态") for t in it["tags"]) or it.get("companies")
        ]
        return sorted(out, key=lambda it: it.get("importance") or 0, reverse=True)

    template = _env.get_template("report.html.j2")
    return template.render(
        week=data.week, week_label=data.week_label, generated_at=data.generated_at,
        overview=data.overview, overview_points=data.overview_points,
        distribution=data.distribution,
        themes=data.themes, top5=data.top5,
        trends=data.trends, next_week=data.next_week, items=render_items,
        relevant_count=data.relevant_count, source_count=data.source_count,
        fallback=data.fallback,
        item_title=item_title, item_url=item_url, item_tags=item_tags,
        vendor_items=vendor_items,
        stars=_stars,
    )
