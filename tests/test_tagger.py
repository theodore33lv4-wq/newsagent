"""Tagger 测试：打标解析、标签过滤、重要度下限、JSON 降级、并行。"""

import json

from newsagent.classify.llm import MockLLMProvider
from newsagent.classify.tagger import Tagger, extract_json

ROW = {"guid": "g1", "title": "某集成商中标智慧高速项目",
       "source_name": "测试源", "published_at": None}


class Responder:
    def __init__(self, obj):
        self.obj = obj

    def chat(self, messages, **kw):
        return json.dumps(self.obj, ensure_ascii=False)


def test_classify_basic(cfg):
    tag = Tagger(MockLLMProvider(), cfg)
    cls = tag.classify_one(ROW, "正文……车路协同……")
    assert cls.ok and cls.relevant is True
    assert "市场与项目/厂商中标与订单" in cls.tags
    assert cls.importance == 2
    assert cls.companies


def test_invalid_tags_filtered(cfg):
    obj = {"is_article": True, "relevant": True, "tags": ["不存在的标签", "市场与项目/厂商中标与订单"],
           "summary": "s", "keywords": [], "companies": [], "importance": 2}
    cls = Tagger(Responder(obj), cfg).classify_one(ROW, "t")
    assert cls.tags == ["市场与项目/厂商中标与订单"]


def test_vendor_floor(cfg):
    obj = {"is_article": True, "relevant": True, "tags": ["市场与项目/厂商中标与订单"], "summary": "s",
           "keywords": [], "companies": [], "importance": 1}
    cls = Tagger(Responder(obj), cfg).classify_one(ROW, "t")
    assert cls.importance == 2  # 下限提升
    obj["importance"] = 9
    assert not Tagger(Responder(obj), cfg).classify_one(ROW, "t").ok
    obj["importance"] = "x"
    assert not Tagger(Responder(obj), cfg).classify_one(ROW, "t").ok


def test_bad_json_retry_then_fallback(cfg):
    class BadJson:
        calls = 0

        def chat(self, messages, **kw):
            BadJson.calls += 1
            return "这不是JSON，请重试"

    cls = Tagger(BadJson(), cfg).classify_one(ROW, "t")
    assert cls.ok is False
    assert BadJson.calls == 2  # 1 + json_retries(1)


def test_extract_json_edge(cfg):
    assert extract_json("```json\n{\"a\":1}\n```") == {"a": 1}
    assert extract_json("开头 {\"relevant\": true} 结尾") == {"relevant": True}
    assert extract_json("垃圾") is None


def test_classify_many_order(cfg):
    rows = [{"guid": f"g{i}", "title": f"标题{i}", "source_name": "测试源",
             "published_at": None} for i in range(5)]
    tag = Tagger(MockLLMProvider(), cfg)
    res = tag.classify_many(rows, concurrency=3)
    assert [r.guid for r in res] == [f"g{i}" for i in range(5)]
    assert all(r.ok for r in res)


def test_invalid_batch_items_retry_as_single_with_configured_model(cfg):
    class RepairingProvider:
        def __init__(self):
            self.models = []

        def chat(self, messages, **kw):
            self.models.append(kw["model"])
            if '"results"' in messages[0]["content"]:
                return '{"results":[{"idx":1},{"idx":1}]}'
            return '{"is_article":true,"relevant":true,"tags":["市场与项目"],' \
                   '"summary":"摘要","keywords":[],"companies":[],"importance":2}'

    cfg.llm.tagger_model = "tagger-test"
    provider = RepairingProvider()
    [result] = Tagger(provider, cfg).classify_many([ROW])
    assert result.ok and result.tags == ["市场与项目"]
    assert provider.models == ["tagger-test", "tagger-test"]
    assert not Tagger(Responder([1]), cfg).classify_one(ROW, "正文").ok
    assert not Tagger(Responder({}), cfg).classify_one(ROW, "正文").ok
