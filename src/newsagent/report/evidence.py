"""标记综述中缺少证据的数量、统计年份和项目阶段。"""

from __future__ import annotations

import re
import unicodedata

_REF = re.compile(r"\[(\d+)\]")
_SENTENCE = re.compile(r"[。！？!?；;]+(?:\s*\[\d+\][。！？!?；;]*)*")
EVIDENCE_MARKER = "⚠"
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*年")
_QUANTITY = re.compile(
    r"(?<![\d.])(?P<bound>超过|超|突破|逾|不足|低于|少于|大约|约|接近|近)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%|亿元?|万元?|公里|千米|个|项|座|套|辆|处|台)"
)
_BOUNDS = {"超过": ">", "超": ">", "突破": ">", "逾": ">",
           "不足": "<", "低于": "<", "少于": "<", "大约": "~", "约": "~", "接近": "~", "近": "~"}
_STAGES = tuple(re.compile(pattern) for pattern in (
    r"(?:发布|印发|出台)(?!会)",
    r"完成(?:施工)?(?:招标|开标)|已开标",
    r"中标|中选",
    r"开工|开建",
    r"投运|投用|投入(?:使用|运营)|建成|已落地|相继落地",
))
_TENTATIVE = re.compile(r"拟|计划|预计|将于|将|有望|何时|待|未|关注")


def _text(value: str) -> str:
    return unicodedata.normalize("NFKC", _REF.sub("", value))


def _quantities(text: str) -> set[str]:
    return {
        _BOUNDS.get(bound, "") + value + {"亿元": "亿", "万元": "万", "千米": "公里"}.get(unit, unit)
        for bound, value, unit in _QUANTITY.findall(text)
    }


def _stages(text: str) -> set[int]:
    return {
        idx for idx, pattern in enumerate(_STAGES)
        for match in pattern.finditer(text)
        if not _TENTATIVE.search(text[max(0, match.start() - 10):match.start()])
    }


def _supported(claim: str, sources: list[list[str]]) -> bool:
    text = _text(claim)
    years = set(_YEAR.findall(text))
    quantities = _quantities(text)
    stages = _stages(text)
    sentences = [sentence for source in sources for sentence in source]
    if not years <= {year for sentence in sentences for year in _YEAR.findall(sentence)}:
        return False
    for quantity in quantities:
        if not any(quantity in _quantities(sentence) and set(_YEAR.findall(sentence)) <= years
                   for sentence in sentences):
            return False
    # 多篇新闻并列时，各篇都应支持所声称的阶段。
    return not stages or bool(sources) and all(
        stages <= {stage for sentence in source for stage in _stages(sentence)}
        for source in sources
    )


def mark_unsupported(claim: str) -> str:
    start = len(claim) - len(claim.lstrip())
    return f"{claim[:start]}{EVIDENCE_MARKER} {claim[start:]}"


def check_composition(composed: dict, items: list[dict], *, require_refs: bool = False) -> int:
    """就地标记未通过校验的表述，保留原文和引用。"""
    if not composed:
        return 0
    evidence = {
        str(it["idx"]): [sentence for value in (it["title"], it["summary"])
                         for sentence in _SENTENCE.split(_text(value))]
        for it in items
    }
    marked = 0

    def checked(claim: str, *, note_idx: str | None = None) -> str:
        nonlocal marked
        refs = {unicodedata.normalize("NFKC", ref).lstrip("0") for ref in _REF.findall(claim)}
        if note_idx is not None and not refs:
            refs = {note_idx}
        valid = refs <= evidence.keys() and (note_idx is None or refs == {note_idx})
        if valid and (refs or not require_refs) and _supported(claim, [evidence[idx] for idx in refs]):
            return claim
        marked += 1
        return mark_unsupported(claim)

    overview = composed.get("overview", "")
    sentences = []
    start = 0
    for match in _SENTENCE.finditer(overview):
        sentences.append(overview[start:match.end()])
        start = match.end()
    sentences.append(overview[start:])
    composed["overview"] = "".join(checked(sentence) if sentence.strip() else sentence
                                   for sentence in sentences)
    for key in ("overview_points", "trends_macro", "trends_projects", "trends_vendor", "next_week", "next_month"):
        composed[key] = [checked(claim) for claim in composed.get(key, [])]
    composed["notes"] = {idx: checked(note, note_idx=str(idx)) for idx, note in composed.get("notes", {}).items()}
    return marked
