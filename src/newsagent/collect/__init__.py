"""newsagent.collect —— 新闻采集（多源适配器 + 去重）。

gather_all(cfg) 按 sources.yaml 的 enabled 源装配采集器，多源拉取并返回候选列表。
"""

from __future__ import annotations

from loguru import logger

from ..utils.config import Config
from .base import Article, Collector, polite_sleep
from .rss import RSSCollector
from .search import SearchCollector
from .sohu import SohuAccountCollector
from .websites import WebsiteCollector

_TYPE_MAP = {
    "sohu_account": SohuAccountCollector,
    "rss": RSSCollector,
    "website": WebsiteCollector,
}


def build_collectors(cfg: Config) -> list[Collector]:
    collectors: list[Collector] = []
    for source in cfg.sources_enabled():
        stype = str(source.get("type", "")).strip()
        cls = _TYPE_MAP.get(stype)
        if cls is None:
            logger.warning("未知源类型 {}（source {}），已跳过", stype, source.get("id"))
            continue
        collectors.append(cls(cfg, source))
    # 搜索补充：配置启用时追加
    if cfg.collect.get("search", {}).get("enabled"):
        collectors.append(SearchCollector(cfg))
    logger.info("装配采集器 {} 个", len(collectors))
    return collectors


def gather_all(cfg: Config, collectors: list[Collector] | None = None) -> list[Article]:
    """拉取全部启用源，返回候选 Article 列表（未去重）。"""
    collectors = collectors if collectors is not None else build_collectors(cfg)
    all_articles: list[Article] = []
    for i, collector in enumerate(collectors):
        if i > 0:
            polite_sleep(cfg)
        try:
            all_articles.extend(collector.collect())
        except Exception as exc:
            logger.error("[{}] 采集器异常，已跳过: {}", collector.source_id, exc)
    logger.info("采集完成：共 {} 条候选", len(all_articles))
    return all_articles


__all__ = ["Article", "Collector", "build_collectors", "gather_all"]
