"""新闻页体检：判断下载到的页面是否是"一条具体新闻"。

背景：列表页选择器可能把栏目页、专题页、聚合汇总贴一并抓进来
（它们的 URL 形态与文章页相同），因此需要在存档阶段做页面级校验。

四类拒绝原因：
  aggregate_title  标题是"汇总/盘点/合集"类聚合贴
  template_title   标题是站点模板标题（栏目页/专题页特征，如 A|B|C_站点名）
  too_short        正文过短，不是一篇报道
  high_link_density 正文几乎全是链接（列表/导航页特征）
日期缺失不直接拒绝：交给大模型判定（日期兜底），仍拿不到日期才丢弃。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

from ..utils.dateparse import normalize_date, parse_date_from_text, parse_date_from_url
from ..utils.config import Config

_AGGREGATE_RE = re.compile(
    r"汇总|盘点|合集|一览表|一览$|周刊合集|主题汇总|资料汇编|导航页|目录页")

# 页面内日期线索
_META_PATTERNS = [
    re.compile(r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|og:release_date|'
               r'publishdate|pubdate|weibo:article:create_at|date|pubDate)["\'][^>]*'
               r'content=["\']([^"\']+)', re.I),
    re.compile(r'"(?:publishTime|pubTime|publishedTime|datePublished|createTime|releaseDate)"'
               r'\s*:\s*"([^"]{6,40})"', re.I),
    re.compile(r"(?:发布时间|发布日期|发布于|时间)\s*[:：]\s*"
               r"(20\d{2}[-/年.]\d{1,2}[-/月.]\d{1,2})"),
]


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
                      meta_date: Optional[str] = None) -> Optional[date]:
    """多策略抽取页面发布日期：meta/JSON → 正文声明 → trafilatura → URL。"""
    if html:
        for pat in _META_PATTERNS:
            for m in pat.finditer(html):
                d = normalize_date(m.group(1))
                if d:
                    return d
    d = normalize_date(meta_date) if meta_date else None
    if d:
        return d
    if url:
        d = parse_date_from_url(url)
        if d:
            return d
    if html:
        # 正文中的"2026-09-10 10:30"这类时间戳，取首个合理日期
        d = parse_date_from_text(html[:20000])
        if d:
            return d
    return None


def validate_page(title: str, text: Optional[str], html: str, url: str,
                  *, source_name: str = "", cfg: Optional[Config] = None,
                  meta_date: Optional[str] = None,
                  fallback_date: Optional[date] = None,
                  density: Optional[float] = None) -> PageVerdict:
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
    if len(body) < min_chars:
        return PageVerdict(False, "too_short")

    density = link_density(html) if density is None else density
    if density > max_density:
        return PageVerdict(False, "high_link_density")

    pub = extract_page_date(html, url, meta_date) or fallback_date
    return PageVerdict(True, None, pub, date_missing=pub is None)
