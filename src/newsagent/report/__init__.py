"""newsagent.report —— 周报综述（生成 / HTML 渲染 / Word 导出）。

write_report()：本周行数据 → 综述 → 落盘 reports/{week}/weekly-{week}.{html,docx,json}。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from loguru import logger

from ..classify.llm import LLMProvider
from ..utils.config import Config
from .export import to_docx
from .generator import ReportData, generate
from .render import render_html


def write_report(cfg: Config, provider: LLMProvider, week: str,
                 rows: list[dict]) -> dict:
    """生成并写出双格式综述，返回 {week, html_path, docx_path, json_path, data}。"""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    data = generate(cfg, provider, week, rows, generated_at)

    out_dir = cfg.data_dir / "reports" / week
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"weekly-{week}.json"
    json_path.write_text(
        json.dumps(_data_to_dict(data), ensure_ascii=False, indent=1),
        encoding="utf-8")

    # 附录条目附带"存档"相对链接：从 reports/<week>/ 指向 raw/ 快照，
    # 保证整个 data/ 目录一起移动/部署后链接依然有效
    render_items = []
    for it in data.items:
        r = dict(it)
        if it.get("html_file"):
            target = (cfg.data_dir / str(it["html_file"])).resolve()
            r["html_link"] = os.path.relpath(target, out_dir).replace("\\", "/")
        render_items.append(r)

    html_path = out_dir / f"weekly-{week}.html"
    html_path.write_text(render_html(data, items=render_items), encoding="utf-8")

    docx_path = out_dir / f"weekly-{week}.docx"
    to_docx(data, docx_path)

    logger.info("周报已生成：{}（HTML {} 字节 / Word {} 字节）",
                week, html_path.stat().st_size, docx_path.stat().st_size)
    return {"week": week, "html_path": html_path, "docx_path": docx_path,
            "json_path": json_path, "data": data}


def _data_to_dict(data: ReportData) -> dict:
    return {
        "week": data.week, "week_label": data.week_label,
        "generated_at": data.generated_at,
        "total_count": data.total_count, "relevant_count": data.relevant_count,
        "source_count": data.source_count,
        "overview": data.overview, "overview_points": data.overview_points,
        "distribution": data.distribution,
        "themes": data.themes, "top5": data.top5,
        "trends": data.trends, "next_week": data.next_week,
        "fallback": data.fallback, "items": data.items,
    }


__all__ = ["write_report", "ReportData", "generate"]
