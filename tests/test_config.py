"""配置加载与工具函数测试。"""

import pytest

from newsagent.utils.config import Config, ConfigError


def test_load_basic(cfg):
    assert cfg.llm.provider == "mock"
    assert cfg.data_dir.name == "data"
    assert cfg.sources_enabled()[0]["id"] == "fake_sohu"


def test_taxonomy_paths(cfg):
    paths = cfg.taxonomy_paths()
    assert "厂商动态/集成商动态" in paths
    assert "厂商动态/设备商动态" in paths
    assert "智慧高速" in paths           # 无子级的叶子节点
    assert "厂商动态" not in paths       # 有子级的一级不算合法标签


def test_sources_enabled_filter(cfg):
    assert len(cfg.sources_enabled()) == 1


def test_invalid_provider_raises(tmp_path):
    from tests.conftest import MAIN_CONFIG, SOURCES_CONFIG, TAXONOMY_CONFIG
    import yaml
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    main = dict(MAIN_CONFIG)
    main["llm"] = dict(MAIN_CONFIG["llm"], provider="bad-provider")
    (cfg_dir / "config.yaml").write_text(
        yaml.safe_dump(main, allow_unicode=True), encoding="utf-8")
    (cfg_dir / "sources.yaml").write_text(
        yaml.safe_dump(SOURCES_CONFIG, allow_unicode=True), encoding="utf-8")
    (cfg_dir / "taxonomy.yaml").write_text(
        yaml.safe_dump(TAXONOMY_CONFIG, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(root=tmp_path)


def test_load_env(tmp_path, monkeypatch):
    from newsagent.utils.config import load_env
    (tmp_path / ".env").write_text(
        "LLM_API_KEY=sk-test\n# 注释\nEMPTY=\"\"\n", encoding="utf-8")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    loaded = load_env(root=tmp_path)
    assert loaded.get("LLM_API_KEY")
    assert "EMPTY" not in loaded or loaded.get("EMPTY") == ""
