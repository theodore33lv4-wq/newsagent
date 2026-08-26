"""每周流水线编排：采集 → 去重 → 存档 → 分类 → 索引 → 综述。

- 单源/单条失败不中断整体，错误记入 stats.errors 并在结束时汇总；
- dry-run：仅执行采集与去重预览，不做任何写入（用于采集链路验证）；
- --limit N：限制本轮处理条数（真实写入，用于小规模验证）；
- regen：跳过采集，仅对指定周重新生成综述。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from loguru import logger

from .archive.downloader import download_and_extract
from .archive.store import Store
from .classify.llm import LLMProvider, create_provider
from .classify.tagger import Tagger
from .collect import build_collectors, gather_all
from .collect.dedup import DedupChecker
from .report import write_report
from .utils.config import Config
from .utils.dates import iso_week
from .utils.logging import setup_logging
from .utils.notify import notify_failure


@dataclass
class RunStats:
    week: str
    candidates: int = 0
    new_articles: int = 0
    archived: int = 0
    archived_failed: int = 0
    classified: int = 0
    relevant: int = 0
    vendor: int = 0
    classify_failed: int = 0
    report_paths: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        logger.error("[pipeline] {}", msg)


def run_pipeline(cfg: Config, *, week: str | None = None, limit: int | None = None,
                 dry_run: bool = False, regen: bool = False,
                 provider: LLMProvider | None = None) -> RunStats:
    week = week or iso_week(None, cfg.app.get("timezone"))
    run_tag = f"{week}-dryrun" if dry_run else week
    setup_logging(cfg.data_dir, run_tag)

    store = Store(cfg.data_dir)
    stats = RunStats(week=week)

    # ---------- regen：仅重新生成综述 ----------
    if regen:
        rows = store.get_week(week)
        if not rows:
            stats.add_error(f"周 {week} 没有已分类条目，无法重新生成综述")
        else:
            llm = provider or create_provider(cfg)
            stats.report_paths = write_report(cfg, llm, week, rows)
            stats.relevant = len(rows)
        return stats

    # ---------- 采集与去重 ----------
    logger.info("===== newsagent 流水线启动：{}（{}）=====", week,
                "dry-run 预览" if dry_run else "全流程")
    candidates = gather_all(cfg, build_collectors(cfg))
    stats.candidates = len(candidates)

    checker = DedupChecker(*store.seen_keys())
    # 先过滤出新条目，再应用 limit；只有实际处理的条目才登记去重，
    # 避免 limit 截断的条目在下次运行被误判为"已见过"
    new_all = [a for a in candidates if checker.is_new(a)]
    if limit:
        new_articles = new_all[: max(0, int(limit))]
    else:
        new_articles = new_all
    for a in new_articles:
        checker.add(a)
    stats.new_articles = len(new_articles)

    if dry_run:
        logger.info("[dry-run] 候选 {} 条，其中新条目 {} 条（预览：不下载、不落库）",
                    stats.candidates, stats.new_articles)
        for a in new_articles[:20]:
            logger.info("[dry-run]   {} | {}", a.title[:40], a.url)
        if len(new_articles) > 20:
            logger.info("[dry-run]   …… 其余 {} 条省略", len(new_articles) - 20)
        return stats

    # ---------- 存档（并发下载） ----------
    concurrency = int(cfg.collect.get("concurrency", 4))

    def archive_one(article):
        try:
            content = download_and_extract(article, cfg)
            return store.save_article(week, article, content), article
        except Exception as exc:
            logger.error("[{}] 下载失败 {}: {}", article.source_id, article.url, exc)
            return None, article

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = list(pool.map(archive_one, new_articles))

    for row, article in results:
        if row is None:
            stats.archived_failed += 1
            stats.add_error(f"存档失败（下轮重试）: {article.url}")
        else:
            stats.archived += 1

    if stats.archived == 0:
        stats.add_error("本周没有新存档条目，跳过分类与综述")

    # ---------- 分类（LLM 打标） ----------
    rows = store.query(week=week, status="archived", relevant_only=False)
    if rows:
        llm = provider or create_provider(cfg)
        tagger = Tagger(llm, cfg)
        texts = {r["guid"]: store.article_text(r) for r in rows}
        class_cons = tagger.classify_many(
            rows, texts, concurrency=int(cfg.classify.get("concurrency", 4)))
        for cls in class_cons:
            if not cls.ok:
                stats.classify_failed += 1
                store.set_note(cls.guid, cls.error or "分类失败（待人工）")
                continue
            if cls.relevant:
                stats.relevant += 1
                if any(t.startswith("厂商动态") for t in cls.tags):
                    stats.vendor += 1
            store.update_classification(
                cls.guid, relevance=1 if cls.relevant else 0,
                tags=cls.tags if cls.relevant else [],
                summary=cls.summary, keywords=cls.keywords,
                companies=cls.companies if cls.relevant else [],
                importance=cls.importance if cls.relevant else None,
            )
        stats.classified = len(class_cons)

    # ---------- 综述 ----------
    week_rows = store.get_week(week)
    if week_rows:
        llm = provider or create_provider(cfg)
        stats.report_paths = write_report(cfg, llm, week, week_rows)
    else:
        stats.report_paths = {}
        logger.warning("本周无相关条目，未生成综述")

    # ---------- 汇总与告警 ----------
    logger.info("===== 流水线结束：候选 {} / 新条目 {} / 存档成功 {} / 失败 {} / "
                "分类 {} / 相关 {} / 厂商动态 {} / 综述 {} =====",
                stats.candidates, stats.new_articles, stats.archived,
                stats.archived_failed, stats.classified, stats.relevant,
                stats.vendor, "有" if stats.report_paths else "无")
    if stats.errors:
        notify_failure(cfg, f"newsagent {week} 运行异常",
                       "\n".join(stats.errors[:10]))
    return stats
