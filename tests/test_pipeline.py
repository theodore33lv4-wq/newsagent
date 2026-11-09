"""pipeline 集成测试（monkeypatch 收集与下载，真实验证编排全链路）。

覆盖：正常入档、页面体检过滤、目标周过滤、去重、limit 语义、dry-run、regen。
"""

from datetime import datetime, timezone

from bs4 import BeautifulSoup

from newsagent.archive.downloader import FetchedContent
from newsagent.archive.store import Store
from newsagent.collect.base import Article, Collector
from newsagent.pipeline import run_pipeline

# 目标周固定为 2026-W35（8/24~8/30）
IN_WEEK = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
OUT_WEEK = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
LONG_BODY = "某市持续推进智能交通建设，路口信号优化与车路协同试点同步展开。" * 20

ARTICLE_HTML = ('<html><head><title>{title}</title>'
                '<meta property="article:published_time" content="{date}T09:00:00+08:00">'
                '</head><body><article><p>{body}</p></article></body></html>')

FAKE_ARTICLES = [
    Article(url="https://www.sohu.com/a/1", title="车路云一体化试点启动",
            source_id="fake", source_name="IT测试", published_at=IN_WEEK),
    Article(url="https://www.7its.com/a/2", title="集成商中标信号项目",
            source_id="fake", source_name="IT测试", published_at=IN_WEEK),
    Article(url="https://www.7its.com/a/3", title="无关娱乐新闻",
            source_id="fake", source_name="IT测试", published_at=IN_WEEK),
]


class FakeCollector(Collector):
    def __init__(self, cfg, source, articles=None):
        super().__init__(cfg, source or {})
        self._articles = articles if articles is not None else FAKE_ARTICLES

    def collect(self):
        return list(self._articles)


def _fake_content(article, date_str="2026-08-25", body: str | None = None):
    """正文默认随标题变化（用于验证内容哈希查重只在真正重复时触发）。"""
    text = body if body is not None else f"【{article.title}】{LONG_BODY}"
    return FetchedContent(
        html=ARTICLE_HTML.format(title=article.title, date=date_str, body=text),
        text=text, extractor="bs4",
        meta_title=article.title, meta_date=date_str)


def _patch(monkeypatch, articles=None, date_str="2026-08-25"):
    import newsagent.pipeline as pipe
    monkeypatch.setattr(
        pipe, "build_collectors",
        lambda c, errors=None: [FakeCollector(c, {"id": "fake", "name": "IT测试",
                                     "type": "website", "limit": 10}, articles)])
    monkeypatch.setattr(pipe, "download_and_extract",
                        lambda a, c: _fake_content(a, date_str))


def test_pipeline_end_to_end(cfg, monkeypatch):
    _patch(monkeypatch)
    stats = run_pipeline(cfg, week="2026-W35")
    assert stats.candidates == 3
    assert stats.new_articles == 3
    assert stats.archived == 3 and stats.archived_failed == 0
    assert stats.classified == 3
    assert "打标批次进度 1/1" in (cfg.data_dir / "logs/2026-W35.log").read_text(encoding="utf-8")
    assert stats.relevant >= 1
    assert stats.report_paths.get("html_path", None) is not None
    html = stats.report_paths["html_path"].read_text(encoding="utf-8")
    table = BeautifulSoup(html, "lxml").find("table")
    date_column = [th.get_text(strip=True) for th in table.select("th")].index("日期") + 1
    dates = [cell.get_text(strip=True) for cell in table.select(f"tbody tr td:nth-child({date_column})")]
    assert dates == ["2026-08-25"] * 3

    # 重复运行：全部去重，不再新增
    stats2 = run_pipeline(cfg, week="2026-W35")
    assert stats2.new_articles == 0
    assert stats2.archived == 0


def test_pipeline_filters_non_news_pages(cfg, monkeypatch):
    """栏目页（模板标题）与聚合汇总贴必须被过滤掉。"""
    articles = [
        Article(url="https://www.7its.com/index.php?aid=21748",
                title="智能交通|智慧高速|智慧交运|车路协同_赛文交通网",
                source_id="fake", source_name="赛文交通网·资讯", published_at=IN_WEEK),
        Article(url="https://www.sohu.com/a/9", title="自动驾驶主题汇总（2026-09-03更新）",
                source_id="fake", source_name="智能交通技术", published_at=IN_WEEK),
        Article(url="https://www.sohu.com/a/10", title="某市车路协同试点落地",
                source_id="fake", source_name="IT测试", published_at=IN_WEEK),
    ]
    _patch(monkeypatch, articles=articles)
    stats = run_pipeline(cfg, week="2026-W35")
    assert stats.rejected == 2
    assert stats.reject_reasons.get("template_title") == 1
    assert stats.reject_reasons.get("aggregate_title") == 1
    assert stats.archived == 1 and stats.classified == 1


