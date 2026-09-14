"""LLM Provider 测试：工厂、Mock、错误路径。"""

import json

import pytest

from newsagent.classify.llm import (LLMError, MockLLMProvider, OllamaProvider,
                                    OpenAICompatProvider, create_provider)
from newsagent.utils.config import ConfigError


def test_mock_tag_output(cfg):
    prov = create_provider(cfg)
    out = prov.chat([{"role": "system", "content": "你是新闻打标助手。"},
                     {"role": "user", "content": "..."}], json_mode=True)
    data = json.loads(out)
    assert set(data) >= {"relevant", "tags", "summary", "companies", "importance"}


def test_mock_report_output(cfg):
    prov = create_provider(cfg)
    out = prov.chat([{"role": "system", "content": "你是周报综述助手。"},
                     {"role": "user", "content": "- 1. 标题A\n- 2. 标题B"}], json_mode=True)
    data = json.loads(out)
    # 合并调用：要点 + 概览 + TOP5 + 趋势 + 下周关注 一次返回
    assert set(data) >= {"notes", "overview", "overview_points", "top5", "trends", "next_week"}
    assert [n["idx"] for n in data["notes"]] == [1, 2]


def test_mock_batch_tag_output(cfg):
    prov = create_provider(cfg)
    out = prov.chat([
        {"role": "system", "content": "你是智能交通领域的新闻打标助手。请逐条分析并一次性输出结果。"},
        {"role": "user", "content": "共 3 条，请逐条分析：\n\n### 1\n标题：A\n\n### 2\n标题：B\n\n### 3\n标题：C"},
    ], json_mode=True)
    data = json.loads(out)
    assert [r["idx"] for r in data["results"]] == [1, 2, 3]
    assert data["results"][0]["is_article"] is True


def test_create_provider_without_key_raises(cfg, monkeypatch):
    cfg.llm.provider = "openai-compat"
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        create_provider(cfg)


def test_ollama_preset():
    p = OllamaProvider(model="qwen2.5:7b")
    assert p.base_url == "http://localhost:11434/v1"
    assert p.model == "qwen2.5:7b"


def test_custom_responder():
    p = MockLLMProvider(responder=lambda msgs: '{"custom": 1}')
    assert p.chat([{"role": "user", "content": "hi"}]) == '{"custom": 1}'
