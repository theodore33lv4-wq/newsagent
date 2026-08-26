"""去重逻辑测试：URL 规范化 / 标题归一化 / DedupChecker。"""

from newsagent.collect.base import Article
from newsagent.collect.dedup import (DedupChecker, content_key, normalize_title,
                                     normalize_url, title_key, url_key)


def test_normalize_url_strips_tracking():
    a = "https://www.sohu.com/a/123_456?spm=abc&utm_source=test&id=9#frag"
    b = "https://WWW.SOHU.COM/a/123_456?id=9"
    assert normalize_url(a) == normalize_url(b)
    assert "#" not in normalize_url(a)


def test_normalize_url_trailing_slash():
    assert normalize_url("https://a.com/news/") == normalize_url("https://a.com/news")


def test_normalize_title():
    assert normalize_title("智慧交通  周报：车路协同「新进展」！") == \
        normalize_title("智慧交通周报:车路协同_新进展")


def test_keys_and_content():
    t = "智慧交通周报"
    assert title_key(t) == title_key("智慧交通 周报")
    assert url_key("https://a.com/x?utm_source=1") == url_key("https://a.com/x")
    assert content_key("车路协同试点") == content_key("车路协同试点！")


def test_dedup_checker(article):
    # 预置历史：URL 与标题各一条（对应 store.seen_keys() 装载的真实形态）
    chk = DedupChecker([url_key("https://www.sohu.com/a/111_222")],
                       [title_key(article.title)])
    assert not chk.is_new(article)                       # URL 重复
    dup_title = Article(url="https://other.com/a/9", title=article.title,
                        source_id="s", source_name="S")
    assert not chk.is_new(dup_title)                     # 标题重复
    fresh = Article(url="https://other.com/a/10", title="全新标题",
                    source_id="s", source_name="S")
    assert chk.is_new(fresh)
    kept = chk.dedupe([article, dup_title, fresh])
    assert len(kept) == 1 and kept[0].title == "全新标题"