def test_pipeline_archives_older_articles_in_their_own_week(cfg, monkeypatch):
    """旧闻不进入目标周周报，仍在真实归属周保留。"""
    articles = [
        Article(url="https://www.sohu.com/a/21", title="上周的旧闻",
                source_id="fake", source_name="IT测试", published_at=OUT_WEEK),
        Article(url="https://www.sohu.com/a/22", title="本周新闻",
                source_id="fake", source_name="IT测试", published_at=IN_WEEK),
    ]
    _patch(monkeypatch, articles=articles, date_str="2026-08-20")
    stats = run_pipeline(cfg, week="2026-W35", limit=1)
    assert stats.out_of_week == 1 and stats.archived == 0
    assert stats.historical == 1 and not stats.report_paths
    # 第二轮补跑剩下的旧闻。
    stats2 = run_pipeline(cfg, week="2026-W35")
    assert stats2.new_articles == 1 and stats2.archived == 0 and stats2.out_of_week == 1
    stats3 = run_pipeline(cfg, week="2026-W35")
    assert stats3.new_articles == 0
    pending = Store(cfg.data_dir).query(week="2026-W34", status="archived", relevant_only=False)
    assert len(pending) == 2 and stats.classified == stats2.classified == 0


def test_pipeline_dry_run_no_writes(cfg, monkeypatch):
    _patch(monkeypatch)
    stats = run_pipeline(cfg, week="2026-W35", dry_run=True)
    assert stats.candidates == 3 and stats.new_articles == 3
    assert stats.archived == 0
    assert not cfg.data_dir.exists()


def test_pipeline_keeps_same_title_from_different_sources(cfg, monkeypatch):
    articles = [
        Article(url="https://www.mot.gov.cn/xinwen/202609/t20260910_1.html",
                title="山西主骨架公路5年内实现数字化升级",
                source_id="fake", source_name="交通运输部", published_at=IN_WEEK),
        Article(url="https://www.zgjtb.com/2026-09/10/content_536613.html",
                title="山西主骨架公路5年内实现数字化升级",
                source_id="fake", source_name="中国交通新闻网", published_at=IN_WEEK),
    ]
    _patch(monkeypatch, articles=articles)
    stats = run_pipeline(cfg, week="2026-W35")
    assert stats.candidates == 2
    assert stats.new_articles == 2
    assert stats.archived == 2 and stats.classified == 2


def test_pipeline_dedupes_same_content(cfg, monkeypatch):
    """跨源全文转载各自入档，报告只显示一份。"""
    articles = [
        Article(url="https://a.com/1", title="山西公路数字化升级提速",
                source_id="fake", source_name="源A", published_at=IN_WEEK),
        Article(url="https://b.com/2", title="山西公路数字化升级提速转载版",
                source_id="fake", source_name="源B", published_at=IN_WEEK),
    ]
    same_body = "同一篇通稿正文内容。" * 30

    import newsagent.pipeline as pipe
    monkeypatch.setattr(
        pipe, "build_collectors",
        lambda c, errors=None: [FakeCollector(c, {"id": "fake", "name": "IT测试",
                                     "type": "website", "limit": 10}, articles)])
    monkeypatch.setattr(pipe, "download_and_extract",
                        lambda a, c: _fake_content(a, body=same_body))
    stats = run_pipeline(cfg, week="2026-W35")
    assert stats.archived == 2
    assert stats.duplicates == 1
    assert stats.classified == 2
    assert len(stats.report_paths["data"].items) == 1


def test_pipeline_limit_keeps_remaining(cfg, monkeypatch):
    """limit 截断的条目不应被登记为已见：下次运行仍可处理。"""
    _patch(monkeypatch)
    stats1 = run_pipeline(cfg, week="2026-W35", limit=1)
    assert stats1.new_articles == 1 and stats1.archived == 1
    stats2 = run_pipeline(cfg, week="2026-W35", limit=10)
    assert stats2.new_articles == 2
    assert stats2.archived == 2
    stats3 = run_pipeline(cfg, week="2026-W35")
    assert stats3.new_articles == 0


def test_pipeline_defers_next_week_articles(cfg, monkeypatch):
    """周一运行采集到"新一周"的新闻（发布于目标周之后）应留待该周周报，不能被丢弃。"""
    articles = [
        Article(url="https://a.com/next1", title="新一周的新闻一",
                source_id="fake", source_name="IT测试", published_at=IN_WEEK),
    ]
    # 目标周 W35（8/24~8/30），文章实际发布于 8/31（W36）
    _patch(monkeypatch, articles=articles, date_str="2026-08-31")
    stats = run_pipeline(cfg, week="2026-W35")
    assert stats.out_of_week == 1 and stats.deferred == 1
    assert stats.archived == 0

    st = Store(cfg.data_dir)
    rows = st.query(week="2026-W36", relevant_only=False)
    # 归属 W36，只在处理 W36 时打标。
    assert len(rows) == 1 and rows[0]["week"] == "2026-W36"
    assert rows[0]["status"] == "archived" and rows[0]["relevance"] is None
    assert stats.classified == 0

    # 跑 W36 时该条正常进入 W36 周报
    stats2 = run_pipeline(cfg, week="2026-W36")
    assert stats2.report_paths.get("html_path") is not None
    assert stats2.errors == []
    assert "新一周的新闻一" in stats2.report_paths["html_path"].read_text(encoding="utf-8")


