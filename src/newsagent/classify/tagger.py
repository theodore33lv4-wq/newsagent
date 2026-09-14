"""新闻分类：LLM 打标 + 摘要 + 厂商抽取 + 相关性过滤（批次调用为主）。

设计要点：
- **批次调用**：默认一次请求处理 10 条新闻（`classify.batch_size`），
  指令段（标签表与规则）固定不变，便于命中服务端前缀缓存；正文放在 user 段。
- **单条补偿**：批次里缺失或解析失败的条目，自动退回单条调用重试。
- **多任务合一**：同一次调用里完成 是否新闻(is_article) / 发布日期(published_date) /
  相关性 / 标签 / 摘要 / 关键词 / 厂商 / 重要度。
- 标签必须来自 taxonomy.yaml；非法标签丢弃；厂商动态类重要度有下限保障。
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from ..utils.config import Config
from ..utils.dateparse import normalize_date
from .llm import LLMProvider

_SINGLE_SYSTEM_TMPL = """你是智能交通领域的新闻打标助手。对给定的一条中文新闻做结构化分析，只输出一个 JSON 对象，不要输出任何其他文字或代码块标记。

合法标签（必须从下面选择，"一级/二级" 或 "一级"）：
{taxonomy}

输出 JSON 字段（严格遵循）：
{{
  "is_article": true 或 false,
  "published_date": "YYYY-MM-DD" 或 null,
  "relevant": true 或 false,
  "tags": ["标签1", "标签2"],
  "summary": "不超过 {summary_max} 字的客观摘要",
  "keywords": ["关键词1", "关键词2"],
  "companies": ["厂商或集成商名称", ...],
  "importance": 1 或 2 或 3
}}

规则：
- is_article=false 表示这不是一篇具体新闻（栏目页、专题页、导航页、聚合汇总贴等）；
- published_date 依据正文或页面给出的发布时间填写，无法判断填 null；
- relevant=false 表示与智能交通无关，此时 tags 为空数组；
- tags 只能从上述合法标签中选择，通常 1-3 个；
- companies 只列正文明确提到的厂商/系统集成商（如中控信息、银江技术、海信网络科技、易华录、千方科技、佳都科技、华为、百度、腾讯等），没有则为空数组；
- importance：1 一般、2 重要、3 重大；厂商动态类不低于 {vendor_floor}。"""

_BATCH_SYSTEM_TMPL = """你是智能交通领域的新闻打标助手。下面会给出同一批次的多条新闻，请逐条分析并一次性输出结果。

合法标签（必须从下面选择，"一级/二级" 或 "一级"）：
{taxonomy}

只输出一个 JSON 对象，格式：
{{"results": [ {{"idx": 1, "is_article": true, "published_date": "YYYY-MM-DD", "relevant": true,
   "tags": ["标签1"], "summary": "…", "keywords": ["…"], "companies": ["…"], "importance": 2}} ]}}

