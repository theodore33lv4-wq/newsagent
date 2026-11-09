"""综述模块测试：生成 / 降级 / HTML 渲染 / Word 导出。"""

import zipfile

from bs4 import BeautifulSoup

from newsagent.classify.llm import MockLLMProvider
from newsagent.report import write_report
from newsagent.report.export import to_docx
from newsagent.report.generator import ReportData, generate
from newsagent.report.render import render_html

ROWS = [
    {"guid": "a1", "title": "某市启动车路云一体化试点", "source_name": "ITS114智慧交通",
     "published_at": "2026-08-24", "tags": ["云脑与智能体/交通大模型", "交管技术与业务/信号控制优化"],
     "companies": ["华为"], "importance": 3, "summary": "试点启动。",
     "url": "https://www.sohu.com/a/1", "html_file": "raw/2026-W35/x.html"},
    {"guid": "a2", "title": "中控信息中标信号控制项目", "source_name": "赛文交通网",
     "published_at": "2026-08-25", "tags": ["市场与项目/厂商中标与订单"],
     "companies": ["中控信息"], "importance": 2, "summary": "中标。",
     "url": "https://www.7its.com/a/2", "html_file": None},
]


def test_generate_mock(cfg):
    data = generate(cfg, MockLLMProvider(), "2026-W35", ROWS, "2026-08-26 13:00")
    assert data.themes and not data.fallback
    # 主题 = 确定性复用打标 level-1 标签（数量降序、名称升序）
    titles = [t["title"] for t in data.themes]
    assert titles == ["云脑与智能体", "市场与项目"]
    # 每条要点来自 LLM（Mock notes），完整保留不截断
    all_notes = "".join(it["note"] for t in data.themes for it in t["items"])
    assert "常态化阶段" in all_notes
    assert "…" not in all_notes
    assert data.overview and data.overview_points
    assert all(1 <= i <= 2 for i in data.top5)
    # 趋势观察结构化为三块
    assert data.trends_macro and data.trends_projects and data.trends_vendor
    # 类别分布：数量总和 = 条目数
    assert sum(d["count"] for d in data.distribution) == len(data.items)
    assert data.distribution[0]["count"] >= data.distribution[-1]["count"]


def test_short_source_name(cfg):
    from newsagent.report.generator import _short_source
    assert _short_source("工信部·政策与公告（首页精选）") == "工信部"
    assert _short_source("赛文交通网·资讯（集成商内容密集）") == "赛文交通网"
    assert _short_source("交通运输部·交通要闻（政策类）") == "交通运输部"
    assert _short_source("ITS114智慧交通（搜狐号）") == "ITS114智慧交通"


def test_report_layer_dedupe(cfg):
    """报告层仅合并相同全文，保留同名异文。"""
    rows = [
        {"guid": "d1", "title": "山西主骨架公路5年内实现数字化升级",
         "source_name": "交通运输部", "published_at": "2026-09-10", "tags": ["政策标准与行业/国家政策法规"],
         "companies": [], "importance": 2, "summary": "s", "url": "https://a/1",
         "html_file": None, "content_hash": "v2:h1"},
        {"guid": "d2", "title": "山西主骨架公路5年内实现数字化升级",
         "source_name": "中国交通新闻网", "published_at": "2026-09-10", "tags": ["政策标准与行业/国家政策法规"],
         "companies": [], "importance": 2, "summary": "s", "url": "https://b/2",
         "html_file": None, "content_hash": "v2:h1"},
        {"guid": "d3", "title": "城市摆渡人变身治理合伙人",
         "source_name": "交通运输部", "published_at": "2026-09-11", "tags": ["交管技术与业务/信号控制优化"],
         "companies": [], "importance": 1, "summary": "s", "url": "https://a/3",
         "html_file": None, "content_hash": "v2:h3"},
    ]
    data = generate(cfg, MockLLMProvider(), "2026-W35", rows, "now")
    titles = [it["title"] for it in data.items]
    assert titles.count("山西主骨架公路5年内实现数字化升级") == 1
    assert len(data.items) == 2
    rows[1]["content_hash"] = "v2:h2"
    data = generate(cfg, MockLLMProvider(), "2026-W35", rows, "now")
    assert len(data.items) == 3


def test_long_notes_not_truncated(cfg):
    """要点超出字数上限时完整保留，不得用省略号截断。"""
    long_note = ("中控信息联合体中标某市智能交通信号控制系统项目，合同金额约 1.2 亿元，"
                 "覆盖全市 320 个路口，计划分三期完成改造并接入市级交通大脑平台。"
                 "项目要求统一接入既有电警、卡口与雷达数据，实现区域协调控制与自适应配时，"
                 "同时预留车路协同路侧设备接口，为后续示范区扩容做准备。")

    class LongNoteProvider:
        def chat(self, messages, **kw):
            import json as _json
            return _json.dumps({
                "notes": [{"idx": 1, "note": long_note}, {"idx": 2, "note": long_note}],
                "overview": "概览", "overview_points": ["要点"],
                "top5": [1], "trends": {"macro": ["m"], "projects": ["p"], "vendor_advice": ["v"]},
                "next_week": ["n"],
            }, ensure_ascii=False)
    cfg.report.summary_max_chars = 500
    rows = [{**row, "summary": long_note} for row in ROWS]
    data = generate(cfg, LongNoteProvider(), "2026-W35", rows, "now")
    notes = [it["note"] for t in data.themes for it in t["items"]]
    assert notes and all(n == long_note for n in notes)
    assert len(notes[0]) > 80 and "…" not in notes[0]


