"""每周流水线编排：采集 → 去重 → 存档（页面体检 + 周归档） → 分类 → 综述 → 推送。

- 单源/单条失败不中断整体，错误记入 stats.errors 并在结束时汇总；
- dry-run：仅执行采集与去重预览，不做任何写入（用于采集链路验证）；
- --limit N：限制本轮处理条数（真实写入，用于小规模验证）；
- regen：跳过采集，仅对指定周重新生成综述。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from loguru import logger

from .archive.downloader import FetchedContent, download_and_extract
from .archive.pagecheck import validate_page
from .archive.store import Store
from .classify.llm import LLMProvider, create_provider
from .classify.tagger import Tagger
from .collect import Article, build_collectors, gather_all
from .collect.dedup import DedupChecker, url_key
from .report import write_report
from .utils.config import Config
from .utils.dateparse import normalize_date
from .utils.dates import iso_week, stored_date, target_week, week_range
from .utils.logging import setup_logging
from .utils.notify import notify_failure
from .utils.tags import is_vendor_tag


@dataclass
class RunStats:
    week: str
    candidates: int = 0
    new_articles: int = 0
    archived: int = 0
    archived_failed: int = 0
    rejected: int = 0                     # 非新闻页（页面体检或模型判定）
    duplicates: int = 0                   # 正文重复（跨源转载同一篇新闻）
    out_of_week: int = 0                  # 发布日期不在目标周
    deferred: int = 0                     # 属于更晚一周，留待该周周报
    historical: int = 0
    date_pending: int = 0                 # 日期待模型判定
    reject_reasons: dict = field(default_factory=dict)
    classified: int = 0
    relevant: int = 0
    vendor: int = 0
    classify_failed: int = 0
    report_paths: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    coverage_warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        logger.error("[pipeline] {}", msg)


def run_pipeline(cfg: Config, *, week: str | None = None, limit: int | None = None,
                 dry_run: bool = False, regen: bool = False, collect: bool = True,
                 provider: LLMProvider | None = None, log_level: str = "INFO") -> RunStats:
    tz_name = cfg.app.get("timezone")
    week = iso_week(week_range(week, tz_name)[0], tz_name) if week else target_week(None, tz_name)
    stats = RunStats(week=week)
    if dry_run and regen:
        raise ValueError("--dry-run 不能与 --regen 或 report 子命令同时使用")
    setup_logging(cfg.data_dir, week, level=log_level, write_file=not dry_run)
    store = Store(cfg.data_dir) if not dry_run else None

    def get_provider() -> LLMProvider | None:
        nonlocal provider
        try:
            provider = provider or create_provider(cfg)
        except Exception as exc:
            stats.add_error(f"模型初始化失败: {exc}")
        return provider

    def generate_report(rows: list[dict], llm: LLMProvider | None) -> None:
        try:
            stats.report_paths = write_report(cfg, llm, week, rows, load_text=store.article_text)
            data = stats.report_paths["data"]
            stats.duplicates = len(rows) - data.total_count
            if data.evidence_adjustments:
                stats.add_error(f"综述需复核：已标记 {data.evidence_adjustments} 处证据不足的表述")
            elif data.fallback:
                stats.add_error("综述已降级，部分内容使用存档摘要或统计补齐")
        except Exception as exc:
            stats.add_error(f"周报生成失败: {exc}")

    # ---------- regen：仅重新生成综述 ----------
    if regen:
        rows = store.get_week(week)
        if not rows:
            stats.add_error(f"周 {week} 没有已分类条目，无法重新生成综述")
        else:
            generate_report(rows, get_provider())
            stats.relevant = len(rows)
        _notify_errors(cfg, stats)
        return stats

    # ---------- 采集与去重 ----------
    logger.info("===== newsagent 流水线启动：{}（{}）=====", week,
                "dry-run 预览" if dry_run else "全流程")
    if collect:
        build_errors: list[str] = []
        result = gather_all(cfg, build_collectors(cfg, build_errors))
        candidates = result.articles
        stats.coverage_warnings = result.coverage_warnings
        for message in result.coverage_warnings:
            logger.warning("采集覆盖: {}", message)
        for error in build_errors + result.errors:
            stats.add_error(error)
    else:
        logger.info("已跳过采集（--no-collect）：仅处理库中待分类条目并重新生成周报")
        candidates = []
    stats.candidates = len(candidates)

    retry_dates = {}
    if collect and store is not None:
        for row in store.query(status="retryable", relevant_only=False, limit=-1):
            candidates.append(Article(row["url"], row["title"], row["source_id"], row["source_name"]))
            retry_dates[row["url_key"]] = stored_date(row["published_at"], tz_name)
    # 仅在下载前合并相同 URL。跨源副本各自入档，生成周报时再比对正文。
    checker = DedupChecker(Store.seen_urls(cfg.data_dir) if candidates else ())
    new_articles = []
    for a in candidates:
        if not checker.is_new(a):
            continue
        if limit and limit > 0 and len(new_articles) >= limit:
            break
        checker.add(a)
        new_articles.append(a)
    stats.new_articles = len(new_articles)

    if dry_run:
        logger.info("[dry-run] 候选 {} 条，其中新条目 {} 条（预览：不下载、不落库）",
                    stats.candidates, stats.new_articles)
        for a in new_articles[:20]:
            logger.info("[dry-run]   {} | {}", a.title[:40], a.url)
        if len(new_articles) > 20:
            logger.info("[dry-run]   …… 其余 {} 条省略", len(new_articles) - 20)
        return stats

    # ---------- 存档：并发下载 + 新闻页体检 + 发布周归档 ----------
    concurrency = int(cfg.collect.get("concurrency", 4))
    strict_week = bool(cfg.archive.get("strict_week", True))
    min_text_chars = int(cfg.archive.get("min_text_chars", 300))

    def note_week(article_week: str) -> None:
        if article_week != week:
            stats.out_of_week += 1
            if article_week > week:
                stats.deferred += 1
            else:
                stats.historical += 1

    def process_one(article):
        """返回 (处理结果, 原因, content, 发布日期)。"""
        fallback = (normalize_date(article.published_at, tz_name=tz_name)
                    or retry_dates.get(url_key(article.url)))
        try:
            content = download_and_extract(article, cfg)
        except Exception as exc:
            logger.error("[{}] 下载失败 {}: {}", article.source_id, article.url, exc)
            return "download_failed", str(exc), FetchedContent("", None, "failed", None, None), fallback

        if not content.text or not content.text.strip():
            return "extract_failed", None, content, fallback
        verdict = validate_page(
            getattr(content, "meta_title", None) or article.title,
            content.text, content.html, article.url,
            source_name=article.source_name, cfg=cfg,
            meta_date=content.meta_date, fallback_date=fallback, allow_short=True)

        if not verdict.ok:
            return "rejected", verdict.reason, content, None
        return "ok", None, content, verdict.publish_date

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        outcomes = list(pool.map(process_one, new_articles))

    for article, (result, reason, content, pub_date) in zip(new_articles, outcomes):
        if result in ("download_failed", "extract_failed"):
            failure = "下载失败" if result == "download_failed" else "正文提取失败"
            store.save_article(iso_week(pub_date, tz_name) if pub_date else week, article, content,
                               status="retryable", note=f"{failure}，等待重新下载",
                               published_iso=pub_date.isoformat() if pub_date else None)
            stats.archived_failed += 1
            stats.add_error(f"{failure}（下轮重试）: {article.url}")
            continue
        if result == "rejected":
            stats.rejected += 1
            stats.reject_reasons[reason or "unknown"] = \
                stats.reject_reasons.get(reason or "unknown", 0) + 1
            store.save_article(week, article, content, status="filtered",
                               note=f"页面体检未通过：{reason}", published_iso=None)
            logger.debug("[过滤] {} —— {}", reason, article.url)
            continue
        pub_week = iso_week(pub_date, tz_name) if pub_date else week
        note = "正文较短，由模型判断是否为新闻" if len(content.text.strip()) < min_text_chars else None
        row = store.save_article(pub_week, article, content, note=note,
                                 published_iso=pub_date.isoformat() if pub_date else None)
        if row is None:
            continue
        note_week(pub_week)
        if pub_date is None:
            stats.date_pending += 1
        elif pub_week == week:
            stats.archived += 1

    logger.info("存档阶段：目标周 {} / 历史周 {} / 后续周 {} / 非新闻页 {} / 日期待判定 {} / 失败 {}",
                stats.archived, stats.historical, stats.deferred, stats.rejected,
                stats.date_pending, stats.archived_failed)

    if stats.archived == 0 and stats.date_pending == 0:
        logger.warning("本轮没有新的可用存档条目，将基于已有条目生成综述")

    # ---------- 分类（LLM 批次打标 + 日期兜底） ----------
    process_backlog = collect and bool(cfg.classify.get("process_backlog", False))
    scope = None if process_backlog else week
    rows = store.query(week=scope, status="archived", relevant_only=False, limit=-1)
    rows.extend(store.query(week=week, status="date_pending", relevant_only=False, limit=-1))
    logger.info("打标范围：{}，待处理 {} 条", scope or "含其他周积压", len(rows))
    llm = get_provider() if rows else provider
    row_by_guid = {r["guid"]: r for r in rows}
    if rows:
        texts = {r["guid"]: store.article_text(r) for r in rows}
        available = [row for row in rows if texts[row["guid"]]]
        stats.classify_failed = len(rows) - len(available)
        if llm is None:
            stats.classify_failed += len(available)
            class_cons = []
        else:
            class_cons = Tagger(llm, cfg).classify_many(available, texts)
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
            row = row_by_guid[cls.guid]
            known_date = stored_date(row["published_at"], tz_name)
            pub = known_date or normalize_date(cls.published_date, tz_name=tz_name)
            if pub is None and strict_week and cls.relevant:
                store.mark_date_pending(cls.guid)
                stats.classify_failed += 1
                continue
            pub_week = iso_week(pub, tz_name) if pub else row["week"]
            if pub is not None and (known_date is None or pub_week != row["week"]):
                note_week(pub_week)
            if cls.relevant:
                stats.relevant += 1
                if any(is_vendor_tag(t) for t in cls.tags):
                    stats.vendor += 1
            store.update_classification(
                cls.guid, relevance=1 if cls.relevant else 0,
                tags=cls.tags if cls.relevant else [],
                summary=cls.summary, keywords=cls.keywords,
                companies=cls.companies if cls.relevant else [],
                importance=cls.importance if cls.relevant else None,
                published_iso=pub.isoformat() if pub else None,
                week=pub_week,
                note="发布日期未确认" if pub is None else "分类完成",
            )
            stats.classified += 1
        if stats.classify_failed:
            stats.add_error(f"{stats.classify_failed} 条新闻未完成分类（正文、日期或模型结果不可用）")

    # ---------- 综述 ----------
    week_rows = store.get_week(week)
    if week_rows:
        generate_report(week_rows, llm if rows else get_provider())
    else:
        stats.report_paths = {}
        logger.warning("本周无相关条目，未生成综述")

    # ---------- 周报推送（可选：钉钉工作通知给个人） ----------
    if cfg.notify.get("push_weekly") and stats.report_paths and not stats.errors:
        try:
            from .utils.dingtalk import push_weekly_report
            from .utils.dates import week_label_cn
            if not stats.report_paths.get("html_path"):
                raise ValueError("钉钉推送需要启用 HTML 报告")
            pushed = push_weekly_report(
                cfg, stats.report_paths["html_path"],
                title=f"智能交通新闻周报 {week}",
                subtitle=week_label_cn(week, cfg.app.get("timezone")),
            )
            if not pushed:
                stats.add_error("钉钉周报推送失败")
        except Exception as exc:  # noqa: BLE001 - 推送失败不得影响主流程
            stats.add_error(f"钉钉周报推送失败: {exc}")

    # ---------- 汇总与告警 ----------
    reasons = "、".join(f"{k}×{v}" for k, v in stats.reject_reasons.items()) or "无"
    logger.info("===== 流水线结束：候选 {} / 新条目 {} / 存档成功 {} / 正文重复 {} / "
                "非新闻页 {} / 不在目标周 {}（留待后续周报 {}）/ 日期待判定 {} / "
                "下载失败 {} / 分类 {} / 相关 {} / 厂商类 {} / 综述 {} =====",
                stats.candidates, stats.new_articles, stats.archived, stats.duplicates,
                stats.rejected, stats.out_of_week, stats.deferred, stats.date_pending,
                stats.archived_failed, stats.classified, stats.relevant,
                stats.vendor, "有" if stats.report_paths else "无")
    logger.info("过滤明细：{}", reasons)
    _notify_errors(cfg, stats)
    return stats


def _notify_errors(cfg: Config, stats: RunStats) -> None:
    if stats.errors:
        try:
            notify_failure(cfg, f"newsagent {stats.week} 运行异常", "\n".join(stats.errors[:10]))
        except Exception as exc:
            logger.warning("异常告警发送失败: {}", exc)
