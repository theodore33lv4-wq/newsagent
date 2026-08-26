"""pipeline 集成测试（monkeypatch 收集与下载，真实验证编排全链路）。"""

from newsagent.archive.downloader import FetchedContent
from newsagent.collect.base import Article, Collector
from newsagent.pipeline import run_pipeline

FAKE_ARTICLES = [
    Article(url="https://www.sohu.com/a/1", title="车路云一体化试点启动",
            source_id="fake", source_name="IT测试"),
    Article(url="https://www.7its.com/a/2", title="集成商中标信号项目",
            source_id="fake", source_name="IT测试"),
    Article(url="https://www.7its.com/a/3", title="无关娱乐新闻",
            source_id="fake", source_name="IT测试"),
]


class FakeCollector(Collector):
    def __init__(self, cfg, source):
        super().__init__(cfg, source or {})

    def collect(self):
        return list(FAKE_ARTICLES)


def test_pipeline_end_to_end(cfg, monkeypatch):
    import newsagent.pipeline as pipe
    monkeypatch.setattr(pipe, "build_collectors",
                        lambda c: [FakeCollector(c, {"id": "fake", "name": "IT测试",
                                                     "type": "website", "limit": 10})])
    monkeypatch.setattr(pipe, "download_and_extract",
                        lambda a, c: FetchedContent(
                            html="<html><body><p>" + a.title + "详情。</p></body></html>",
                            text=a.title + "详情。", extractor="bs4",
                            meta_title=a.title, meta_date=None))

    stats = run_pipeline(cfg, week="2026-W35")
    assert stats.candidates == 3
    assert stats.new_articles == 3
    assert stats.archived == 3 and stats.archived_failed == 0
    assert stats.classified == 3
    assert stats.relevant >= 1
    assert stats.report_paths.get("html_path", None) is not None
    assert stats.report_paths["html_path"].exists()

    # 重复运行：全部去重，不再新增
    stats2 = run_pipeline(cfg, week="2026-W35")
    assert stats2.new_articles == 0
    assert stats2.archived == 0


def test_pipeline_dry_run_no_writes(cfg, monkeypatch):
    import newsagent.pipeline as pipe
    monkeypatch.setattr(pipe, "build_collectors",
                        lambda c: [FakeCollector(c, {"id": "fake", "name": "IT测试",
                                                     "type": "website", "limit": 10})])
    stats = run_pipeline(cfg, week="2026-W35", dry_run=True)
    assert stats.candidates == 3 and stats.new_articles == 3
    assert stats.archived == 0
    # dry-run 不写库
    from newsagent.archive.store import Store
    assert Store(cfg.data_dir).query(relevant_only=False) == []


def test_pipeline_limit_keeps_remaining(cfg, monkeypatch):
    """limit 截断的条目不应被登记为已见：下次运行仍可处理。"""
    import newsagent.pipeline as pipe
    monkeypatch.setattr(pipe, "build_collectors",
                        lambda c: [FakeCollector(c, {"id": "fake", "name": "IT测试",
                                                     "type": "website", "limit": 10})])
    monkeypatch.setattr(pipe, "download_and_extract",
                        lambda a, c: FetchedContent(
                            html="<html><body><p>x</p></body></html>",
                            text=a.title, extractor="bs4",
                            meta_title=a.title, meta_date=None))
    stats1 = run_pipeline(cfg, week="2026-W35", limit=1)
    assert stats1.new_articles == 1 and stats1.archived == 1
    stats2 = run_pipeline(cfg, week="2026-W35", limit=10)
    assert stats2.new_articles == 2  # 剩余 2 条仍可处理（未被误判为已见）
    assert stats2.archived == 2
    stats3 = run_pipeline(cfg, week="2026-W35")
    assert stats3.new_articles == 0  # 全部处理完后再跑：零重复


def test_pipeline_regen_no_data(cfg):
    stats = run_pipeline(cfg, week="2026-W35", regen=True)
    assert stats.errors  # 无数据 → 报错信息
    assert stats.report_paths == {}