每条字段与规则：
- idx：新闻编号，必须与输入一致并原样回显；
- is_article：是否为一篇具体新闻（false = 栏目页、专题页、导航页、聚合汇总贴等）；
- published_date：该新闻发布日期（YYYY-MM-DD），依据给出日期或正文判断，无法判断填 null；
- relevant：是否与智能交通相关（is_article=false 时填 false），false 时 tags 为空数组；
- tags：只能从上述合法标签中选择，通常 1-3 个；
- summary：不超过 {summary_max} 字的客观摘要；
- keywords：2-5 个关键词；companies：正文明确提到的厂商/系统集成商，没有则为空数组；
- importance：1 一般、2 重要、3 重大；厂商动态类不低于 {vendor_floor}；
- 必须覆盖输入中的全部 idx，且顺序与输入一致；除 JSON 外不要输出任何文字。"""


@dataclass
class Classification:
    """单条新闻的打标结果。ok=False 表示输出无法解析（待人工）。"""

    guid: str
    is_article: bool = True
    published_date: Optional[str] = None
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
        self._batch_item_max = int(cfg.classify.get("batch_max_chars_per_item", 1200))
        self._batch_size = int(cfg.classify.get("batch_size", 10))
        self._batch_concurrency = int(cfg.classify.get("batch_concurrency", 2))
        self._vendor_floor = int(cfg.classify.get("vendor_importance_floor", 2))

    # ---------- 对外 ----------
    def classify_many(self, rows: list[dict],
                      texts: dict[str, Optional[str]] | None = None,
                      concurrency: int | None = None,
                      batch_size: int | None = None) -> list[Classification]:
        """批次打标；按输入顺序返回结果。缺失条目自动单条补偿。"""
        texts = texts or {}
        if not rows:
            return []
        size = max(1, int(batch_size or self._batch_size))
        workers = max(1, int(concurrency or self._batch_concurrency))
        batches = [rows[i:i + size] for i in range(0, len(rows), size)]

        collected: dict[str, Classification] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for part in pool.map(lambda b: self._classify_batch_once(b, texts), batches):
                collected.update(part)

        out: list[Classification] = []
        retried = 0
        for row in rows:
            cls = collected.get(row["guid"])
            if cls is None:                      # 批次缺失 → 单条补偿
                retried += 1
                cls = self.classify_one(row, texts.get(row["guid"]))
            out.append(cls)

        ok = sum(1 for c in out if c.ok)
        rel = sum(1 for c in out if c.ok and c.relevant)
        vendor = sum(1 for c in out if c.ok and c.relevant
                     and any(t.startswith("厂商动态") for t in c.tags))
        logger.info("打标完成：{} 条（成功 {} / 相关 {} / 厂商动态 {} / 批次 {} 个 / 单条补偿 {}）",
                    len(out), ok, rel, vendor, len(batches), retried)
        return out

    def classify_one(self, row: dict, text: Optional[str]) -> Classification:
        guid = row["guid"]
        messages = self._build_single_messages(row, text)
        raw = None
        for attempt in range(self._json_retries + 1):
            try:
                raw = self.provider.chat(messages, json_mode=True)
            except Exception as exc:
                return Classification(guid=guid, ok=False,
                                      error=f"LLM 调用失败: {exc}", raw=str(exc))
            data = extract_json(raw)
            if data is not None:
                return self._parse_payload(guid, data, raw)
            logger.warning("[{}] 第 {} 次输出无法解析为 JSON，重试", guid, attempt + 1)
            messages = messages + [{
                "role": "user",
                "content": "注意：你上一次的输出无法被解析为 JSON。请重新输出，且只输出一个合法的 JSON 对象。",
            }]
        return Classification(guid=guid, ok=False,
                              error="多次输出均无法解析为 JSON（待人工）", raw=raw)

    # ---------- 批次内部 ----------
    def _classify_batch_once(self, batch: list[dict],
                             texts: dict[str, Optional[str]]) -> dict[str, Classification]:
        system = _BATCH_SYSTEM_TMPL.format(
            taxonomy="\n".join(f"- {p}" for p in self._taxonomy_paths),
            summary_max=self._summary_max, vendor_floor=self._vendor_floor)
        sections = []
        for i, row in enumerate(batch, start=1):
            body = (texts.get(row["guid"]) or "").strip()
            if len(body) > self._batch_item_max:
                body = body[: self._batch_item_max] + "……（正文已截断）"
            sections.append(
                f"### {i}\n"
                f"标题：{row.get('title', '')}\n"
                f"来源：{row.get('source_name', '')}\n"
                f"列表页日期：{row.get('published_at') or '未知'}\n"
                f"正文：\n{body or '（正文提取失败，请仅依据标题判断）'}")
        messages = [
            {"role": "system", "content": system},
            {"role": "user",
             "content": f"共 {len(batch)} 条，请逐条分析：\n\n" + "\n\n".join(sections)},
        ]
        try:
            raw = self.provider.chat(messages, json_mode=True)
            data = extract_json(raw)
            if not data:
                logger.warning("批次输出无法解析为 JSON（{} 条），将单条重试", len(batch))
                return {}
            out: dict[str, Classification] = {}
            for item in data.get("results") or []:
                try:
                    idx = int(item.get("idx"))
                except (TypeError, ValueError):
                    continue
                if not (1 <= idx <= len(batch)):
                    continue
                row = batch[idx - 1]
                out[row["guid"]] = self._parse_payload(row["guid"], item, raw)
            missing = len(batch) - len(out)
            if missing:
                logger.warning("批次返回缺项 {} / {} 条，缺项将单条重试", missing, len(batch))
            return out
        except Exception as exc:
            logger.warning("批次调用失败（{} 条），将单条重试: {}", len(batch), exc)
            return {}

    # ---------- 提示词与解析 ----------
    def _build_single_messages(self, row: dict, text: Optional[str]) -> list[dict]:
        system = _SINGLE_SYSTEM_TMPL.format(
            taxonomy="\n".join(f"- {p}" for p in self._taxonomy_paths),
            summary_max=self._summary_max, vendor_floor=self._vendor_floor)
        body = (text or "").strip()
        if len(body) > self._input_max:
            body = body[: self._input_max] + "……（正文已截断）"
        user = (f"【新闻标题】{row.get('title', '')}\n"
                f"【来源】{row.get('source_name', '')}\n"
                f"【列表页日期】{row.get('published_at') or '未知'}\n"
                f"【正文】\n{body or '（正文提取失败，请仅依据标题判断）'}")
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _parse_payload(self, guid: str, data: dict, raw: str) -> Classification:
        is_article = self._as_bool(data.get("is_article"), default=True)
        relevant = self._as_bool(data.get("relevant"), default=True)
        if not is_article:
            relevant = False

        tags = self._filter_tags(self._as_str_list(data.get("tags"))) if relevant else []
        summary = self._as_str(data.get("summary")) or None
        keywords = self._as_str_list(data.get("keywords")) if relevant else []
        companies = self._as_str_list(data.get("companies")) if relevant else []
        importance = self._as_int(data.get("importance")) if relevant else None
        pub = normalize_date(data.get("published_date"))

        if tags and any(t.startswith("厂商动态") for t in tags) and importance is not None:
            if importance < self._vendor_floor:
                importance = self._vendor_floor
                logger.debug("[{}] 厂商动态重要度提升至 {}", guid, importance)

        return Classification(
            guid=guid, is_article=is_article,
            published_date=pub.isoformat() if pub else None,
            relevant=relevant, tags=tags, summary=summary,
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
    def _as_bool(v, *, default: bool = True) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("true", "yes", "1", "是"):
                return True
            if s in ("false", "no", "0", "否"):
                return False
        if isinstance(v, (int, float)):
            return bool(v)
        return default

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
