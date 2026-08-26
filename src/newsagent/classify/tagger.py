"""新闻分类：LLM 打标 + 摘要 + 厂商/集成商抽取 + 相关性过滤。

- 单条 prompt + 结构化 JSON 输出（标签必须来自 taxonomy.yaml 合法集合）
- JSON 解析失败自动重试（补一句纠正提示），仍失败则降级为"分类失败待人工"
- 厂商动态类新闻重要度下限保障（config: classify.vendor_importance_floor）
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from ..utils.config import Config
from .llm import LLMProvider

_SYSTEM_TEMPLATE = """你是智能交通领域的新闻打标助手。对给定的中文新闻内容做结构化分析，只输出一个 JSON 对象，不要输出任何其他文字或代码块标记。

合法标签（必须从下面选择，"一级/二级" 或 "一级"）：
{taxonomy}

输出 JSON 字段（严格遵循）：
{{
  "relevant": true 或 false,
  "tags": ["标签1", "标签2"],
  "summary": "不超过 {summary_max} 字的客观摘要",
  "keywords": ["关键词1", "关键词2"],
  "companies": ["厂商或集成商名称", ...],
  "importance": 1 或 2 或 3
}}

规则：
- relevant=false 表示与智能交通无关（如纯娱乐、无关行业新闻），此时 tags 为空数组；
- tags 只能从上述合法标签中选择，通常 1-3 个；
- companies 只列正文明确提到的厂商/系统集成商（如中控信息、银江技术、海信网络科技、易华录、千方科技、佳都科技、华为、百度、腾讯等），没有则为空数组；
- importance：1 一般、2 重要、3 重大。"""


@dataclass
class Classification:
    """单条新闻的打标结果。ok=False 表示 LLM 输出无法解析（待人工）。"""

    guid: str
    relevant: Optional[bool] = None
    tags: list[str] = field(default_factory=list)
    summary: Optional[str] = None
    keywords: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    importance: Optional[int] = None
    ok: bool = True
    error: Optional[str] = None
    raw: Optional[str] = None


def extract_json(text: str) -> Optional[dict]:
    """从模型输出中提取 JSON 对象（容忍代码块围栏/前后杂文）。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except Exception:
            return None
    return None


class Tagger:
    def __init__(self, provider: LLMProvider, cfg: Config):
        self.provider = provider
        self.cfg = cfg
        self._taxonomy_paths = cfg.taxonomy_paths()
        self._json_retries = int(cfg.classify.get("json_retries", 1))
        self._summary_max = int(cfg.classify.get("summary_max_chars", 100))
        self._input_max = int(cfg.classify.get("max_input_chars", 2000))
        self._vendor_floor = int(cfg.classify.get("vendor_importance_floor", 2))

    # ---------- 对外 ----------
    def classify_one(self, row: dict, text: Optional[str]) -> Classification:
        guid = row["guid"]
        messages = self._build_messages(row, text)
        raw = None
        for attempt in range(self._json_retries + 1):
            try:
                raw = self.provider.chat(messages, json_mode=True)
            except Exception as exc:
                return Classification(guid=guid, ok=False,
                                      error=f"LLM 调用失败: {exc}", raw=str(exc))
            data = extract_json(raw)
            if data is not None:
                return self._parse(guid, data, raw)
            # 解析失败 → 追加纠正提示重试
            logger.warning("[{}] 第 {} 次输出无法解析为 JSON，重试", guid, attempt + 1)
            messages = messages + [{
                "role": "user",
                "content": "注意：你上一次的输出无法被解析为 JSON。请重新输出，且只输出一个合法的 JSON 对象。",
            }]
        return Classification(guid=guid, ok=False,
                              error="多次输出均无法解析为 JSON（待人工）", raw=raw)

    def classify_many(self, rows: list[dict], texts: dict[str, Optional[str]] | None = None,
                      concurrency: int = 4) -> list[Classification]:
        """并行打标；按输入顺序返回结果。texts: guid → 正文（缺省则无正文）。"""
        texts = texts or {}
        results: list[Optional[Classification]] = [None] * len(rows)

        def work(i: int) -> tuple[int, Classification]:
            row = rows[i]
            return i, self.classify_one(row, texts.get(row["guid"]))

        if concurrency <= 1 or len(rows) <= 1:
            for i in range(len(rows)):
                results[i] = work(i)[1]
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                for i, cls in pool.map(work, range(len(rows))):
                    results[i] = cls

        out = [r for r in results if r is not None]
        ok = sum(1 for r in out if r.ok)
        rel = sum(1 for r in out if r.ok and r.relevant)
        vendor = sum(1 for r in out if r.ok and r.relevant
                     and any(t.startswith("厂商动态") for t in r.tags))
        logger.info("打标完成：{} 条（成功 {} / 判定相关 {} / 厂商动态 {}）",
                    len(out), ok, rel, vendor)
        return out

    # ---------- 内部 ----------
    def _build_messages(self, row: dict, text: Optional[str]) -> list[dict]:
        system = _SYSTEM_TEMPLATE.format(
            taxonomy="\n".join(f"- {p}" for p in self._taxonomy_paths),
            summary_max=self._summary_max,
        )
        body = (text or "").strip()
        if len(body) > self._input_max:
            body = body[: self._input_max] + "……（正文已截断）"
        user = (f"【新闻标题】{row.get('title', '')}\n"
                f"【来源】{row.get('source_name', '')}\n"
                f"【发布时间】{row.get('published_at') or '未知'}\n"
                f"【正文】\n{body or '（正文提取失败，请仅依据标题判断）'}")
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _parse(self, guid: str, data: dict, raw: str) -> Classification:
        relevant = data.get("relevant")
        if not isinstance(relevant, bool):
            relevant = True  # 缺省视为相关，宁收勿漏（下游人工可查）

        tags = self._filter_tags(self._as_str_list(data.get("tags")))
        summary = self._as_str(data.get("summary")) or None
        keywords = self._as_str_list(data.get("keywords"))
        companies = self._as_str_list(data.get("companies"))
        importance = self._as_int(data.get("importance"))

        if tags and any(t.startswith("厂商动态") for t in tags) and importance is not None:
            if importance < self._vendor_floor:
                importance = self._vendor_floor
                logger.debug("[{}] 厂商动态重要度提升至 {}", guid, importance)

        return Classification(
            guid=guid, relevant=relevant, tags=tags, summary=summary,
            keywords=keywords, companies=companies, importance=importance,
            ok=True, raw=raw,
        )

    def _filter_tags(self, tags: list[str]) -> list[str]:
        valid = set(self._taxonomy_paths)
        out: list[str] = []
        for t in tags:
            t = t.strip()
            if t in valid and t not in out:
                out.append(t)
            else:
                logger.debug("标签不在合法集合，已丢弃: {!r}", t)
        return out

    @staticmethod
    def _as_str(v) -> Optional[str]:
        return str(v).strip() if v is not None else None

    @classmethod
    def _as_str_list(cls, v) -> list[str]:
        if not isinstance(v, list):
            return []
        out = []
        for item in v:
            s = cls._as_str(item)
            if s:
                out.append(s)
        return out

    @staticmethod
    def _as_int(v) -> Optional[int]:
        try:
            i = int(v)
        except (TypeError, ValueError):
            return None
        return max(1, min(3, i))