def test_themes_reuse_level1_tags(cfg):
    """主题直接复用打标 level-1 标签：LLM 完全不允许参与归类（零调用）。"""
    class NoLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, **kw):
            self.calls += 1
            raise RuntimeError("no llm allowed")

    prov = NoLLM()
    data = generate(cfg, prov, "2026-W35", ROWS, "now")
    titles = {t["title"] for t in data.themes}
    assert titles == {"云脑与智能体", "市场与项目"}
    assert data.fallback is True      # 综述降级（要点用摘要兜底）
    assert prov.calls == 1            # 要点与综述已合并为一次调用；归类零调用

    # 主题与附录标签一致（同一条新闻不会出现"归类冲突"）
    idx2_theme = next(t["title"] for t in data.themes
                      for it in t["items"] if it["idx"] == 2)
    assert idx2_theme == "市场与项目"


def test_generate_fallback(cfg):
    class Boom:
        def chat(self, *a, **k):
            raise RuntimeError("api down")

    data = generate(cfg, Boom(), "2026-W35", ROWS, "now")
    assert data.fallback is True
    assert data.themes  # 标签兜底分组
    assert data.overview_points  # 兜底要点
    assert "本周共收录" in data.overview


def test_render_html(cfg):
    data = generate(cfg, MockLLMProvider(), "2026-W35", ROWS, "now")
    html = render_html(data)
    assert "智能交通新闻周报" in html
    assert "类别分布" in html                      # 可视化章节
    assert "厂商与集成商动态" in html
    assert "宏观市场动态" in html and "重点工程与项目" in html and "集成商产品规划建议" in html
    assert "附录" in html
    assert "车路云" in html
    # 主题要点/厂商区链接改为原文 URL（不再指向附录锚点）
    assert 'href="https://www.sohu.com/a/1"' in html
    assert "#item-" not in html.replace('id="item-', '')  # 非锚点链接


def test_render_html_with_html_link(cfg):
    data = generate(cfg, MockLLMProvider(), "2026-W35", ROWS, "now")
    items = [dict(it, html_link="../../raw/2026-W35/x.html") for it in data.items]
    html = render_html(data, items=items)
    assert 'href="../../raw/2026-W35/x.html"' in html  # 存档相对路径可点击


def test_render_citation_numbers_as_safe_original_links(cfg):
    original = "https://example.com/news?id=1&from=report"
    rows = [{**ROWS[0], "url": original}, {**ROWS[1], "url": "javascript:alert(1)"}]
    data = generate(cfg, MockLLMProvider(), "2026-W35", rows, "now")
    data.overview = "<script>test</script> 见 [1][2][999]"
    data.overview_points = ["要点 [1]"]
    data.trends_macro = ["市场 [1]"]
    data.trends_projects = ["项目 [1]"]
    data.trends_vendor = ["建议 [1]"]
    data.next_week = ["关注 [1]"]
    html = BeautifulSoup(render_html(data), "lxml")

    links = html.select("a.citation")
    assert len(links) == 6
    assert all(a.get_text() == "[1]" and a["href"] == original for a in links)
    assert all(a["target"] == "_blank" and "noopener" in a["rel"] for a in links)
    assert html.select_one(".overview p").get_text() == "<script>test</script> 见 [1][2][999]"
    assert html.find("script") is None


def test_export_docx(cfg, tmp_path):
    data = generate(cfg, MockLLMProvider(), "2026-W35", ROWS, "now")
    path = tmp_path / "weekly.docx"
    to_docx(data, path)
    assert path.exists() and path.stat().st_size > 5000
    with zipfile.ZipFile(path) as zf:
        assert "word/document.xml" in zf.namelist()


def test_write_report(cfg):
    out = write_report(cfg, MockLLMProvider(), "2026-W35", ROWS)
    for key in ("html_path", "docx_path", "json_path"):
        assert out[key].exists()
    html = out["html_path"].read_text(encoding="utf-8")
    assert "智能交通新闻周报" in html
    assert "类别分布" in html
    assert "存档" in html


