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
    assert data.overview
    assert all(1 <= i <= 2 for i in data.top5)


def test_generate_fallback(cfg):
    class Boom:
        def chat(self, *a, **k):
            raise RuntimeError("api down")

    data = generate(cfg, Boom(), "2026-W35", ROWS, "now")
    assert data.fallback is True
    assert data.themes  # 标签兜底分组
    assert "本周共收录" in data.overview


def test_render_html(cfg):
    data = generate(cfg, MockLLMProvider(), "2026-W35", ROWS, "now")
    html = render_html(data)
    assert "智能交通新闻周报" in html
    assert "厂商与集成商动态" in html
    assert "附录" in html
    assert "车路云" in html


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
    assert "智能交通新闻周报" in out["html_path"].read_text(encoding="utf-8")
