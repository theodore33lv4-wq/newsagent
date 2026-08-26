"""国内搜索引擎补充采集器（实验性，默认关闭）。

引擎：bing（cn.bing.com HTML 解析，国内可达性较好）| baidu_news（新闻搜索）。
提示：搜索页结构可能变化，失败仅记日志不影响主流程。
"""

from __future__ import annotations

from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup
from loguru import logger

from .base import Article, Collector, fetch

_BING_RESULT = ".b_algo h2 a"
_BAIDU_RESULT = ".result h3 a, .c-title a"  # 百度新闻/网页搜索常见结构


class SearchCollector(Collector):
    """type: search 的源（由 collect.__init__ 按 config 装配）。"""

    def __init__(self, cfg, source: dict | None = None):
        super().__init__(cfg, source or {})
        self.engine = str(cfg.collect.get("search", {}).get("engine", "bing"))
        self.keywords = list(cfg.collect.get("search", {}).get("keywords", []))
        self.per_keyword = int(cfg.collect.get("search", {}).get("max_results_per_keyword", 5))

    @property
    def source_id(self) -> str:
        return "search"

    @property
    def source_name(self) -> str:
        return f"搜索补充({self.engine})"

    def collect(self) -> list[Article]:
        if not self.keywords:
            return []
        articles: list[Article] = []
        for kw in self.keywords:
            try:
                articles.extend(self._search_one(kw, self.per_keyword))
            except Exception as exc:
                logger.warning("[search] 关键词“{}”失败: {}", kw, exc)
        # 站内去重
        seen: set[str] = set()
        out = []
        for a in articles:
            if a.url in seen:
                continue
            seen.add(a.url)
            out.append(a)
        logger.info("[search] 引擎 {} 共采集 {} 条 / {} 个关键词",
                    self.engine, len(out), len(self.keywords))
        return out

    def _search_one(self, keyword: str, limit: int) -> list[Article]:
        if self.engine == "baidu_news":
            url = f"https://news.baidu.com/ns?word={quote(keyword)}&tn=news"
            selector = _BAIDU_RESULT
        else:  # bing（默认）
            url = f"https://cn.bing.com/search?q={quote(keyword)}&ensearch=0"
            selector = _BING_RESULT

        resp = fetch(url, self.cfg)
        soup = BeautifulSoup(resp.text, "lxml")
        out: list[Article] = []
        for a in soup.select(selector)[:limit]:
            href = (a.get("href") or "").strip()
            title = a.get_text(" ", strip=True)
            if not href or not title or href.startswith(("javascript:", "#")):
                continue
            host = urlparse(href).netloc.lower()
            if host in ("", "cn.bing.com", "www.baidu.com", "www.sogou.com"):
                continue  # 跳过引擎自身/站内跳转壳页
            out.append(Article(
                url=href, title=title,
                source_id=self.source_id, source_name=self.source_name,
            ))
        logger.debug("[search] “{}” → {} 条", keyword, len(out))
        return out
