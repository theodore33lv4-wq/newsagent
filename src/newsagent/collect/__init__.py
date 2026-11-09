"""newsagent.collect —— 新闻采集（多源适配器 + 去重）。

gather_all(cfg) 按 sources.yaml 的 enabled 源装配采集器，多源拉取并返回候选列表。
"""

from __future__ import annotations

from dataclasses import dataclass, field

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


def build_collectors(cfg: Config, errors: list[str] | None = None) -> list[Collector]:
    collectors: list[Collector] = []
    for source in cfg.sources_enabled():
        stype = str(source.get("type", "")).strip()
        cls = _TYPE_MAP.get(stype)
        if cls is None:
            logger.warning("未知源类型 {}（source {}），已跳过", stype, source.get("id"))
            if errors is not None:
                errors.append(f"来源 {source.get('id')} 的类型无效: {stype}")
            continue
        collectors.append(cls(cfg, source))
    # 搜索补充：配置启用时追加
    if cfg.collect.get("search", {}).get("enabled"):
        collectors.append(SearchCollector(cfg))
    logger.info("装配采集器 {} 个", len(collectors))
    return collectors


@dataclass
class CollectionResult:
    articles: list[Article] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    coverage_warnings: list[str] = field(default_factory=list)


def gather_all(cfg: Config, collectors: list[Collector] | None = None) -> CollectionResult:
    """拉取全部启用源，保留候选、来源异常和覆盖限制。"""
    result = CollectionResult()
    collectors = collectors if collectors is not None else build_collectors(cfg, result.errors)
    for i, collector in enumerate(collectors):
        if i > 0:
            polite_sleep(cfg)
        collector.errors.clear()
        collector.coverage_warning = None
        try:
            result.articles.extend(collector.collect())
        except Exception as exc:
            result.errors.append(f"来源 {collector.source_id} 采集失败: {exc}")
        result.errors.extend(f"来源 {collector.source_id}: {message}" for message in collector.errors)
        if collector.coverage_warning:
            result.coverage_warnings.append(f"{collector.source_id}: {collector.coverage_warning}")
    logger.info("采集完成：共 {} 条候选", len(result.articles))
    return result


__all__ = ["Article", "Collector", "build_collectors", "gather_all"]
