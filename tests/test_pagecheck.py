"""新闻页体检测试：模板标题 / 聚合贴 / 长度 / 链接密度 / 日期抽取。"""

from datetime import date

from newsagent.archive.pagecheck import (extract_page_date, link_density,
                                         title_is_template, validate_page)

LONG_BODY = "某市启动车路云一体化试点项目。" * 40          # 远超 300 字
ARTICLE_HTML = ('<html><head><title>某市启动车路协同试点</title>'
                '<meta property="article:published_time" content="2026-09-10T09:00:00+08:00">'
                '</head><body><article><p>' + LONG_BODY + '</p></article></body></html>')
LISTING_HTML = ('<html><body>' + "".join(
    f'<li><a href="/a/{i}">这是一条栏目页链接标题内容比较长{i}</a></li>' for i in range(40)
) + '</body></html>')


def test_template_title_detection():
    assert title_is_template("智能交通|智慧高速|智慧交运|车路协同_赛文交通网", "赛文交通网·资讯")
    assert not title_is_template("张福生：不敢让孩子看信号灯过街是交通人的耻辱", "赛文交通网·资讯")


def test_link_density():
    assert link_density(ARTICLE_HTML) < 0.60
    assert link_density(LISTING_HTML) > 0.60


def test_validate_rejects_non_news():
    # 模板标题（栏目/专题页）
    v = validate_page("智能交通|智慧高速|智慧交运|车路协同_赛文交通网", LONG_BODY,
                      ARTICLE_HTML, "https://www.7its.com/index.php?aid=1",
                      source_name="赛文交通网·资讯")
    assert not v.ok and v.reason == "template_title"

    # 聚合汇总贴
    v = validate_page("自动驾驶主题汇总（2026-09-03更新）", LONG_BODY, ARTICLE_HTML,
                      "https://www.sohu.com/a/1", source_name="智能交通技术")
    assert not v.ok and v.reason == "aggregate_title"

    # 正文过短
    v = validate_page("短讯", "太短了", ARTICLE_HTML, "https://a.com/1", source_name="X")
    assert not v.ok and v.reason == "too_short"

    # 链接密度过高（列表/导航页）
    v = validate_page("某栏目", "标题" * 300, LISTING_HTML, "https://a.com/list",
                      source_name="X")
    assert not v.ok and v.reason == "high_link_density"


def test_validate_accepts_article_and_extracts_date():
    v = validate_page("某市启动车路协同试点", LONG_BODY, ARTICLE_HTML,
                      "https://www.zgjtb.com/2026-09/10/content_1.html",
                      source_name="中国交通新闻网")
    assert v.ok and v.publish_date == date(2026, 9, 10) and not v.date_missing

    # 页面无日期 → 由列表层日期兜底
    v2 = validate_page("标题", LONG_BODY, "<html><body>" + LONG_BODY + "</body></html>",
                       "https://a.com/x", source_name="X",
                       fallback_date=date(2026, 9, 8))
    assert v2.ok and v2.publish_date == date(2026, 9, 8)

    # 完全无日期 → ok 但标记待判定（交由模型兜底）
    v3 = validate_page("标题", LONG_BODY, "<html><body>" + LONG_BODY + "</body></html>",
                       "https://a.com/x", source_name="X")
    assert v3.ok and v3.date_missing


def test_extract_page_date_strategies():
    assert extract_page_date(ARTICLE_HTML, "https://a.com/1") == date(2026, 9, 10)
    assert extract_page_date("<html><body>发布时间：2026-09-09</body></html>",
                             "https://a.com/1") == date(2026, 9, 9)
    assert extract_page_date("<html></html>", "https://www.zgjtb.com/2026-09/08/content_1.html") \
        == date(2026, 9, 8)
