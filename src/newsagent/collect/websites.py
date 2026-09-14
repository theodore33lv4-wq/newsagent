"""固定网站列表页采集器（通用规则：列表页 + 文章链接选择器）。

配置示例（sources.yaml）：
  type: website
  list_url: https://www.example.com/news/
  article_selector: "h3 a"        # CSS 选择器，可选（默认 a）
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from loguru import logger

from ..utils.dateparse import parse_date_from_text, parse_date_from_url
from .base import Article, Collector, fetch

_SKIP_HREF = ("javascript:", "mailto:", "tel:", "#", "data:")


def _is_article_link(href: str) -> bool:
    """过滤明显非文章的链接形态。

    注意：不要排除以 "?" 开头的纯查询链接 —— 部分站点（如赛文交通网）的
    文章地址正是 "?m=home&c=View&a=index&aid=xxxx" 这种形式。
    """
    if not href or href.lower().startswith(_SKIP_HREF):
        return False
    return not href.startswith("#")


def _same_host(netloc: str, base_host: str) -> bool:
    """同域名判断（含 www 前缀互认，忽略端口）。"""
    n = netloc.lower().split(":")[0]
    b = base_host.lower().split(":")[0]
    if n == b:
        return True
    if b.startswith("www."):
        return n == b[4:]
    if n.startswith("www."):
        return n[4:] == b
    return False


class WebsiteCollector(Collector):
    """type: website 的源。"""

    def collect(self) -> list[Article]:
        list_url = str(self.source.get("list_url", "")).strip()
        if not list_url:
            logger.error("[{}] 缺少 list_url 配置", self.source_id)
            return []
        selector = str(self.source.get("article_selector", "a"))
        try:
            resp = fetch(list_url, self.cfg)
        except Exception as exc:
            logger.error("[{}] 列表页拉取失败 {}: {}", self.source_id, list_url, exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        links = soup.select(selector)
        seen: set[str] = set()
        articles: list[Article] = []
        base_host = urlparse(list_url).netloc.lower()

        for a in links:
            if len(articles) >= self.limit:
                break
            href = (a.get("href") or "").strip()
            if not _is_article_link(href):
                continue
            url = urljoin(list_url, href)
            parsed = urlparse(url)
            # 仅保留同站点链接，避免抓到导航外链
            if base_host and not _same_host(parsed.netloc, base_host):
                continue
            if url in seen:
                continue
            seen.add(url)
            title = a.get_text(" ", strip=True)
            if not title:
                continue
            # 列表层日期线索：链接文本（如"标题 2026-08-26"）与 URL 路径（如 /2026-09/10/）
            published = (parse_date_from_text(title, default_year=date.today().year)
                         or parse_date_from_text(a.parent.get_text(" ", strip=True)
                                                 if a.parent else "", default_year=date.today().year)
                         or parse_date_from_url(url))
            articles.append(Article(
                url=url, title=title,
                source_id=self.source_id, source_name=self.source_name,
                published_at=(datetime.combine(published, time.min, tzinfo=timezone.utc)
                              if published else None),
            ))
        logger.info("[{}] 网站采集 {} 条 @ {}（选择器 {}）", self.source_id,
                    len(articles), list_url, selector)
        return articles