def test_report_retries_on_bad_json(cfg):
    """综述输出无法解析时应自动重试一次，而不是直接降级。"""
    import json as _json

    class FlakyProvider:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, **kw):
            self.calls += 1
            if self.calls == 1:
                return "这不是 JSON，模型偶发输出异常"
            return _json.dumps({
                "notes": [{"idx": i, "note": f"第 {i} 条要点内容，描述具体事实与看点。"}
                          for i in (1, 2)],
                "overview": "本周概览正文。", "overview_points": ["要点一"],
                "top5": [1, 2], "trends": {"macro": ["宏观"], "projects": ["项目"],
                                        "vendor_advice": ["建议"]},
                "next_week": ["关注"],
            }, ensure_ascii=False)

    prov = FlakyProvider()
    data = generate(cfg, prov, "2026-W35", ROWS, "now")
    assert prov.calls == 2                 # 第一次失败 → 重试一次
    assert data.fallback is False          # 未降级
    assert data.overview == "本周概览正文。"
    assert data.trends_projects == ["项目"]


def test_report_falls_back_after_retries(cfg):
    """多次都无法解析时才降级，且周报仍可产出。"""
    class BrokenProvider:
        calls = 0

        def chat(self, messages, **kw):
            BrokenProvider.calls += 1
            return "仍然不是 JSON"

    data = generate(cfg, BrokenProvider(), "2026-W35", ROWS, "now")
    assert BrokenProvider.calls == 2       # 1 次 + json_retries(1)
    assert data.fallback is True
    assert data.overview and data.themes    # 降级内容仍然完整


def test_report_marks_salvaged_notes_as_degraded(cfg):
    provider = MockLLMProvider(lambda _: '{"notes":[{"idx":1,"note":"已输出要点"}],"overview":"截断')
    data = generate(cfg, provider, "2026-W35", ROWS, "now")
    assert data.fallback and not data.top5
    assert "已输出要点" in [item["note"] for theme in data.themes for item in theme["items"]]


def test_report_escapes_text_and_rejects_unsafe_links(cfg):
    row = {**ROWS[0], "title": '<script>alert("test")</script>', "url": "javascript:alert(1)"}
    html = render_html(generate(cfg, MockLLMProvider(), "2026-W35", [row], "now"))
    assert "&lt;script&gt;" in html
    assert "<script>" not in html and 'href="javascript:' not in html


def test_report_format_and_input_limit_preserve_appendix(cfg):
    cfg.report.update(formats=["docx"], max_items=1)
    out = write_report(cfg, MockLLMProvider(), "2026-W35", ROWS)
    assert out["docx_path"].is_file() and out["json_path"].is_file()
    assert "html_path" not in out and not list(cfg.data_dir.rglob("*.html"))
    assert len(out["data"].items) == 2 and out["data"].top5 == [1]


def test_same_title_reprint_with_extra_navigation_and_distinct_revision(cfg):
    body = "交管部门在多个路口部署信号控制设备，并公布项目阶段。" * 20 + "投资额为1.2亿元。"
    source = {**ROWS[0], "guid": "orig", "title": "智慧交通项目进展"}
    reprint = {**source, "guid": "reprint", "source_name": "转载站", "published_at": "2026-08-25"}
    revision = {**reprint, "guid": "revision", "source_name": "另一来源"}
    bodies = {"orig": body, "reprint": f"来源：新闻频道。{body}更多资讯",
              "revision": body.replace("1.2亿元", "12亿元")}
    data = generate(cfg, MockLLMProvider(), "2026-W35", [source, reprint, revision], "now",
                    load_text=lambda row: bodies[row["guid"]])
    assert [it["guid"] for it in data.items] == ["orig", "revision"]


def test_report_checks_model_stage_and_original_year(cfg):
    import json

    rows = [
        {**ROWS[0], "title": "上海交通委介绍行动方案", "summary": "上海编制了行动方案。"},
        {**ROWS[1], "title": "监测预警行业研究", "summary": "研究报告显示，2025年投资增长超200%。"},
    ]
    reply = {
        "notes": [{"idx": 1, "note": "上海正式发布行动方案。"},
                  {"idx": 2, "note": "研究报告显示，2025年投资增长超200%。"}],
        "overview": "上海正式发布行动方案 [1]。投资增长超200% [2]。",
        "overview_points": ["上海已发布方案 [1]"],
        "top5": [1, 2],
        "trends": {"macro": ["研究显示2025年投资增长超200% [2]"],
                   "projects": ["投资增长超300% [2]"], "vendor_advice": []},
        "next_week": ["后续关注 [999]"],
    }
    data = generate(cfg, MockLLMProvider(lambda _: json.dumps(reply)), "2026-W35", rows, "now")
    assert data.fallback and data.evidence_adjustments == 6
    assert "6 处表述因证据不足" in render_html(data)
    assert "发布" not in data.overview and "上海编制了行动方案" in data.overview
    assert "2025年投资增长超200%" in data.overview
    assert data.trends_macro == ["研究显示2025年投资增长超200% [2]"]
    assert "300%" not in data.trends_projects[0]
    assert not data.next_week
    notes = {note["idx"]: note["note"] for theme in data.themes for note in theme["items"]}
    assert notes[1] == "上海编制了行动方案。"
