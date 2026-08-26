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
    assert "厂商动态/集成商动态" in cls.tags
    assert cls.importance == 2
    assert cls.companies


def test_invalid_tags_filtered(cfg):
    obj = {"relevant": True, "tags": ["不存在的标签", "厂商动态/集成商动态"],
           "summary": "s", "keywords": [], "companies": [], "importance": 2}
    cls = Tagger(Responder(obj), cfg).classify_one(ROW, "t")
    assert cls.tags == ["厂商动态/集成商动态"]


def test_vendor_floor(cfg):
    obj = {"relevant": True, "tags": ["厂商动态/设备商动态"], "summary": "s",
           "keywords": [], "companies": [], "importance": 1}
    cls = Tagger(Responder(obj), cfg).classify_one(ROW, "t")
    assert cls.importance == 2  # 下限提升
    obj["importance"] = 9
    assert Tagger(Responder(obj), cfg).classify_one(ROW, "t").importance == 3
    obj["importance"] = "x"
    assert Tagger(Responder(obj), cfg).classify_one(ROW, "t").importance is None


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
