"""模型输出的 JSON 容错解析。

大模型偶尔会输出"差一个括号"或"多一个尾随逗号"的 JSON（finish_reason 仍是 stop，
并非截断），严格解析会直接失败，导致整份综述降级。本模块按以下顺序尝试：

1. 严格解析（含去除 ```json 代码围栏）
2. 截取首尾大括号后再解析（容忍前后杂文）
3. 括号补齐修复：按未闭合栈补上 `}`/`]`，并清理尾随逗号
4. 截断修复：从末尾往前找合适的逗号处截断，再补齐括号
5. 兜底抢救：正则抽取 notes 条目（保住最有价值的逐条要点）
"""

from __future__ import annotations

import json
import re
from typing import Optional

_FENCE_RE = re.compile(r"```(?:json)?", re.I)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_NOTE_RE = re.compile(r'"idx"\s*:\s*(\d+)\s*,\s*"note"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _scan(text: str) -> tuple[list[str], list[int]]:
    """字符串感知地扫描括号：返回（未闭合括号栈, 各逗号位置）。"""
    stack: list[str] = []
    commas: list[int] = []
    in_str = esc = False
    for i, c in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            stack.append(c)
        elif c in "}]":
            if stack:
                stack.pop()
        elif c == ",":
            commas.append(i)
    return stack, commas


def _close(stack: list[str]) -> str:
    return "".join("}" if c == "{" else "]" for c in reversed(stack))


def _try(text: str) -> Optional[dict]:
    try:
        data = json.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def parse_json_lenient(raw: str) -> Optional[dict]:
    """尽力解析出 JSON 对象；彻底失败返回 None。"""
    if not raw or not raw.strip():
        return None
    text = _FENCE_RE.sub("", raw).strip()

    data = _try(text)
    if data is not None:
        return data

    start = text.find("{")
    if start == -1:
        return _salvage_notes(text)
    text = text[start:]

    # 尾部有杂文时：截到最后一个 } 再试（例如"以上。"之类的收尾说明）
    end = text.rfind("}")
    if end != -1 and end < len(text) - 1:
        data = _try(text[:end + 1])
        if data is not None:
            return data

    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    data = _try(text)                      # 仅尾随逗号问题：清理后即可解析
    if data is not None:
        return data

    # 括号补齐（模型漏写 } 或 ] 的常见情形）
    stack, _ = _scan(text)
    if stack:
        data = _try(text + _close(stack))
        if data is not None:
            return data

    # 截断修复：从末尾逗号逐个回退，补上括号后解析
    _, commas = _scan(text)
    for pos in reversed(commas[-30:]):
        head = text[:pos]
        head_stack, _ = _scan(head)
        data = _try(head + _close(head_stack))
        if data is not None:
            return data

    return _salvage_notes(text)


def _salvage_notes(text: str) -> Optional[dict]:
    """兜底：正则抽取 notes（逐条要点是周报最有价值的部分）。"""
    notes = [{"idx": int(i), "note": n.replace('\\"', '"')}
             for i, n in _NOTE_RE.findall(text)]
    return {"notes": notes} if notes else None
