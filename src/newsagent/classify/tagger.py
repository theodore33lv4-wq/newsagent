"""新闻分类：LLM 打标 + 摘要 + 厂商抽取 + 相关性过滤（批次调用为主）。

设计要点：
- **批次调用**：默认一次请求处理 10 条新闻（`classify.batch_size`），
  指令段（标签表与规则）固定不变，便于命中服务端前缀缓存；正文放在 user 段。
- **单条补偿**：批次里缺失或解析失败的条目，自动退回单条调用重试。
- **多任务合一**：同一次调用里完成 是否新闻(is_article) / 发布日期(published_date) /
  相关性 / 标签 / 摘要 / 关键词 / 厂商 / 重要度。
- 标签必须来自 taxonomy.yaml；非法标签丢弃；厂商/集成商类重要度有下限保障。
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from time import monotonic
from typing import Optional

from loguru import logger

from ..utils.config import Config
from ..utils.dateparse import normalize_date
from ..utils.jsonrepair import parse_json_lenient
from ..utils.tags import is_vendor_tag
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
- importance：1 一般、2 重要、3 重大；厂商/集成商类（含中标、订单）不低于 {vendor_floor}。"""

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
- importance：1 一般、2 重要、3 重大；厂商/集成商类（含中标、订单）不低于 {vendor_floor}；
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
    """从模型输出中提取 JSON 对象。"""
    return parse_json_lenient(text)


class Tagger:
    def __init__(self, provider: LLMProvider, cfg: Config):
        self.provider = provider
        self.cfg = cfg
        self._taxonomy_paths = cfg.taxonomy_paths()
        self._json_retries = max(0, int(cfg.classify.get("json_retries", 1)))
        self._summary_max = int(cfg.classify.get("summary_max_chars", 100))
        self._input_max = int(cfg.classify.get("max_input_chars", 2000))
        self._batch_item_max = int(cfg.classify.get("batch_max_chars_per_item", 1200))
        self._batch_size = int(cfg.classify.get("batch_size", 10))
        self._batch_concurrency = int(cfg.classify.get("batch_concurrency", 2))
        self._vendor_floor = max(1, min(3, int(cfg.classify.get("vendor_importance_floor", 2))))

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

        def run_batch(batch):
            started = monotonic()
            result = self._classify_batch_once(batch, texts)
            return result, len(batch), monotonic() - started

        collected: dict[str, Classification] = {}
        logger.info("开始打标：{} 条，{} 批，并发 {}", len(rows), len(batches), workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_batch, batch) for batch in batches]
            for done, future in enumerate(as_completed(futures), start=1):
                part, count, seconds = future.result()
                collected.update(part)
                logger.info("打标批次进度 {}/{}：成功 {} / {}，待单条补偿 {}，耗时 {:.1f} 秒",
                            done, len(batches), sum(cls.ok for cls in part.values()), count,
                            count - len(part), seconds)

        out: list[Classification] = []
        retried = 0
        missing = len(rows) - len(collected)
        for row in rows:
            cls = collected.get(row["guid"])
            if cls is None:                      # 批次缺失 → 单条补偿
                retried += 1
                logger.info("单条补偿进度 {}/{}：{}", retried, missing, row["guid"])
                cls = self.classify_one(row, texts.get(row["guid"]))
            out.append(cls)

        ok = sum(1 for c in out if c.ok)
        rel = sum(1 for c in out if c.ok and c.relevant)
        vendor = sum(1 for c in out if c.ok and c.relevant
                     and any(is_vendor_tag(t) for t in c.tags))
        logger.info("打标完成：{} 条（成功 {} / 相关 {} / 厂商类 {} / 批次 {} 个 / 单条补偿 {}）",
                    len(out), ok, rel, vendor, len(batches), retried)
        return out

    def classify_one(self, row: dict, text: Optional[str]) -> Classification:
        guid = row["guid"]
        messages = self._build_single_messages(row, text)
        raw = None
        for attempt in range(self._json_retries + 1):
            try:
                raw = self.provider.chat(messages, json_mode=True, model=self.cfg.tagger_model)
            except Exception as exc:
                return Classification(guid=guid, ok=False,
                                      error=f"LLM 调用失败: {exc}", raw=str(exc))
            cls = self._parse_payload(guid, extract_json(raw), raw)
            if cls.ok:
                return cls
            logger.warning("[{}] 第 {} 次输出无效: {}", guid, attempt + 1, cls.error)
            messages = messages + [{
                "role": "user",
                "content": "请按要求重新输出一个 JSON 对象，确保字段、类型和标签有效。",
            }]
        return Classification(guid=guid, ok=False, error="模型多次返回无效打标结果", raw=raw)

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
            raw = self.provider.chat(messages, json_mode=True, model=self.cfg.tagger_model)
        except Exception as exc:
            return {r["guid"]: Classification(guid=r["guid"], ok=False,
                                               error=f"LLM 批次调用失败: {exc}") for r in batch}
        results = (extract_json(raw) or {}).get("results")
        if not isinstance(results, list):
            return {}
        valid_items = [
            item for item in results if isinstance(item, dict)
            and type(item.get("idx")) is int and 1 <= item["idx"] <= len(batch)
        ]
        counts = Counter(item["idx"] for item in valid_items)
        out: dict[str, Classification] = {}
        for item in valid_items:
            idx = item["idx"]
            if counts[idx] == 1:
                guid = batch[idx - 1]["guid"]
                cls = self._parse_payload(guid, item, raw)
                if cls.ok:
                    out[guid] = cls
        return out

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

    def _parse_payload(self, guid: str, data: dict | None, raw: str) -> Classification:
        invalid = Classification(guid=guid, ok=False, error="缺少字段或字段类型无效", raw=raw)
        if data is None:
            return invalid
        is_article = data.get("is_article")
        relevant = data.get("relevant")
        if type(is_article) is not bool or type(relevant) is not bool or (relevant and not is_article):
            return invalid

        pub = data.get("published_date")
        if pub is not None:
            try:
                parsed = date.fromisoformat(pub)
            except (ValueError, TypeError):
                return invalid
            if pub != parsed.isoformat() or normalize_date(parsed, tz_name=self.cfg.app.get("timezone")) is None:
                return invalid

        if not relevant:
            return Classification(guid=guid, is_article=is_article, published_date=pub,
                                  relevant=False, raw=raw)
        tags, keywords, companies = (data.get(key) for key in ("tags", "keywords", "companies"))
        if not all(isinstance(values, list) and all(isinstance(value, str) for value in values)
                   for values in (tags, keywords, companies)):
            return invalid
        tags = self._filter_tags(tags)
        summary = data.get("summary")
        importance = data.get("importance")
        if (not tags or not isinstance(summary, str) or not summary.strip()
                or type(importance) is not int or not 1 <= importance <= 3):
            return invalid

        if importance < self._vendor_floor and any(is_vendor_tag(t) for t in tags):
            importance = self._vendor_floor
            logger.debug("[{}] 厂商类新闻重要度提升至 {}", guid, importance)

        return Classification(
            guid=guid, is_article=is_article,
            published_date=pub,
            relevant=relevant, tags=tags, summary=summary.strip(),
            keywords=[x.strip() for x in keywords if x.strip()],
            companies=[x.strip() for x in companies if x.strip()], importance=importance,
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
