"""newsagent.report —— 周报综述（生成 / HTML 渲染 / Word 导出）。

write_report()：本周行数据 → 综述 → 落盘 reports/{week}/weekly-{week}.{html,docx,json}。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from loguru import logger

from ..classify.llm import LLMProvider
from ..utils.config import Config
from ..utils.dates import now_local
from .export import to_docx
from .generator import ReportData, generate
from .render import render_html


def write_report(cfg: Config, provider: LLMProvider | None, week: str,
                 rows: list[dict], *, load_text: Callable[[dict], str | None] | None = None) -> dict:
    """写入 JSON 和已配置的周报格式，返回产物路径与报告数据。"""
    formats = cfg.report.get("formats", ["html", "docx"])
    if not isinstance(formats, list) or any(fmt not in ("html", "docx") for fmt in formats):
        raise ValueError("report.formats 必须为 HTML、DOCX 格式列表")
    generated_at = now_local(cfg.app.get("timezone")).strftime("%Y-%m-%d %H:%M")
    data = generate(cfg, provider, week, rows, generated_at, load_text=load_text)

    out_dir = cfg.data_dir / "reports" / week
    out_dir.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix=".report-", dir=out_dir) as work_dir:
        files = _stage_report(cfg, data, out_dir, Path(work_dir), formats)
        paths = {"week": week, "data": data}
        for key, path in files.items():
            target = out_dir / path.name
            path.replace(target)
            paths[key] = target
    logger.info("周报已生成：{}", week)
    return paths


def _stage_report(cfg: Config, data: ReportData, out_dir: Path, staging: Path,
                  formats: list[str]) -> dict[str, Path]:
    basename = f"weekly-{data.week}"
    json_path = staging / f"{basename}.json"
    json_path.write_text(json.dumps(_data_to_dict(data), ensure_ascii=False, indent=1),
                         encoding="utf-8")
    paths = {"json_path": json_path}

    # 附录条目附带"存档"相对链接：从 reports/<week>/ 指向 raw/ 快照，
    # 保证整个 data/ 目录一起移动/部署后链接依然有效
    if "html" in formats:
        render_items = []
        raw_dir = (cfg.data_dir / "raw").resolve()
        for it in data.items:
            item = dict(it)
            if it.get("html_file"):
                target = (cfg.data_dir / it["html_file"]).resolve()
                if target.is_relative_to(raw_dir):
                    item["html_link"] = os.path.relpath(target, out_dir).replace("\\", "/")
            render_items.append(item)
        html_path = staging / f"{basename}.html"
        html_path.write_text(render_html(data, items=render_items), encoding="utf-8")
        paths["html_path"] = html_path
    if "docx" in formats:
        docx_path = staging / f"{basename}.docx"
        to_docx(data, docx_path)
        paths["docx_path"] = docx_path
    return paths


def _data_to_dict(data: ReportData) -> dict:
    return {
        "week": data.week, "week_label": data.week_label,
        "generated_at": data.generated_at,
        "total_count": data.total_count, "relevant_count": data.relevant_count,
        "source_count": data.source_count,
        "overview": data.overview, "overview_points": data.overview_points,
        "distribution": data.distribution,
        "themes": data.themes, "top5": data.top5,
        "trends_macro": data.trends_macro, "trends_projects": data.trends_projects,
        "trends_vendor": data.trends_vendor,
        "next_week": data.next_week,
        "fallback": data.fallback, "evidence_adjustments": data.evidence_adjustments,
        "items": data.items,
    }


__all__ = ["write_report", "ReportData", "generate"]
