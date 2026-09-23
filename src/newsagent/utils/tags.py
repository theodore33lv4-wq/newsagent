"""标签工具：厂商相关标签的判定。

"厂商/集成商"内容散落在标签树的多个位置（如"市场与项目/厂商中标与订单"），
因此不再按固定标签名硬编码，而是按关键词判定，标签树调整后无需改代码。
"""

from __future__ import annotations

# 命中任一关键词即视为"厂商/集成商相关"
VENDOR_KEYWORDS = ("厂商", "中标", "集成商", "供应商", "订单")


def is_vendor_tag(tag: str | None) -> bool:
    """判断标签是否与厂商/集成商相关（用于重要度下限与周报专节）。"""
    t = (tag or "").strip()
    return any(k in t for k in VENDOR_KEYWORDS)


def has_vendor_tag(tags: list[str] | None) -> bool:
    return any(is_vendor_tag(t) for t in (tags or []))
