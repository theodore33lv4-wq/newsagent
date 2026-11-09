"""固定网站列表页采集器（通用规则：列表页 + 文章链接选择器）。

配置示例（sources.yaml）：
  type: website
  list_url: https://www.example.com/news/
  article_selector: "h3 a"        # CSS 选择器，可选（默认 a）
"""

from __future__ import annotations

from datetime import datetime, time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from loguru import logger

from ..utils.dateparse import normalize_date, parse_date_from_url
from ..utils.dates import now_local, tz_for
from .base import Article, Collector, fetch, polite_sleep

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
            raise ValueError("缺少 list_url 配置")
        selector = str(self.source.get("article_selector", "a"))
        next_selector = self.source.get("next_page_selector")
        max_pages = max(1, int(self.source.get("max_pages", 5)))
        page_url = list_url
        visited = set()
        seen: set[str] = set()
        articles: list[Article] = []
        base_host = urlparse(list_url).netloc
        self.coverage_warning = "列表中可见的文章范围未核实为整周"
        while page_url and page_url not in visited and len(visited) < max_pages:
            if visited:
                polite_sleep(self.cfg)
            visited.add(page_url)
            try:
                soup = BeautifulSoup(fetch(page_url, self.cfg).text, "lxml")
                links = soup.select(selector)
                if not links:
                    raise ValueError(f"选择器 {selector} 未匹配到文章")
                articles.extend(self._page_articles(links, page_url, base_host, seen,
                                                    self.limit - len(articles)))
                if not articles:
                    raise ValueError(f"选择器 {selector} 未匹配到文章")
                if len(articles) >= self.limit:
                    self.coverage_warning = f"已达到单源上限 {self.limit}"
                    break
                if next_selector:
                    next_link = soup.select_one(next_selector)
                else:
                    next_link = soup.select_one("a[rel~='next'], link[rel~='next'], a[title='下一页']")
                    if next_link is None:
                        next_link = next((a for a in soup.select("a[href]")
                                          if a.get_text(strip=True) in ("下一页", "下页")), None)
                href = next_link.get("href") if next_link else None
                page_url = urljoin(page_url, href) if href else ""
                if page_url:
                    parsed = urlparse(page_url)
                    if parsed.scheme not in ("http", "https") or not _same_host(parsed.netloc, base_host):
                        raise ValueError("分页链接指向站外或使用无效协议")
            except Exception as exc:
                if not articles:
                    raise
                self.errors.append(f"列表分页失败: {exc}")
                break
        if len(visited) == max_pages and page_url and len(articles) < self.limit:
            self.coverage_warning = f"已达到分页上限 {max_pages} 页"
        logger.info("[{}] 网站采集 {} 条 / {} 页", self.source_id, len(articles), len(visited))
        return articles

    def _page_articles(self, links, list_url: str, base_host: str, seen: set[str],
                       limit: int) -> list[Article]:
        articles = []
        tz_name = self.cfg.app.get("timezone")
        today = now_local(tz_name).date()
        for a in links:
            if len(articles) >= limit:
                break
            href = (a.get("href") or "").strip()
            if not _is_article_link(href):
                continue
            url = urljoin(list_url, href)
            parsed = urlparse(url)
            # 仅保留同站点链接，避免抓到导航外链
            if parsed.scheme not in ("http", "https") or not _same_host(parsed.netloc, base_host):
                continue
            if url in seen:
                continue
            seen.add(url)
            title = a.get_text(" ", strip=True)
            if not title:
                continue
            published = parse_date_from_url(url, today=today)
            if published is None:
                container = a.find_parent("li") or a.parent
                times = container.find_all("time") if container else []
                if len(times) == 1:
                    value = times[0].get("datetime") or times[0].get_text(" ", strip=True)
                    published = normalize_date(value, today=today, tz_name=tz_name)
            articles.append(Article(
                url=url, title=title,
                source_id=self.source_id, source_name=self.source_name,
                published_at=(datetime.combine(published, time.min, tzinfo=tz_for(tz_name))
                              if published else None),
            ))
        return articles
