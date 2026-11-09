from __future__ import annotations

import json

from bs4 import BeautifulSoup
from docx import Document

from newsagent.archive.downloader import FetchedContent
from newsagent.archive.store import Store
from newsagent.classify.llm import MockLLMProvider, OpenAICompatProvider
from newsagent.cli import main
from newsagent.collect.base import Article
from newsagent.report import write_monthly_report


def _archive(store, slug, title, day, *, week="2026-W44", relevance=1,
             importance=2, tag="市场与项目", body=None):
    row = store.save_article(
        week, Article(f"https://example.com/{slug}", title, slug, "测试源"),
        FetchedContent("<p>正文</p>", body or title, "test", None, None), published_iso=day,
    )
    if relevance is not None:
        store.update_classification(row["guid"], relevance=relevance, tags=[tag],
                                    summary=title, keywords=[], companies=[], importance=importance)


def test_monthly_cli_offline_uses_local_publish_date_and_preserves_archive(cfg, monkeypatch):
    store = Store(cfg.data_dir)
    _archive(store, "last", "月末项目招标", "2026-10-31T15:59:59+00:00")
    _archive(store, "next", "下月项目招标", "2026-10-31T16:00:00+00:00")
    _archive(store, "first", "月初项目招标", "2026-09-30T16:00:00+00:00", week="2026-W40")
    _archive(store, "pending", "待处理项目", "2026-10-31", relevance=None)
    _archive(store, "undated", "未注明日期", None)
    _archive(store, "irrelevant", "无关新闻", "2026-10-20", relevance=0)
    before = store.db_path.read_bytes()
    cfg.llm["provider"] = "openai-compat"
    monkeypatch.setattr("newsagent.cli.Config.load", lambda: cfg)

    assert main(["monthly", "--month", "2026-10", "--provider", "mock"]) == 0
    assert store.db_path.read_bytes() == before
    base = cfg.data_dir / "reports" / "2026-10" / "monthly-2026-10-preview"
    report = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["preview"] and report["relevant_count"] == 2
    assert {item["published_at"] for item in report["items"]} == {"2026-10-01", "2026-10-31"}
    assert "1 条归档记录尚待处理" in report["coverage_note"]
    assert "1 条日期未确认" in report["coverage_note"]
    assert "模拟预览" in Document(base.with_suffix(".docx")).paragraphs[0].text


def test_monthly_cli_defaults_to_configured_report_model(cfg, monkeypatch):
    _archive(Store(cfg.data_dir), "project", "月末项目招标", "2026-10-31")
    cfg.llm.update(provider="openai-compat", report_model="configured-report-model")
    monkeypatch.setenv(cfg.llm["api_key_env"], "test-key")
    monkeypatch.setattr("newsagent.cli.Config.load", lambda: cfg)
    models = []

    def chat(self, messages, *, json_mode, model):
        models.append(model)
        return MockLLMProvider().chat(messages, json_mode=json_mode, model=model)

    monkeypatch.setattr(OpenAICompatProvider, "chat", chat)
    assert main(["monthly", "--month", "2026-10"]) == 0
    assert models == ["configured-report-model"]
    path = cfg.data_dir / "reports" / "2026-10" / "monthly-2026-10.json"
    assert json.loads(path.read_text(encoding="utf-8"))["preview"] is False


def test_monthly_projects_and_citations_use_selected_evidence_but_appendix_is_complete(cfg):
    store = Store(cfg.data_dir)
    _archive(store, "tender", "新城智慧高速项目启动招标", "2026-10-05", importance=3)
    _archive(store, "award", "新城智慧高速项目公布中标结果", "2026-10-25", importance=3)
    _archive(store, "reprint", "新城智慧高速项目公布中标结果", "2026-10-26")
    _archive(store, "policy", "东城发布交通政策", "2026-10-15", tag="政策标准与行业")
    _archive(store, "other", "其他地区项目动态", "2026-10-20", importance=1)
    cfg.report["monthly_max_items"] = 3

    def respond(messages):
        payload = json.loads(messages[1]["content"])
        assert payload["month"] == "2026-10"
        items = {it["title"]: it["idx"] for g in payload["themes"] for it in g["items"]}
        tender = items["新城智慧高速项目启动招标"]
        award = items["新城智慧高速项目公布中标结果"]
        policy = items["东城发布交通政策"]
        assert len(items) == 3
        return json.dumps({
            "overview": f"新城智慧高速项目已开工 [{award}]。",
            "overview_points": [f"新城智慧高速项目已开工 [{award}]", "没有出处的观察"],
            "trends_macro": [f"东城发布交通政策 [{policy}]"],
            "trends_vendor": [], "top5": [award, policy],
            "projects": [{"name": "新城智慧高速项目", "indices": [award, tender]},
                         {"name": "其他地区项目", "indices": [award, policy]},
                         {"name": "没有对应新闻的项目", "indices": [999]}],
            "next_month": [f"关注新城智慧高速项目后续进展 [{award}]", "缺少引用的建议"],
        }, ensure_ascii=False)

    out = write_monthly_report(cfg, MockLLMProvider(respond), "2026-10")
    data = out["data"]
    assert data.evidence_adjustments == 6 and data.fallback
    assert len(data.items) == sum(d["count"] for d in data.distribution) == 4
    assert data.overview.startswith("⚠ 新城智慧高速项目已开工 ")
    assert data.overview_points == [data.overview.removesuffix("。"), "⚠ 没有出处的观察"]
    assert data.next_month[1] == "⚠ 缺少引用的建议"
    assert [project["name"] for project in data.projects] == [
        "新城智慧高速项目", "⚠ 其他地区项目", "⚠ 没有对应新闻的项目",
    ]
    assert [data.find_item(idx)["published_at"] for idx in data.projects[0]["indices"]] == [
        "2026-10-05", "2026-10-25",
    ]
    html = BeautifulSoup(out["html_path"].read_text(encoding="utf-8"), "html.parser")
    assert len(html.select("table.appendix tbody tr")) == 4
    assert [a["href"] for a in html.select(".projects ul")[0].select(".citation")] == [
        "https://example.com/tender", "https://example.com/award",
    ]
    missing = html.select(".projects ul")[-1]
    assert "[999] 未找到对应新闻" in missing.get_text() and not missing.select("a")
    assert html.select_one(".next .citation")["href"] == "https://example.com/award"
    assert "⚠ 没有对应新闻的项目" in [p.text for p in Document(out["docx_path"]).paragraphs]
    assert json.loads(out["json_path"].read_text(encoding="utf-8"))["projects"] == data.projects


def test_monthly_missing_archive_is_not_created(cfg, monkeypatch):
    monkeypatch.setattr("newsagent.cli.Config.load", lambda: cfg)
    assert main(["monthly", "--month", "2026-13"]) == 2
    assert main(["monthly", "--month", "2026-10"]) == 1
    assert not cfg.data_dir.exists()
