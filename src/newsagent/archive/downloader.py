"""网页下载与正文提取（trafilatura 优先，BeautifulSoup 兜底）。"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from loguru import logger
import trafilatura

from ..collect.base import Article, fetch
from ..utils.config import Config


@dataclass
class FetchedContent:
    """一次下载+提取的产物（尚未落盘）。"""

    html: str                      # 原始 HTML 文本（快照）
    text: str | None               # 提取的正文（可能为 None = 提取失败）
    extractor: str                 # trafilatura | bs4 | failed
    meta_title: str | None
    meta_date: str | None


_MAX_TEXT_CHARS = 200_000  # 防止异常页面产生超大文本


def _bs4_fallback(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "iframe", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        return text[:_MAX_TEXT_CHARS]
    except Exception:
        return ""


def extract(html: str) -> tuple[str | None, str, str | None, str | None]:
    """返回 (text, extractor, meta_title, meta_date)。提取失败时 text=None。"""
    # trafilatura：结构化输出 JSON 可同时拿到标题/日期元数据
    try:
        result = trafilatura.extract(
            html, output_format="json", with_metadata=True,
            include_comments=False, include_tables=False, favor_recall=True,
        )
        if result:
            data = json.loads(result)
            text = (data.get("text") or "").strip() or None
            return (text[:_MAX_TEXT_CHARS] if text else None), "trafilatura", \
                data.get("title"), data.get("date")
    except Exception as exc:
        logger.debug("trafilatura 提取异常: {}", exc)

    text = _bs4_fallback(html)
    if text:
        return text, "bs4", None, None
    return None, "failed", None, None


def download_and_extract(article: Article, cfg: Config,
                         client: httpx.Client | None = None) -> FetchedContent:
    """下载文章页并提取正文；网络失败抛 httpx.HTTPError（由调用方决定重试/跳过）。"""
    resp = fetch(article.url, cfg, client=client)
    html = resp.text
    text, extractor, meta_title, meta_date = extract(html)
    if text is None:
        logger.warning("[{}] 正文提取失败（保留快照）: {}", article.source_id, article.url)
    return FetchedContent(
        html=html,
        text=text,
        extractor=extractor,
        meta_title=meta_title,
        meta_date=meta_date,
    )
