"""容错 JSON 解析测试（模型偶发漏括号 / 尾随逗号 / 截断）。"""

from newsagent.utils.jsonrepair import parse_json_lenient

VALID = '{"notes": [{"idx": 1, "note": "要点一"}], "overview": "概览"}'


def test_valid_json_passthrough():
    assert parse_json_lenient(VALID) == {"notes": [{"idx": 1, "note": "要点一"}], "overview": "概览"}


def test_code_fence_and_prose():
    assert parse_json_lenient(f"好的，如下：\n```json\n{VALID}\n```\n以上。")


def test_missing_closing_brace():
    """模型漏掉一个 } —— 本次真实故障的形态。"""
    broken = '{"notes": [{"idx": 1, "note": "要点一"}], "trends": {"macro": ["宏观"'
    data = parse_json_lenient(broken)
    assert data and data["notes"][0]["note"] == "要点一"
    assert data["trends"]["macro"] == ["宏观"]


def test_missing_outer_brace():
    broken = '{"notes": [{"idx": 1, "note": "要点一"}], "overview": "概览"'
    data = parse_json_lenient(broken)
    assert data and data["overview"] == "概览"


def test_trailing_comma():
    broken = '{"notes": [{"idx": 1, "note": "a"},], "overview": "b",}'
    data = parse_json_lenient(broken)
    assert data and data["overview"] == "b" and len(data["notes"]) == 1


def test_truncated_mid_array_salvages_notes():
    truncated = ('{"notes": [{"idx": 1, "note": "第一条要点"}, '
                 '{"idx": 2, "note": "第二条要点"}, {"idx": 3, "note": "第三条要')
    data = parse_json_lenient(truncated)
    idxs = [n["idx"] for n in data["notes"]]
    assert idxs[:2] == [1, 2]              # 至少保住已完整输出的要点
    assert data["notes"][0]["note"] == "第一条要点"


def test_garbage_returns_none():
    assert parse_json_lenient("这不是 JSON") is None
    assert parse_json_lenient("") is None
