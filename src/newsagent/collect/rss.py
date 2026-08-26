"""RSS 订阅采集器（feedparser）。"""

from __future__ import annotations

from datetime import datetime, timezone

import feedparser
from loguru import logger

from .base import Article, Collector, fetch


class RSSCollector(Collector):
    """type: rss 的源。取 entries 的 link/title/published。"""

    def collect(self) -> list[Article]:
        url = str(self.source.get("url", "")).strip()
        if not url:
            logger.error("[{}] 缺少 url 配置", self.source_id)
            return []
        try:
            resp = fetch(url, self.cfg)
        except Exception as exc:
            logger.error("[{}] RSS 拉取失败 {}: {}", self.source_id, url, exc)
            return []

        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            logger.warning("[{}] RSS 解析异常: {}", self.source_id,
                           getattr(feed, "bozo_exception", "unknown"))

        articles: list[Article] = []
        for entry in feed.entries[: self.limit]:
            link = (entry.get("link") or "").strip()
            title = (entry.get("title") or "").strip()
            if not link or not title:
                continue
            published = None
            t = entry.get("published_parsed") or entry.get("updated_parsed")
            if t:
                try:
                    published = datetime(*t[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    published = None
            articles.append(Article(
                url=link, title=title,
                source_id=self.source_id, source_name=self.source_name,
                published_at=published,
            ))
        logger.info("[{}] RSS 采集 {} 条（源共 {}）", self.source_id,
                    len(articles), len(feed.entries))
        return articles
