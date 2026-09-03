"""综述生成：确定性主题分组 + LLM 要点 + LLM 综述成文 → ReportData。

- 主题分组：直接复用打标阶段的 level-1 标签（零 LLM 调用、与附录/检索完全一致、跨周可比）；
- LLM 要点：为每条新闻写"一句话要点"（失败降级为摘要截断）；
- LLM 综述：概览（含 bullet 要点）/ TOP5 / 趋势观察 / 下周关注（失败自动降级）。
任一步失败都不会中断周报产出。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from ..classify.llm import LLMProvider
from ..classify.tagger import extract_json
from ..utils.config import Config
from ..utils.dates import week_label_cn


@dataclass
class ReportData:
    """一份可渲染的周报数据（HTML/Word 共用同一数据源）。"""

    week: str
    week_label: str
    generated_at: str
    total_count: int = 0
    relevant_count: int = 0
    source_count: int = 0
    overview: str = ""
    overview_points: list[str] = field(default_factory=list)  # 概览 bullet 要点
    distribution: list[dict] = field(default_factory=list)    # 类别分布 [{name,count,pct}]
    themes: list[dict] = field(default_factory=list)   # {title, items:[{idx,note}]}
    top5: list[int] = field(default_factory=list)
    trends: list[str] = field(default_factory=list)
    next_week: list[str] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)    # 附录清单
    fallback: bool = False                              # 是否走降级生成

    def find_item(self, idx: int) -> Optional[dict]:
        for it in self.items:
            if it.get("idx") == idx:
                return it
        return None


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def build_items(rows: list[dict], summary_max: int) -> list[dict]:
    """store 行 → 带 idx 的条目列表（截断摘要）。"""
    items = []
    for i, row in enumerate(rows, start=1):
        items.append({
            "idx": i,
            "title": row.get("title", ""),
            "source_name": row.get("source_name", ""),
            "published_at": (row.get("published_at") or "")[:10],
            "tags": row.get("tags", []) or [],
            "companies": row.get("companies", []) or [],
            "importance": row.get("importance"),
            "summary": _truncate(row.get("summary") or "", summary_max),
            "url": row.get("url", ""),
            "html_file": row.get("html_file"),
            "guid": row.get("guid"),
        })
    return items


# ---------- LLM 要点 + 综述 ----------
_NOTES_SYSTEM_TMPL = """你是智能交通领域的周报综述助手。为下面列出的每条新闻写一句要点（不超过 40 字），突出该条本周最值得关注之处。

只输出一个 JSON 对象，格式：
{{"notes": [{{"idx": 1, "note": "一句话要点"}}, ...]}}

要求：覆盖全部 idx；note 不超过 40 字。"""

_STEP2_SYSTEM_TMPL = """你是智能交通领域的周报综述助手。综述成文任务：基于下面的主题分组撰写综述。

只输出一个 JSON 对象，格式：
{{"overview": "约 {overview_chars} 字的本周整体概览，全面覆盖主要主题并突出重点",
 "overview_points": ["要点1，不超过40字", ...],
 "top5": [idx, idx, ...],
 "trends": ["趋势观察1", ...],
 "next_week": ["下周关注1", ...]}}

