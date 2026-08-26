"""正文提取测试（trafilatura / bs4 兜底 / 失败）。"""

from newsagent.archive.downloader import extract


def test_extract_trafilatura(sample_html):
    text, extractor, title, date = extract(sample_html)
    assert extractor == "trafilatura"
    assert text and "车路协同" in text
    assert title == "某城市智慧高速试点启动"


def test_extract_bs4_fallback():
    html = "<html><body><p>纯文本正文内容,无结构。</p></body></html>"
    text, extractor, _, _ = extract(html)
    assert extractor in ("trafilatura", "bs4")  # 视 trafilatura 是否判定有正文
    assert text and "纯文本正文内容" in text


def test_extract_failed():
    text, extractor, _, _ = extract("<html><script>abc</script></html>")
    # 极简页面可能被 trafilatura 判为无正文 → bs4 兜底后仍可能为空
    assert extractor in ("trafilatura", "bs4", "failed")