def test_pipeline_no_new_items_not_an_error(cfg, monkeypatch):
    """目标周已有归档条目时，本轮没有新条目属正常情况，不应报错。"""
    articles = [
        Article(url="https://a.com/x1", title="本周新闻",
                source_id="fake", source_name="IT测试", published_at=IN_WEEK),
    ]
    _patch(monkeypatch, articles=articles)
    assert run_pipeline(cfg, week="2026-W35").archived == 1
    # 第二轮：无新条目，但应基于已有条目重新生成综述，且不产生错误
    stats = run_pipeline(cfg, week="2026-W35")
    assert stats.archived == 0
    assert stats.report_paths.get("html_path") is not None
    assert stats.errors == []


def test_pipeline_regen_no_data(cfg):
    stats = run_pipeline(cfg, week="2026-W35", regen=True)
    assert stats.errors
    assert stats.report_paths == {}


def test_cli_returns_error_when_classification_fails(cfg, monkeypatch):
    from newsagent import cli, pipeline
    from newsagent.utils.config import Config

    class OfflineModel:
        def chat(self, *args, **kwargs):
            raise RuntimeError("simulated outage")

    _patch(monkeypatch, articles=FAKE_ARTICLES[:1])
    monkeypatch.setattr(Config, "load", lambda: cfg)
    monkeypatch.setattr(pipeline, "create_provider", lambda _: OfflineModel())
    assert cli.main(["--week", "2026-W35"]) == 1
    assert not (cfg.data_dir / "reports").exists()
    assert Store(cfg.data_dir).query(status="archived", relevant_only=False)


def test_pipeline_routes_model_date_back_to_historical_week(cfg, monkeypatch):
    from newsagent import pipeline
    from newsagent.classify.llm import MockLLMProvider

    article = Article("https://a.com/undated", "待确认日期的新闻", "fake", "测试源")
    store = Store(cfg.data_dir)
    store.save_article("2026-W35", article, FetchedContent("<html/>", LONG_BODY, "bs4", None, None))

    class DatedModel(MockLLMProvider):
        def chat(self, *args, **kwargs):
            return super().chat(*args, **kwargs).replace('"published_date": null', '"published_date": "2026-08-25"')

    _patch(monkeypatch, articles=[])
    skipped = pipeline.run_pipeline(cfg, week="2026-W36", collect=False, provider=DatedModel())
    assert skipped.classified == 0
    cfg.classify["process_backlog"] = True
    stats = pipeline.run_pipeline(cfg, week="2026-W36", provider=DatedModel())
    assert stats.classified == 1 and stats.historical == 1 and not stats.errors
    assert store.get_week("2026-W35")[0]["published_at"] == "2026-08-25"


def test_pipeline_retries_missing_body_after_article_leaves_source(cfg, monkeypatch):
    from newsagent import pipeline

    article = FAKE_ARTICLES[0]
    _patch(monkeypatch, articles=[article])
    monkeypatch.setattr(pipeline, "download_and_extract",
                        lambda a, c: FetchedContent("<html/>", None, "failed", None, None))
    failed = run_pipeline(cfg, week="2026-W35")
    assert failed.errors and failed.archived_failed == 1
    [row] = Store(cfg.data_dir).query(status="retryable", relevant_only=False)
    assert (cfg.data_dir / row["html_file"]).exists()

    _patch(monkeypatch, articles=[])
    retry = run_pipeline(cfg, week="2026-W35")
    assert retry.classified == 1 and not retry.errors and retry.report_paths


def test_cli_reports_partial_run_and_applies_root_log_level(cfg, monkeypatch, capsys):
    from newsagent import cli, pipeline
    from newsagent.utils.config import Config

    _patch(monkeypatch)
    assert run_pipeline(cfg, week="2026-W35").report_paths

    class FailedCollector(FakeCollector):
        def collect(self):
            raise OSError("模拟来源失败")

    monkeypatch.setattr(pipeline, "build_collectors", lambda c, errors=None: [FailedCollector(c, {})])
    monkeypatch.setattr(Config, "load", lambda: cfg)
    setup_calls = []
    monkeypatch.setattr(pipeline, "setup_logging", lambda *args, **kwargs: setup_calls.append(kwargs))
    assert cli.main(["--log-level", "warning", "pipeline", "--week", "2026-W35"]) == 1
    assert setup_calls == [{"level": "WARNING", "write_file": True}]
    assert "部分完成" in capsys.readouterr().out