要求：
- overview {overview_chars} 字左右：概括本周整体态势（政策动向、技术进展、产业与厂商动态、城市与地方实践）并突出重点；
- overview_points 给出 {points_count} 条结构化的本周要点，每条不超过 40 字，可注明所属主题（如“政策：…”“厂商：…”）；
- top5 为本周最重要的 5 条新闻 idx（按重要性排序，从给出的条目中选择）；
- trends 2-4 条，概括本周行业趋势（如技术路线、政策走向、厂商动态）；
- next_week 1-3 条，下周值得关注的方向。"""


def generate(cfg: Config, provider: LLMProvider, week: str,
             rows: list[dict], generated_at: str) -> ReportData:
    c = cfg.report
    summary_max = int(c.get("summary_max_chars", 90))
    max_items = int(c.get("max_items", 120))
    if len(rows) > max_items:
        logger.warning("综述条目 {} 超过上限 {}，按重要度截断", len(rows), max_items)
        rows = sorted(rows, key=lambda r: (r.get("importance") or 0), reverse=True)[:max_items]

    items = build_items(rows, summary_max)
    data = ReportData(
        week=week, week_label=week_label_cn(week, cfg.app.get("timezone")),
        generated_at=generated_at,
        total_count=len(items) + 0,
        relevant_count=len(items),
        source_count=len({it["source_name"] for it in items}),
        items=items,
    )
    if not items:
        data.overview = "本周没有符合条件的新闻条目。"
        return data

    data.distribution = _distribution(items, _level1_names(cfg))

    # 主题分组（确定性，复用打标 level-1 标签）+ LLM 写要点
    themes, notes_ok = _build_themes(cfg, provider, items)
    data.themes = themes

    # LLM 综述成文
    overview_chars = int(c.get("overview_chars", 250))
    points_count = int(c.get("overview_points_count", 5))
    overview, overview_points, top5, trends, next_week = _step2_compose(
        cfg, provider, items, themes, overview_chars, points_count)

    data.overview = overview or _fallback_overview(items)
    data.overview_points = overview_points or _fallback_points(
        items, points_count, _level1_names(cfg))
    data.top5 = _valid_indices(top5, items)
    data.trends = _str_list(trends)
    data.next_week = _str_list(next_week)
    data.fallback = (not notes_ok) or (overview is None)
    return data


# ---------- 主题分组（确定性） ----------
def _level1_names(cfg: Config) -> list[str]:
    """标签体系的一级分类名（可能是"车路协同/智能网联"这种含斜杠的名字）。"""
    return [str(n.get("name", "")).strip() for n in cfg.taxonomy
            if str(n.get("name", "")).strip()]


def _level1_of(tags: list[str], nodes: list[str]) -> str:
    """标签路径 → 一级分类名。

    优先全等匹配（标签恰好是一级名，如"车路协同/智能网联"），
    再按"一级名/二级名"前缀匹配（如"厂商动态/集成商动态"），认不出兜底"其他"。
    """
    for t in tags or []:
        t = str(t).strip()
        for name in nodes:
            if t == name or t.startswith(name + "/"):
                return name
    return "其他"


def _group_by_level1(items: list[dict], nodes: list[str]) -> list[dict]:
    """直接复用打标的 level-1 标签分组（与附录标签、检索接口完全一致）。

    返回 [{title, items:[{idx, note(摘要兜底)}]}]，主题按 数量降序 → 名称升序。
    """
    groups: dict[str, list[dict]] = {}
    for it in items:
        key = _level1_of(it.get("tags"), nodes)
        groups.setdefault(key, []).append(it)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [{"title": title,
             "items": [{"idx": it["idx"], "note": _truncate(it["summary"], 40)}
                       for it in sub]}
            for title, sub in ordered]


def _build_themes(cfg: Config, provider: LLMProvider,
                  items: list[dict]) -> tuple[list[dict], bool]:
    """主题 = 确定性分组；要点 note 由 LLM 补充（失败用摘要兜底）。

    返回 (themes, notes_ok)。
    """
    themes = _group_by_level1(items, _level1_names(cfg))
    notes = _notes_for_items(cfg, provider, items)
    if notes:
        for t in themes:
            for sub in t["items"]:
                sub["note"] = notes.get(sub["idx"], sub["note"])
        return themes, True
    return themes, False


def _notes_for_items(cfg: Config, provider: LLMProvider,
                     items: list[dict]) -> Optional[dict[int, str]]:
    listing = "\n".join(f"{it['idx']}. {it['title']}（{it['source_name']}）"
                        f"{' / ' + '/'.join(it['tags']) if it['tags'] else ''}"
                        f" {it['summary']}" for it in items)
    messages = [
        {"role": "system", "content": _NOTES_SYSTEM_TMPL},
        {"role": "user", "content": f"本周新闻条目：\n{listing}"},
    ]
    try:
        raw = provider.chat(messages, json_mode=True, model=cfg.report_model)
        data = extract_json(raw)
        if not data:
            logger.warning("要点生成未返回有效 JSON")
            return None
        out: dict[int, str] = {}
        for n in data.get("notes") or []:
            try:
                idx = int(n.get("idx"))
            except (TypeError, ValueError):
                continue
            note = _str(n.get("note"))
            if note:
                out[idx] = _truncate(note, 60)
        return out or None
    except Exception as exc:
        logger.warning("要点生成失败，将使用摘要作为要点: {}", exc)
        return None


def _step2_compose(cfg: Config, provider: LLMProvider, items: list[dict],
                   themes: list[dict], overview_chars: int,
                   points_count: int) -> tuple[Optional[str], list[str], list[int],
                                               list[str], list[str]]:
    theme_text = "\n".join(
        f"## {t['title']}\n" + "\n".join(f"- {it['idx']}. {it['note']}" for it in t["items"])
        for t in themes) or "（无主题分组）"
    system = _STEP2_SYSTEM_TMPL.format(overview_chars=overview_chars,
                                       points_count=points_count)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"主题分组如下：\n{theme_text}"},
    ]
    try:
        raw = provider.chat(messages, json_mode=True, model=cfg.report_model)
        data = extract_json(raw)
        if not data:
            return None, [], [], [], []
        top5 = []
        for idx in (data.get("top5") or []):
            try:
                top5.append(int(idx))
            except (TypeError, ValueError):
                continue
        return (_str(data.get("overview")), _str_list(data.get("overview_points"))[:points_count],
                top5, _str_list(data.get("trends")), _str_list(data.get("next_week")))
    except Exception as exc:
        logger.warning("综述成文失败，使用降级内容: {}", exc)
        return None, [], [], [], []


# ---------- 类别分布与降级 ----------
def _distribution(items: list[dict], nodes: list[str]) -> list[dict]:
    """按一级标签统计类别分布：[{name, count, pct}]，pct 相对最大类（100%）。"""
    counts: dict[str, int] = {}
    for it in items:
        key = _level1_of(it.get("tags"), nodes)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return []
    max_count = max(counts.values())
    dist = [{"name": name, "count": cnt,
             "pct": round(cnt / max_count * 100, 1)}
            for name, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return dist


def _fallback_overview(items: list[dict]) -> str:
    vendors = sum(1 for it in items if any(t.startswith("厂商动态") for t in it["tags"]))
    return (f"本周共收录 {len(items)} 条智能交通相关新闻，"
            f"其中厂商/集成商动态 {vendors} 条。"
            f"（自动降级生成：LLM 不可用时的概览）")


def _fallback_points(items: list[dict], n: int, nodes: list[str]) -> list[str]:
    """LLM 不可用时的兜底要点：按类别分布生成。"""
    return [f"{d['name']}：{d['count']} 条"
            for d in _distribution(items, nodes)[: max(0, n)]]


def _valid_indices(top5: list[int], items: list[dict]) -> list[int]:
    valid = {it["idx"] for it in items}
    seen: set[int] = set()
    out = []
    for i in top5:
        if i in valid and i not in seen:
            seen.add(i)
            out.append(i)
        if len(out) >= 5:
            break
    return out


def _str(v) -> str:
    return str(v).strip() if v is not None else ""


def _str_list(v) -> list[str]:
    if not isinstance(v, list):
        return []
    return [s for s in (_str(x) for x in v) if s]
