"""采集器使用本地响应样本测试分页和来源状态。"""

import json
from types import SimpleNamespace

from newsagent.collect import gather_all
from newsagent.collect.rss import RSSCollector
from newsagent.collect.sohu import SohuAccountCollector
from newsagent.collect.websites import WebsiteCollector


def test_website_follows_next_page_and_preserves_articles_on_failure(cfg, monkeypatch):
    first = "https://example.com/news/"
    second = first + "?page=2"
    pages = {
        first: '<article><a href="/202612/t20261231_123.html">年末报道</a></article>'
               '<a rel="next" href="?page=2">下一页</a>',
        second: '<article><a href="/202701/t20270101_124.html">跨年报道</a></article>'
                '<a rel="next" href="?page=3">下一页</a>',
    }

    def fetch(url, cfg):
        if url not in pages:
            raise OSError("模拟分页故障")
        return SimpleNamespace(text=pages[url])

    monkeypatch.setattr("newsagent.collect.websites.fetch", fetch)
    site = WebsiteCollector(cfg, {"id": "site", "list_url": first, "limit": 10,
                                  "article_selector": "article a[href*='/t20'][href$='.html']"})
    result = gather_all(cfg, [site])
    assert [a.title for a in result.articles] == ["年末报道", "跨年报道"]
    assert len(result.errors) == 1 and "分页" in result.errors[0]


def test_empty_feed_and_failed_feed_are_distinct(cfg, monkeypatch):
    rss = RSSCollector(cfg, {"id": "rss", "url": "https://example.com/rss"})
    body = b'<rss version="2.0"><channel><title>News</title></channel></rss>'
    monkeypatch.setattr("newsagent.collect.rss.fetch", lambda *a: SimpleNamespace(content=body))
    result = gather_all(cfg, [rss])
    assert not result.errors and not result.articles
    body = b"<html>Blocked</html>"
    result = gather_all(cfg, [rss])
    assert result.errors and not result.articles


def test_sohu_uses_api_pages_to_extend_profile_results(cfg, monkeypatch):
    def article(idx):
        return {"id": idx, "authorName": "来源", "title": f"新闻 {idx}", "url": f"https://www.sohu.com/a/{idx}"}

    def fetch(url, *args, **kwargs):
        if "profile?" in url:
            return SimpleNamespace(text="window.blockRenderData = " + json.dumps([article(1)]))
        if "pageNo=1" in url:
            return SimpleNamespace(text=json.dumps([article(1)]))
        if "pageNo=2" in url:
            return SimpleNamespace(text=json.dumps([article(2)]))
        raise AssertionError(url)

    monkeypatch.setattr("newsagent.collect.sohu.fetch", fetch)
    collector = SohuAccountCollector(cfg, {"xpt": "sample", "profile_url": "https://example.com/profile?xpt=sample",
                                           "limit": 2})
    result = gather_all(cfg, [collector])
    assert [a.title for a in result.articles] == ["新闻 1", "新闻 2"]
    assert not result.errors and result.coverage_warnings
