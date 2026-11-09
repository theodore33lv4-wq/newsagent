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
            raise ValueError("缺少 RSS URL")
        resp = fetch(url, self.cfg)
        feed = feedparser.parse(resp.content)
        if not feed.entries and (feed.bozo or not feed.version):
            raise ValueError(f"RSS 解析异常: {getattr(feed, 'bozo_exception', '无法识别订阅格式')}")
        self.coverage_warning = "覆盖范围以订阅源当前保留的条目为限"
        if len(feed.entries) > self.limit:
            self.coverage_warning += f"，已达到单源上限 {self.limit}"

        articles: list[Article] = []
        for entry in feed.entries[: self.limit]:
            link = (entry.get("link") or "").strip()
            title = (entry.get("title") or "").strip()
            if not link or not title:
                continue
            published = None
            t = entry.get("published_parsed")
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
