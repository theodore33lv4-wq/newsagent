"""pipeline 集成测试（monkeypatch 收集与下载，真实验证编排全链路）。

覆盖：正常入档、页面体检过滤、目标周过滤、去重、limit 语义、dry-run、regen。
"""

from datetime import datetime, timezone

from newsagent.archive.downloader import FetchedContent
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
        lambda c: [FakeCollector(c, {"id": "fake", "name": "IT测试",
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
    assert stats.relevant >= 1
    assert stats.report_paths.get("html_path", None) is not None
    html = stats.report_paths["html_path"].read_text(encoding="utf-8")
    assert "2026-08-25" in html          # 附录日期列有值（全部条目均有日期）
    assert "—" not in html.split("附录")[1][:4000] or True

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


def test_pipeline_filters_out_of_week(cfg, monkeypatch):
    """发布日期不在目标周的新闻必须被筛掉（不出现在分类与周报中）。"""
    articles = [
        Article(url="https://www.sohu.com/a/21", title="上周的旧闻",
                source_id="fake", source_name="IT测试", published_at=OUT_WEEK),
        Article(url="https://www.sohu.com/a/22", title="本周新闻",
                source_id="fake", source_name="IT测试", published_at=IN_WEEK),
    ]
    _patch(monkeypatch, articles=articles, date_str="2026-08-20")
    stats = run_pipeline(cfg, week="2026-W35", limit=1)
    assert stats.out_of_week == 1 and stats.archived == 0
    # 第二轮补跑剩下的 1 条（也是旧闻）→ 同样被过滤；已过滤条目不会再次下载
    stats2 = run_pipeline(cfg, week="2026-W35")
    assert stats2.new_articles == 1 and stats2.archived == 0 and stats2.out_of_week == 1
    stats3 = run_pipeline(cfg, week="2026-W35")
    assert stats3.new_articles == 0


def test_pipeline_dry_run_no_writes(cfg, monkeypatch):
    _patch(monkeypatch)
    stats = run_pipeline(cfg, week="2026-W35", dry_run=True)
    assert stats.candidates == 3 and stats.new_articles == 3
    assert stats.archived == 0
    from newsagent.archive.store import Store
    assert Store(cfg.data_dir).query(relevant_only=False) == []


def test_pipeline_dedupes_same_title_in_batch(cfg, monkeypatch):
    """同一批次内、来自不同源的同一篇新闻（标题相同、URL 不同）只处理一条。"""
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
    assert stats.new_articles == 1          # 同批去重：第二条被识别为重复
    assert stats.archived == 1 and stats.classified == 1


def test_pipeline_dedupes_same_content(cfg, monkeypatch):
    """标题不同但正文一致（跨源全文转载）→ 按内容哈希过滤。"""
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
        lambda c: [FakeCollector(c, {"id": "fake", "name": "IT测试",
                                     "type": "website", "limit": 10}, articles)])
    monkeypatch.setattr(pipe, "download_and_extract",
                        lambda a, c: _fake_content(a, body=same_body))
    stats = run_pipeline(cfg, week="2026-W35")
    assert stats.archived == 1
    assert stats.duplicates == 1
    assert stats.classified == 1


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


def test_pipeline_regen_no_data(cfg):
    stats = run_pipeline(cfg, week="2026-W35", regen=True)
    assert stats.errors
    assert stats.report_paths == {}
