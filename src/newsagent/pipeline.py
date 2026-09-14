"""每周流水线编排：采集 → 去重 → 存档（页面体检 + 周过滤） → 分类 → 综述 → 推送。

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
from .archive.pagecheck import validate_page
from .archive.store import Store
from .classify.llm import LLMProvider, create_provider
from .classify.tagger import Tagger
from .collect import build_collectors, gather_all
from .collect.dedup import DedupChecker
from .report import write_report
from .utils.config import Config
from .utils.dates import iso_week, target_week, week_range
from .utils.logging import setup_logging
from .utils.notify import notify_failure


@dataclass
class RunStats:
    week: str
    candidates: int = 0
    new_articles: int = 0
    archived: int = 0
    archived_failed: int = 0
    rejected: int = 0                     # 非新闻页（页面体检或模型判定）
    out_of_week: int = 0                  # 发布日期不在目标周
    date_pending: int = 0                 # 日期待模型判定
    reject_reasons: dict = field(default_factory=dict)
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
    week = week or target_week(None, cfg.app.get("timezone"))
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

    # ---------- 存档：并发下载 + 新闻页体检 + 目标周过滤 ----------
    concurrency = int(cfg.collect.get("concurrency", 4))
    tz_name = cfg.app.get("timezone")
    week_start, week_end = week_range(week, tz_name)
    strict_week = bool(cfg.archive.get("strict_week", True))

    def in_target_week(d) -> bool:
        return week_start.date() <= d <= week_end.date()

    def process_one(article):
        """返回 (处理结果, 原因, content, 发布日期)。"""
        try:
            content = download_and_extract(article, cfg)
        except Exception as exc:
            logger.error("[{}] 下载失败 {}: {}", article.source_id, article.url, exc)
            return "download_failed", str(exc), None, None

        fallback = article.published_at.date() if article.published_at else None
        verdict = validate_page(
            getattr(content, "meta_title", None) or article.title,
            content.text, content.html, article.url,
            source_name=article.source_name, cfg=cfg,
            meta_date=content.meta_date, fallback_date=fallback)

        if not verdict.ok:
            return "rejected", verdict.reason, content, None
        if verdict.publish_date is None:
            return "date_pending", None, content, None          # 交由大模型判定日期
        if strict_week and not in_target_week(verdict.publish_date):
            return "out_of_week", verdict.publish_date.isoformat(), content, verdict.publish_date
        return "ok", None, content, verdict.publish_date

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        outcomes = list(pool.map(process_one, new_articles))

    for article, (result, reason, content, pub_date) in zip(new_articles, outcomes):
        if result == "download_failed":
            stats.archived_failed += 1
            stats.add_error(f"存档失败（下轮重试）: {article.url}")
            continue
        if result == "rejected":
            stats.rejected += 1
            stats.reject_reasons[reason or "unknown"] = \
                stats.reject_reasons.get(reason or "unknown", 0) + 1
            store.save_article(week, article, content, status="filtered",
                               note=f"页面体检未通过：{reason}", published_iso=None)
            logger.debug("[过滤] {} —— {}", reason, article.url)
            continue
        if result == "out_of_week":
            stats.out_of_week += 1
            store.save_article(iso_week(pub_date, tz_name), article, content,
                               status="filtered",
                               note=f"发布日期 {pub_date} 不在目标周 {week}",
                               published_iso=pub_date.isoformat())
            continue
        if result == "date_pending":
            stats.date_pending += 1
            store.save_article(week, article, content, status="archived",
                               note="发布日期待模型判定")
            continue
        row = store.save_article(week, article, content,
                                 published_iso=pub_date.isoformat() if pub_date else None)
        if row is None:
            continue
        stats.archived += 1

    logger.info("存档阶段：成功 {} / 非新闻页 {} / 不在目标周 {} / 日期待判定 {} / 下载失败 {}",
                stats.archived, stats.rejected, stats.out_of_week,
                stats.date_pending, stats.archived_failed)

    if stats.archived == 0 and stats.date_pending == 0:
        stats.add_error("本周没有可用的新存档条目，跳过分类与综述")

    # ---------- 分类（LLM 批次打标 + 日期兜底） ----------
    rows = store.query(week=week, status="archived", relevant_only=False)
    if rows:
        llm = provider or create_provider(cfg)
        tagger = Tagger(llm, cfg)
        texts = {r["guid"]: store.article_text(r) for r in rows}
        class_cons = tagger.classify_many(
            rows, texts,
            concurrency=int(cfg.classify.get("batch_concurrency", 2)))
        for cls in class_cons:
            if not cls.ok:
                stats.classify_failed += 1
                store.set_note(cls.guid, cls.error or "分类失败（待人工）")
                continue
            if not cls.is_article:
                stats.rejected += 1
                stats.reject_reasons["non_article_llm"] = \
                    stats.reject_reasons.get("non_article_llm", 0) + 1
                store.mark_filtered(cls.guid, "模型判定为非新闻页")
                continue
            # 日期兜底：列表/页面都没拿到日期时，用模型给出的发布日期判定归属周
            if not any(r["guid"] == cls.guid and r.get("published_at") for r in rows):
                if not cls.published_date:
                    store.mark_filtered(cls.guid, "无法确定发布日期")
                    stats.rejected += 1
                    stats.reject_reasons["no_date"] = \
                        stats.reject_reasons.get("no_date", 0) + 1
                    continue
                from datetime import date as _date
                try:
                    pub = _date.fromisoformat(cls.published_date)
                except ValueError:
                    pub = None
                if strict_week and pub and not in_target_week(pub):
                    stats.out_of_week += 1
                    store.set_published(cls.guid, pub.isoformat())
                    store.mark_filtered(cls.guid, f"发布日期 {pub} 不在目标周 {week}")
                    continue
                store.set_published(cls.guid, cls.published_date)
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

    # ---------- 周报推送（可选：钉钉工作通知给个人） ----------
    # 独立可选通道：任何异常只记录日志，绝不影响周报产物与本轮运行结果
    if cfg.notify.get("push_weekly") and stats.report_paths.get("html_path"):
        try:
            from .utils.dingtalk import push_weekly_report
            from .utils.dates import week_label_cn
            push_weekly_report(
                cfg, stats.report_paths["html_path"],
                title=f"智能交通新闻周报 {week}",
                subtitle=week_label_cn(week, cfg.app.get("timezone")),
            )
        except Exception as exc:  # noqa: BLE001 - 推送失败不得影响主流程
            logger.warning("周报推送异常（已忽略，不影响周报生成）: {}", exc)

    # ---------- 汇总与告警 ----------
    reasons = "、".join(f"{k}×{v}" for k, v in stats.reject_reasons.items()) or "无"
    logger.info("===== 流水线结束：候选 {} / 新条目 {} / 存档成功 {} / 非新闻页 {} / "
                "不在目标周 {} / 日期待判定 {} / 下载失败 {} / 分类 {} / 相关 {} / "
                "厂商动态 {} / 综述 {} =====",
                stats.candidates, stats.new_articles, stats.archived,
                stats.rejected, stats.out_of_week, stats.date_pending,
                stats.archived_failed, stats.classified, stats.relevant,
                stats.vendor, "有" if stats.report_paths else "无")
    logger.info("过滤明细：{}", reasons)
    if stats.errors:
        notify_failure(cfg, f"newsagent {week} 运行异常",
                       "\n".join(stats.errors[:10]))
    return stats
