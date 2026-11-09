"""新闻页体检：判断下载到的页面是否是"一条具体新闻"。

背景：列表页选择器可能把栏目页、专题页、聚合汇总贴一并抓进来
（它们的 URL 形态与文章页相同），因此需要在存档阶段做页面级校验。

四类拒绝原因：
  aggregate_title  标题是"汇总/盘点/合集"类聚合贴
  template_title   标题是站点模板标题（栏目页/专题页特征，如 A|B|C_站点名）
  too_short        正文过短，不是一篇报道
  high_link_density 页面链接密集，且缺少可信的正文段落
日期缺失交给大模型补判，仍无法确定时可保留待核验。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

from ..utils.dateparse import normalize_date, parse_date_from_url
from ..utils.dates import now_local
from ..utils.config import Config

_AGGREGATE_RE = re.compile(
    r"汇总|盘点|合集|一览表|一览$|周刊合集|主题汇总|资料汇编|导航页|目录页")

# 页面内日期线索
_PUBLISHED_META = {"article:published_time", "og:release_date", "publishdate", "pubdate",
                   "weibo:article:create_at", "datepublished"}
_STRUCTURED_DATE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.I)
_PUBLISHED_LABEL = re.compile(r"(?:发布时间|发布日期|发布于)\s*[:：]?\s*"
                              r"(20\d{2}[-/年.]\d{1,2}[-/月.]\d{1,2})")


@dataclass
class PageVerdict:
    ok: bool
    reason: Optional[str] = None
    publish_date: Optional[date] = None
    date_missing: bool = False


def link_density(html: str) -> float:
    """链接密度 = 链接内文本长度 / 全页文本长度。列表页/导航页显著偏高。"""
    if not html:
        return 0.0
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        total = len(soup.get_text(" ", strip=True))
        if total <= 0:
            return 0.0
        linked = sum(len(a.get_text(" ", strip=True)) for a in soup.find_all("a"))
        return min(1.0, linked / total)
    except Exception:
        return 0.0


def _has_prose(html: str, extracted: str, min_chars: int) -> bool:
    """确认抽出的正文在页面中有独立段落，避免推荐链接抬高整页密度。"""
    soup = BeautifulSoup(html, "lxml")
    text = re.sub(r"\s+", "", extracted)
    prose = set()
    for paragraph in soup.find_all("p"):
        if paragraph.find_parent(["a", "nav", "header", "footer", "aside"]):
            continue
        if paragraph.find("a"):
            continue
        content = paragraph.get_text("", strip=True)
        content = re.sub(r"\s+", "", content)
        if len(content) >= 40 and re.search(r"[。！？.!?]", content) and content in text:
            prose.add(content)
    return sum(map(len, prose)) >= min_chars


def title_is_template(title: str, source_name: str = "") -> bool:
    """站点模板标题识别：多段竖线分隔、或与来源名高度重合。"""
    t = (title or "").strip()
    if not t:
        return False
    segments = [s.strip() for s in re.split(r"[|｜_]", t) if s.strip()]
    if len(segments) >= 3 and len(set(segments)) >= 3:
        return True
    brand = re.sub(r"[（(].*?[)）]|搜狐号|官网|首页", "", source_name or "").strip()
    if brand and (t == brand or (brand in t and "|" in t)):
        return True
    return False


def extract_page_date(html: str, url: str = "",
                      meta_date: Optional[str] = None, *, fallback_date: date | None = None,
                      tz_name: str | None = None) -> Optional[date]:
    """优先使用明确的发布时间，再用链接与列表线索。"""
    today = now_local(tz_name).date()
    if html:
        soup = BeautifulSoup(html, "lxml")
        for meta in soup.find_all("meta"):
            key = str(meta.get("property") or meta.get("name") or meta.get("itemprop") or "").lower()
            if key in _PUBLISHED_META:
                published = normalize_date(meta.get("content"), today=today, tz_name=tz_name)
                if published:
                    return published
        for script in soup.find_all("script", type="application/ld+json"):
            for match in _STRUCTURED_DATE.finditer(script.string or ""):
                published = normalize_date(match[1], today=today, tz_name=tz_name)
                if published:
                    return published
        for tag in soup(["header", "footer", "nav", "aside", "script", "style"]):
            tag.decompose()
        for match in _PUBLISHED_LABEL.finditer(soup.get_text(" ", strip=True)):
            published = normalize_date(match[1], today=today, tz_name=tz_name)
            if published:
                return published
    return (parse_date_from_url(url, today=today) or fallback_date
            or normalize_date(meta_date, today=today, tz_name=tz_name))


def validate_page(title: str, text: Optional[str], html: str, url: str,
                  *, source_name: str = "", cfg: Optional[Config] = None,
                  meta_date: Optional[str] = None,
                  fallback_date: Optional[date] = None,
                  density: Optional[float] = None, allow_short: bool = False) -> PageVerdict:
    """返回体检结论；ok=False 时 reason 说明拒绝原因。"""
    arc = (cfg.archive if cfg is not None else {}) or {}
    min_chars = int(arc.get("min_text_chars", 300))
    max_density = float(arc.get("max_link_density", 0.15))
    reject_aggregate = bool(arc.get("reject_aggregate_titles", True))

    t = (title or "").strip()
    if reject_aggregate and _AGGREGATE_RE.search(t):
        return PageVerdict(False, "aggregate_title")
    if title_is_template(t, source_name):
        return PageVerdict(False, "template_title")

    body = (text or "").strip()
    if not allow_short and len(body) < min_chars:
        return PageVerdict(False, "too_short")

    density = link_density(html) if density is None else density
    min_prose = max(1, int(arc.get("min_prose_chars", 100)))
    if density > max_density and not _has_prose(html, body, min_prose):
        return PageVerdict(False, "high_link_density")

    tz_name = cfg.app.get("timezone") if cfg is not None else None
    pub = extract_page_date(html, url, meta_date, fallback_date=fallback_date, tz_name=tz_name)
    return PageVerdict(True, None, pub, date_missing=pub is None)
