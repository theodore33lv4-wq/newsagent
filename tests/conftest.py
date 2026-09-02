"""pytest fixtures：临时配置目录 + 数据目录。"""

from __future__ import annotations

import pytest
import yaml

from newsagent.utils.config import Config

MAIN_CONFIG = {
    "app": {"timezone": "Asia/Shanghai", "data_dir": "data"},
    "llm": {"provider": "mock", "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key_env": "LLM_API_KEY", "temperature": 0.2,
            "timeout_seconds": 60, "max_retries": 0,
            "tagger_model": None, "report_model": None},
    "collect": {"per_source_limit": 20, "fetch_timeout_seconds": 10,
                "retries": 0, "concurrency": 2, "request_interval_seconds": 0,
                "user_agent": "test-agent",
                "search": {"enabled": False, "engine": "bing",
                           "keywords": ["智能交通"], "max_results_per_keyword": 2}},
    "classify": {"summary_max_chars": 100, "json_retries": 1,
                 "max_input_chars": 2000, "concurrency": 2,
                 "vendor_importance_floor": 2},
    "report": {"max_items": 120, "summary_max_chars": 90,
               "formats": ["html", "docx"]},
    "notify": {"enabled": False, "webhook_url_env": "NOTIFY_WEBHOOK_URL",
               "format": "wecom"},
}

SOURCES_CONFIG = {
    "sources": [
        {"id": "fake_sohu", "name": "测试源", "type": "sohu_account",
         "enabled": True, "account": "智慧交通",
         "xpt": "aXRzMTE0QHNvaHUuY29t",
         "profile_url": "https://mp.sohu.com/profile?xpt=aXRzMTE0QHNvaHUuY29t",
         "limit": 20},
    ]
}

TAXONOMY_CONFIG = {
    "taxonomy": [
        {"name": "厂商动态", "children": ["集成商动态", "设备商动态"]},
        {"name": "车路协同/智能网联", "children": ["试点城市与示范区"]},
        {"name": "智慧高速", "children": []},
        {"name": "政策法规", "children": []},
        {"name": "其他", "children": []},
    ]
}


@pytest.fixture
def cfg(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(MAIN_CONFIG, allow_unicode=True), encoding="utf-8")
    (config_dir / "sources.yaml").write_text(
        yaml.safe_dump(SOURCES_CONFIG, allow_unicode=True), encoding="utf-8")
    (config_dir / "taxonomy.yaml").write_text(
        yaml.safe_dump(TAXONOMY_CONFIG, allow_unicode=True), encoding="utf-8")
    return Config.load(root=tmp_path)


@pytest.fixture
def article():
    from newsagent.collect.base import Article
    return Article(url="https://www.sohu.com/a/111_222?spm=abc&utm_source=x",
                   title="智慧高速试点正式启动",
                   source_id="fake_sohu", source_name="测试源")


SAMPLE_HTML = """<html><head><title>某城市智慧高速试点启动</title>
<meta property="og:title" content="某城市智慧高速试点启动"></head>
<body><nav>导航</nav><article>
<p>近日，某省宣布启动智慧高速公路试点项目，将在三个路段部署车路协同感知设备。</p>
<p>项目由当地交管部门与多家集成商联合实施，预计年内完成调试并投入试运行。</p>
</article><footer>版权</footer><script>var x=1;</script></body></html>"""


@pytest.fixture
def sample_html() -> str:
    return SAMPLE_HTML
