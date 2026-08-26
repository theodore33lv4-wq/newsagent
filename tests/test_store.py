"""Store 读写与查询接口测试（tmp 数据目录）。"""

from newsagent.archive.downloader import FetchedContent
from newsagent.archive.store import Store, guid_for
from newsagent.collect.base import Article


def _make_store(cfg):
    return Store(cfg.data_dir)


def test_save_and_dup(cfg, article, sample_html):
    st = _make_store(cfg)
    fc = FetchedContent(html=sample_html, text="正文文本车路协同",
                        extractor="trafilatura", meta_title="某城市智慧高速试点启动",
                        meta_date=None)
    row = st.save_article("2026-W09", article, fc)
    assert row["status"] == "archived"
    assert (cfg.data_dir / row["html_file"]).exists()
    assert (cfg.data_dir / row["text_file"]).exists()

    # URL 规范化后重复 → None
    dup = Article(url="https://www.sohu.com/a/111_222", title="另一个标题",
                  source_id="fake_sohu", source_name="测试源")
    assert st.save_article("2026-W09", dup, fc) is None


def test_update_and_query(cfg, article, sample_html):
    st = _make_store(cfg)
    fc = FetchedContent(html=sample_html, text="正文", extractor="trafilatura",
                        meta_title="标题", meta_date=None)
    row = st.save_article("2026-W09", article, fc)
    st.update_classification(row["guid"], relevance=1,
                             tags=["厂商动态/集成商动态"], summary="摘要",
                             keywords=["信号控制"], companies=["中控信息"],
                             importance=2)
    # 另一条（meta_title=None → 回退用 article.title 入库）
    a2 = Article(url="https://www.7its.com/news/1.html", title="集成商中标",
                 source_id="fake_sohu", source_name="测试源")
    r2 = st.save_article("2026-W09", a2, FetchedContent(
        html=sample_html, text="s2", extractor="trafilatura",
        meta_title=None, meta_date=None))
    st.update_classification(r2["guid"], relevance=1, tags=["智慧高速"],
                             summary="s2", keywords=[], companies=["银江技术"],
                             importance=3)

    assert len(st.get_week("2026-W09")) == 2
    assert len(st.query(tag="厂商动态/集成商动态")) == 1
    assert st.query(company="银江技术")[0]["title"] == "集成商中标"
    assert len(st.query(keyword="信号控制")) == 1
    assert len(st.query(importance=3)) == 1
    assert len(st.query(status="classified")) == 2
    assert len(st.query(relevant_only=False)) == 2
    # 未分类条目不进入 relevant_only 查询
    a3 = Article(url="https://www.7its.com/news/2.html", title="未分类",
                 source_id="fake_sohu", source_name="测试源")
    st.save_article("2026-W09", a3, fc)
    assert len(st.query(relevant_only=True)) == 2
    assert len(st.query(relevant_only=False)) == 3


def test_article_text_and_seen_keys(cfg, article, sample_html):
    st = _make_store(cfg)
    fc = FetchedContent(html=sample_html, text="存档正文内容 here",
                        extractor="trafilatura", meta_title="T", meta_date=None)
    row = st.save_article("2026-W09", article, fc)
    assert st.article_text(row) == "存档正文内容 here"
    ukeys, tkeys = st.seen_keys()
    assert len(ukeys) == 1 and len(tkeys) == 1


def test_guid(cfg):
    assert guid_for("https://a.com/x") == guid_for("https://A.com/x")
