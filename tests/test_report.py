"""综述模块测试：生成 / 降级 / HTML 渲染 / Word 导出。"""

import zipfile

from newsagent.classify.llm import MockLLMProvider
from newsagent.report import write_report
from newsagent.report.export import to_docx
from newsagent.report.generator import ReportData, generate
from newsagent.report.render import render_html

ROWS = [
    {"guid": "a1", "title": "某市启动车路云一体化试点", "source_name": "ITS114智慧交通",
     "published_at": "2026-08-24", "tags": ["车路协同/智能网联", "试点城市与示范区"],
     "companies": ["华为"], "importance": 3, "summary": "试点启动。",
     "url": "https://www.sohu.com/a/1", "html_file": "raw/2026-W35/x.html"},
    {"guid": "a2", "title": "中控信息中标信号控制项目", "source_name": "赛文交通网",
     "published_at": "2026-08-25", "tags": ["厂商动态/集成商动态"],
     "companies": ["中控信息"], "importance": 2, "summary": "中标。",
     "url": "https://www.7its.com/a/2", "html_file": None},
]


def test_generate_mock(cfg):
    data = generate(cfg, MockLLMProvider(), "2026-W35", ROWS, "2026-08-26 13:00")
    assert data.themes and not data.fallback
    # 主题 = 确定性复用打标 level-1 标签（数量降序、名称升序）
    titles = [t["title"] for t in data.themes]
    assert titles == ["厂商动态", "车路协同/智能网联"]
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
    """报告层查重：标题相同的跨源转载只保留一条。"""
    rows = [
        {"guid": "d1", "title": "山西主骨架公路5年内实现数字化升级",
         "source_name": "交通运输部", "published_at": "2026-09-10", "tags": ["政策法规"],
         "companies": [], "importance": 2, "summary": "s", "url": "https://a/1",
         "html_file": None, "content_hash": "h1"},
        {"guid": "d2", "title": "山西主骨架公路5年内实现数字化升级",
         "source_name": "中国交通新闻网", "published_at": "2026-09-10", "tags": ["政策法规"],
         "companies": [], "importance": 2, "summary": "s", "url": "https://b/2",
         "html_file": None, "content_hash": "h2"},
        {"guid": "d3", "title": "城市摆渡人变身治理合伙人",
         "source_name": "交通运输部", "published_at": "2026-09-11", "tags": ["智能公交/出租"],
         "companies": [], "importance": 1, "summary": "s", "url": "https://a/3",
         "html_file": None, "content_hash": "h3"},
    ]
    data = generate(cfg, MockLLMProvider(), "2026-W35", rows, "now")
    titles = [it["title"] for it in data.items]
    assert titles.count("山西主骨架公路5年内实现数字化升级") == 1
    assert len(data.items) == 2


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

    data = generate(cfg, LongNoteProvider(), "2026-W35", ROWS, "now")
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
    assert titles == {"厂商动态", "车路协同/智能网联"}
    assert data.fallback is True      # 综述降级（要点用摘要兜底）
    assert prov.calls == 1            # 要点与综述已合并为一次调用；归类零调用

    # 主题与附录标签一致（同一条新闻不会出现"归类冲突"）
    idx2_theme = next(t["title"] for t in data.themes
                      for it in t["items"] if it["idx"] == 2)
    assert idx2_theme == "厂商动态"


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
